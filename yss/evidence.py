"""Evidence checking: prove that what a doc claims about the repo is still true.

Claims come from two places:
  1. `evidence:` lists on a doc or any item:
       - {path: yss/build.py}                      file or folder exists (globs allowed)
       - {path: yss/build.py, contains: "def build("}  and contains the text
       - {glob: "yss/prefabs/*.yaml", min: 10}     at least `min` matches
       - {symbol: "yss.build:build"}               module attribute (parsed, then imported unless
                                                   evidence.import_symbols is off)
       - {command: "python -m yss validate", expect: 0}   exit code (only with --run-commands)
  2. schema annotations `x-evidence: path|glob|command|symbol|export` on fields such as codemap
     modules.path or design components.code, so common fields are checked for free. An `export`
     field is resolved against the nearest enclosing `path`, which is how every code map export
     is proved to still exist at the line the site links to (adr-024).

Git recency: if any path cited by a doc changed after the doc's `updated` date, the doc gets a
`warn` claim ("possibly stale"). Statuses: ok | stale | warn | unknown | skipped.

Policy (`git_recency`, `run_commands`, `import_symbols`) resolves per doc: a CLI flag wins, then
that doc's collection.yaml `evidence` block, then site.yaml `evidence`, then the defaults in
config.py. `import_symbols` (default true, adr-036) is what allows the symbol import fallback to
run project code; with it off an unresolvable symbol claim is `skipped`, not `stale`.
"""
from __future__ import annotations

import glob as globmod
import importlib
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .loader import SchemaRegistry
from .symbols import SymbolError, index_for, supported

STATUS_ORDER = {"stale": 0, "warn": 1, "unknown": 2, "skipped": 3, "ok": 4}


@dataclass
class Claim:
    doc: str
    item: str | None
    field: str
    kind: str
    target: str
    status: str = "unknown"
    detail: str = ""
    source: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceReport:
    claims: list[Claim] = field(default_factory=list)

    def by_doc(self) -> dict[str, list[Claim]]:
        out: dict[str, list[Claim]] = {}
        for c in self.claims:
            out.setdefault(c.doc, []).append(c)
        return out

    def summary(self) -> dict[str, dict]:
        """{doc_id: {status, counts, items: {item_id: status}}}"""
        out: dict[str, dict] = {}
        for doc_id, claims in self.by_doc().items():
            counts: dict[str, int] = {}
            items: dict[str, str] = {}
            for c in claims:
                counts[c.status] = counts.get(c.status, 0) + 1
                if c.item:
                    prev = items.get(c.item)
                    if prev is None or STATUS_ORDER[c.status] < STATUS_ORDER[prev]:
                        items[c.item] = c.status
            worst = min((c.status for c in claims), key=lambda s: STATUS_ORDER[s]) if claims else "ok"
            out[doc_id] = {"status": worst, "counts": counts, "items": items, "claims": len(claims)}
        return out

    @property
    def stale(self) -> list[Claim]:
        return [c for c in self.claims if c.status == "stale"]

    @property
    def warnings(self) -> list[Claim]:
        return [c for c in self.claims if c.status == "warn"]


# --- collecting -------------------------------------------------------------
def _explicit(entry: Any) -> tuple[str, str, dict] | None:
    if not isinstance(entry, dict):
        return None
    if "path" in entry:
        return ("contains" if "contains" in entry else "path", str(entry["path"]), entry)
    if "glob" in entry:
        return ("glob", str(entry["glob"]), entry)
    if "symbol" in entry:
        return ("symbol", str(entry["symbol"]), entry)
    if "command" in entry:
        return ("command", str(entry["command"]), entry)
    return None


