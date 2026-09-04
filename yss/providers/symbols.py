"""Symbol index for the code map: which lines each cited export occupies.

Two sources come from this module, because the two targets have different sources of truth
for the source text itself (adr-024):

  `collect`        the index alone - path, export, [start, end]. Cheap, both targets. The
                   public reader pairs it with a commit-pinned fetch from the hosting service.
  `collect_source` the same ranges plus the lines themselves, private target only, so the
                   private site reads its own working tree and needs no network.

Both honour `spec['_target']`: a code map module marked `visibility: private` must not appear
in a public index, and a name is all it would take to leak one.
"""
from __future__ import annotations

from ..symbols import SymbolError, index_for, supported
from ..visibility import filter_for_target


def _bases(cfg, doc: dict) -> list:
    """Where a doc's cited paths resolve from: its own collection first, then the repo root.

    The same order `yss check` uses for path evidence, so the index agrees with the checker
    about what a module path means.
    """
    bases = [cfg.root]
    cid = doc.get("_collection")
    if cid:
        try:
            bases.insert(0, cfg.collection(cid).root)
        except Exception:  # noqa: BLE001 - an unknown collection just falls back to the repo root
            pass
    return bases


def _codemap_modules(cfg, target: str) -> list[tuple[dict, object, str]]:
    """(module, base, repo-relative path) for every code map module the target may see."""
    from ..build import load_all

    loaded = load_all(cfg)
    docs = filter_for_target(loaded.docs, target) if target else loaded.docs
    modules = []
    for doc in docs.values():
        if doc.get("kind") != "codemap":
            continue
        for module in doc.get("modules") or []:
            path = (module.get("path") or "").strip()
            if not path:
                continue
            for base in _bases(cfg, doc):
                if (base / path).exists():
                    break
            else:
                base = cfg.root
            try:
                rel = (base / path).resolve().relative_to(cfg.root.resolve()).as_posix()
            except ValueError:
                rel = path  # outside the checkout: keep the doc's own spelling, never an absolute path
            modules.append((module, base, rel))
    return modules


def _relative(cfg, base, rel: str) -> str:
    """A path the site can link to: repo-relative, and never absolute (adr-004)."""
    try:
        return (base / rel).resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        return rel


def _index(cfg, target: str) -> dict:
    """{path: {export: [start, end, file]}} plus what could not be resolved, for visible modules.

    `file` is where the export is actually defined, which differs from the code map's own path
    whenever a module entry names a package directory - a link has to point at the file.
    """
    by_path: dict[str, dict[str, list]] = {}
    unresolved: list[dict] = []
    errors: list[dict] = []
    unsupported: list[str] = []

    for module, base, path in _codemap_modules(cfg, target):
        exports = [e for e in (module.get("exports") or []) if e.get("name")]
        if not supported(path):
            if exports:
                unsupported.append(path)
            continue
        try:
            available = index_for(base, module["path"].strip())
        except SymbolError as exc:
            # Relative path and exception name only: this string is written into the build output,
            # which the public target scans for the checkout path (adr-004).
            errors.append({"path": path, "error": exc.reason})
            continue
        found: dict[str, list] = {}
        for export in exports:
            name = export["name"]
            span = available.get(name)
            if span:
                found[name] = [span[0], span[1], _relative(cfg, base, span[2])]
            else:
                unresolved.append({"path": path, "export": name})
        if found:
            by_path[path] = found

    return {"modules": by_path, "unresolved": unresolved, "errors": errors, "unsupported": unsupported}


def collect(cfg, spec):
    """The index on its own - safe for the public target, which fetches the text elsewhere."""
    return _index(cfg, spec.get("_target") or "")


def collect_source(cfg, spec):
    """The index plus the source lines, for a target that may read the working tree.

    Declared with `targets: [private]`. Guarded anyway: a misconfiguration that enabled this on a
    redacting target would put source text into the public output, so it refuses rather than obeys.
    """
    target = spec.get("_target") or ""
    if target and cfg.targets.get(target, {}).get("redact"):
        raise RuntimeError(f"source text is not available to redacting target '{target}'")

    result = _index(cfg, target)
    max_lines = int(spec.get("max_lines", 400))
    # Keyed by the defining file, not by the code map's own path: a module entry may spell a
    # package directory with a trailing slash that the resolved path does not carry, and the
    # reader already knows which file each export came from.
    text: dict[str, dict[str, dict]] = {}
    cache: dict[str, list[str]] = {}
    for exports in result["modules"].values():
        for name, span in exports.items():
            start, end, file = span[0], span[1], span[2]
            if file not in cache:
                try:
                    cache[file] = (cfg.root / file).read_text(encoding="utf-8").splitlines()
                except OSError as exc:
                    result["errors"].append({"path": file, "error": type(exc).__name__})
                    cache[file] = []
            if not cache[file]:
                continue
            end = min(end, start + max_lines - 1)
            text.setdefault(file, {})[name] = {
                "start": start,
                "end": end,
                "file": file,
                "lines": cache[file][start - 1:end],
            }
    result["text"] = text
    return result