def collect_claims(docs: dict[str, dict], reg: SchemaRegistry) -> list[Claim]:
    claims: list[Claim] = []
    for doc_id, doc in docs.items():
        ann = reg.annotations(f"doc.{doc.get('kind')}")["evidence"]
        source = doc.get("_source", doc_id)

        def walk(value: Any, path: str, item_id: str | None, base: str | None) -> None:
            if isinstance(value, dict):
                current = value.get("id") if isinstance(value.get("id"), str) and path else item_id
                # The nearest enclosing `path` travels down with the walk so a field annotated
                # `x-evidence: export` knows which module it belongs to (a code map export).
                own = value.get("path")
                here_base = own.strip() if isinstance(own, str) and own.strip() else base
                for key, sub in value.items():
                    if isinstance(key, str) and key.startswith("_"):
                        continue
                    here = f"{path}/{key}" if path else key
                    if key == "evidence" and isinstance(sub, list):
                        for i, entry in enumerate(sub):
                            parsed = _explicit(entry)
                            if parsed:
                                kind, target, extra = parsed
                                claims.append(Claim(doc_id, current, f"{here}/{i}", kind, target, source=source, detail=_extra_detail(extra)))
                    elif key in ann and sub is not None:
                        values = sub if isinstance(sub, list) else [sub]
                        for i, v in enumerate(values):
                            if isinstance(v, str) and v.strip():
                                loc = f"{here}/{i}" if isinstance(sub, list) else here
                                target = v.strip()
                                if ann[key] == "export":
                                    # Annotations are per field name across a whole schema, so the
                                    # same `name` appears on entrypoints too. An export is only an
                                    # export when something above it says which module it lives in.
                                    if not here_base:
                                        continue
                                    target = f"{here_base}::{target}"
                                claims.append(Claim(doc_id, current, loc, ann[key], target, source=source))
                    walk(sub, here, current, here_base)
            elif isinstance(value, list):
                for i, entry in enumerate(value):
                    walk(entry, f"{path}/{i}", item_id, base)

        walk(doc, "", None, None)
    return claims


def _extra_detail(extra: dict) -> str:
    bits = []
    if "contains" in extra:
        bits.append(f"contains={extra['contains']!r}")
    if "min" in extra:
        bits.append(f"min={extra['min']}")
    if "expect" in extra:
        bits.append(f"expect={extra['expect']}")
    return " ".join(bits)


# --- evaluating -------------------------------------------------------------
def _candidates(cfg: Config, doc: dict, target: str) -> list[Path]:
    roots = [cfg.root]
    cid = doc.get("_collection")
    if cid:
        try:
            roots.insert(0, cfg.collection(cid).root)
        except Exception:  # noqa: BLE001
            pass
    return [r / target for r in roots]


def _check_path(cfg: Config, doc: dict, claim: Claim, extra: dict) -> None:
    for candidate in _candidates(cfg, doc, claim.target):
        matches = globmod.glob(str(candidate)) if any(ch in claim.target for ch in "*?[") else ([str(candidate)] if candidate.exists() else [])
        if matches:
            if claim.kind == "contains":
                needle = str(extra.get("contains", ""))
                for m in matches:
                    try:
                        if needle in Path(m).read_text(encoding="utf-8", errors="ignore"):
                            claim.status = "ok"
                            return
                    except OSError:
                        continue
                claim.status = "stale"
                claim.detail = f"{claim.target} exists but does not contain {needle!r}"
                return
            claim.status = "ok"
            return
    claim.status = "stale"
    claim.detail = f"path not found: {claim.target}"


def _check_glob(cfg: Config, doc: dict, claim: Claim, extra: dict) -> None:
    minimum = int(extra.get("min", 1))
    for candidate in _candidates(cfg, doc, claim.target):
        matches = globmod.glob(str(candidate), recursive=True)
        if len(matches) >= minimum:
            claim.status = "ok"
            claim.detail = f"{len(matches)} match(es)"
            return
    claim.status = "stale"
    claim.detail = f"fewer than {minimum} match(es) for {claim.target}"


def _module_to_rel(root: Path, module_name: str) -> str | None:
    """`yss.config` -> `yss/config.py`; `yss.providers` -> `yss/providers`. None when neither exists."""
    rel = module_name.replace(".", "/")
    if (root / f"{rel}.py").is_file():
        return f"{rel}.py"
    if (root / rel).is_dir():
        return rel
    return None


def _check_export(cfg: Config, claim: Claim) -> None:
    """An `x-evidence: export` claim: `<path>::<name>`, resolved by parsing that module.

    Reports the line it resolved to, which is the same number the code map's reader links to -
    so a stale claim here is exactly the case where a published deep link would be wrong.
    """
    rel, _, name = claim.target.rpartition("::")
    if not supported(rel):
        claim.status = "skipped"
        claim.detail = f"{rel} is not parsed for exports"
        return
    try:
        index = index_for(cfg.root, rel)
    except SymbolError as exc:
        claim.status = "stale"
        claim.detail = f"cannot parse {exc.rel}: {exc.reason}"
        return
    span = index.get(name)
    if span:
        claim.status = "ok"
        claim.detail = f"{span[2]}:{span[0]}-{span[1]}"
        return
    claim.status = "stale"
    claim.detail = f"{rel} does not define {name}"


def _check_symbol(cfg: Config, claim: Claim, allow_import: bool = True) -> None:
    module_name, _, attr = claim.target.partition(":")
    # Parse first: importing runs module-level code, and `hasattr` cannot answer for a dotted
    # member like `Config.evidence_for` or for a constant. Import remains the fallback so a name
    # that only exists at runtime (a re-export, a generated attribute) still checks out - but it
    # is the one proof step that executes the thing it proves, so `evidence.import_symbols` can
    # turn it off and get a read-only check instead (adr-036).
    rel = _module_to_rel(cfg.root, module_name)
    if rel is not None and attr:
        try:
            span = index_for(cfg.root, rel).get(attr)
        except SymbolError:
            span = None
        if span:
            claim.status = "ok"
            claim.detail = f"{span[2]}:{span[0]}-{span[1]}"
            return
    if not allow_import:
        claim.status = "skipped"
        claim.detail = f"parsing did not resolve {claim.target}; import fallback off (evidence.import_symbols)"
        return
    root = str(cfg.root)
    inserted = root not in sys.path
    if inserted:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        claim.status = "stale"
        claim.detail = f"cannot import {module_name}: {type(exc).__name__}: {exc}"
        return
    finally:
        # A proof step leaves the interpreter as it found it; before adr-036 this entry
        # accumulated once per check in a long-lived process such as `yss serve`.
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
    if attr and not hasattr(module, attr):
        claim.status = "stale"
        claim.detail = f"{module_name} has no attribute {attr}"
        return
    claim.status = "ok"


def _check_command(cfg: Config, claim: Claim, extra: dict, run: bool) -> None:
    if not run:
        claim.status = "skipped"
        claim.detail = "commands run only with --run-commands"
        return
    expect = int(extra.get("expect", 0))
    try:
        proc = subprocess.run(claim.target, shell=True, cwd=cfg.root, capture_output=True, text=True, timeout=int(extra.get("timeout", 300)))
    except subprocess.TimeoutExpired:
        claim.status = "stale"
        claim.detail = "timed out"
        return
    if proc.returncode == expect:
        claim.status = "ok"
    else:
        claim.status = "stale"
        claim.detail = f"exit {proc.returncode} (expected {expect}): {(proc.stderr or proc.stdout).strip()[-300:]}"


def _git_last_change(cfg: Config, paths: list[str]) -> str | None:
    if not paths:
        return None
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *paths],
            cwd=cfg.root, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _policy(cfg: Config, doc: dict, name: str, override: bool | None, default: bool) -> bool:
    """Resolve one evidence setting: CLI override wins, then collection.yaml, then site.yaml."""
    if override is not None:
        return bool(override)
    return bool(cfg.evidence_for(doc.get("_collection") or "").get(name, default))


def evaluate(cfg: Config, docs: dict[str, dict], claims: list[Claim],
             run_commands: bool | None = None, git_recency: bool | None = None,
             import_symbols: bool | None = None) -> EvidenceReport:
    extras: dict[int, dict] = {}
    for claim in claims:
        doc = docs.get(claim.doc, {})
        extra = _explicit_extra(doc, claim)
        extras[id(claim)] = extra
        if claim.kind in ("path", "contains"):
            _check_path(cfg, doc, claim, extra)
        elif claim.kind == "glob":
            _check_glob(cfg, doc, claim, extra)
        elif claim.kind == "symbol":
            _check_symbol(cfg, claim, _policy(cfg, doc, "import_symbols", import_symbols, True))
        elif claim.kind == "export":
            _check_export(cfg, claim)
        elif claim.kind == "command":
            _check_command(cfg, claim, extra, _policy(cfg, doc, "run_commands", run_commands, False))
        else:
            claim.status = "unknown"
            claim.detail = f"unknown evidence kind '{claim.kind}'"
    report = EvidenceReport(list(claims))
    for doc_id, doc in docs.items():
        updated = doc.get("updated")
        if not updated or not _policy(cfg, doc, "git_recency", git_recency, True):
            continue
        cited = sorted({c.target for c in claims if c.doc == doc_id and c.kind in ("path", "contains") and c.status == "ok"})
        cited = [p for p in cited if not any(ch in p for ch in "*?[")]
        last = _git_last_change(cfg, cited)
        if last and str(last) > str(updated):
            report.claims.append(
                Claim(doc_id, None, "updated", "git", ", ".join(cited[:5]) + (" ..." if len(cited) > 5 else ""),
                      status="warn", detail=f"cited code changed {last}, doc updated {updated}", source=doc.get("_source", ""))
            )
    return report


def _explicit_extra(doc: dict, claim: Claim) -> dict:
    """Recover the {contains, min, expect} options for an explicit evidence claim from its field path."""
    if "/evidence/" not in f"/{claim.field}":
        return {}
    cur: Any = doc
    for part in claim.field.split("/"):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return {}
    return cur if isinstance(cur, dict) else {}


def check(cfg: Config, docs: dict[str, dict], reg: SchemaRegistry, run_commands: bool | None = None,
          git_recency: bool | None = None, import_symbols: bool | None = None) -> EvidenceReport:
    """Evaluate every claim. `run_commands`/`git_recency`/`import_symbols` None means "use the
    configured policy", which is site.yaml `evidence` overridden by each doc's collection.yaml."""
    claims = collect_claims(docs, reg)
    return evaluate(cfg, docs, claims, run_commands=run_commands, git_recency=git_recency,
                    import_symbols=import_symbols)


def inject(docs: dict[str, dict], report: EvidenceReport) -> None:
    """Attach `_evidence` summaries to docs and to items with ids so prefabs can show freshness."""
    summary = report.summary()
    for doc_id, doc in docs.items():
        info = summary.get(doc_id, {"status": "ok", "counts": {}, "items": {}, "claims": 0})
        doc["_evidence"] = {"status": info["status"], "counts": info["counts"], "claims": info["claims"]}
        if not info["items"]:
            continue

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                item_id = value.get("id")
                if isinstance(item_id, str) and item_id in info["items"] and value is not doc:
                    value["_evidence"] = {"status": info["items"][item_id]}
                for sub in value.values():
                    walk(sub)
            elif isinstance(value, list):
                for entry in value:
                    walk(entry)

        walk(doc)


def format_report(report: EvidenceReport, verbose: bool = False) -> str:
    lines = []
    for doc_id, claims in sorted(report.by_doc().items()):
        worst = min((c.status for c in claims), key=lambda s: STATUS_ORDER[s])
        counts = {}
        for c in claims:
            counts[c.status] = counts.get(c.status, 0) + 1
        lines.append(f"{doc_id:24s} {worst:8s} " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for c in claims:
            if c.status in ("stale", "warn") or verbose:
                where = f"{c.source}: at {c.field}" if c.source else c.field
                lines.append(f"    {c.status:8s} {c.kind:9s} {c.target}  ({where}) {c.detail}".rstrip())
    if not lines:
        lines.append("no evidence claims found (add `evidence:` lists or x-evidence annotations)")
    return "\n".join(lines)
