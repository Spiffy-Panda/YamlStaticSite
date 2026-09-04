"""Build orchestration: validate -> check refs -> filter for target -> evidence -> render -> export -> scan."""
from __future__ import annotations

import html as htmlmod
import json
import os
import posixpath
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import PKG_DIR, Config
from .dynamic import write_all
from .evidence import EvidenceReport, check as evidence_check, inject as evidence_inject
from .hooks import HookError, call, load_hooks
from .loader import SchemaRegistry, check_refs, load_collection_configs, load_docs, load_pages, load_prefabs, resolve_doc_id
from .render import ANCHOR_RE, Renderer, RenderError, route_to_path
from .visibility import filter_for_target, is_visible, scan_tree


class BuildError(Exception):
    pass


def git_commit(root: Path) -> dict:
    """The commit this build represents, and whether the tree had uncommitted changes.

    Anything that deep-links into a hosting service by line number has to name a commit: line 191
    is only true of one revision, so a link pinned to a branch drifts silently (adr-024). CI is
    authoritative via GITHUB_SHA; locally we ask git and report `dirty` so callers can decline to
    pin. Never raises - a build outside a checkout simply has no commit.
    """
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return {"commit": env_sha, "commit_short": env_sha[:7], "dirty": False}
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
        if rev.returncode != 0:
            return {"commit": None, "commit_short": None, "dirty": False}
        sha = rev.stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10)
        dirty = bool(status.returncode == 0 and status.stdout.strip())
        return {"commit": sha, "commit_short": sha[:7], "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "commit_short": None, "dirty": False}


@dataclass
class Loaded:
    registry: SchemaRegistry
    docs: dict
    pages: list
    prefabs: dict
    errors: list = field(default_factory=list)


@dataclass
class BuildReport:
    target: str
    out_dir: Path
    pages: list = field(default_factory=list)
    docs: list = field(default_factory=list)
    dynamic: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    evidence: EvidenceReport | None = None
    out_label: str = ""

    def summary(self) -> str:
        stale = len(self.evidence.stale) if self.evidence else 0
        return (
            f"[{self.target}] {len(self.pages)} pages, {len(self.docs)} docs, "
            f"{len(self.dynamic)} dynamic sources, {stale} stale claims -> {self.out_label or self.out_dir}"
        )


def load_all(cfg: Config) -> Loaded:
    registry = SchemaRegistry(cfg.schema_dirs())
    errors: list[str] = []
    if cfg.raw and "site" in registry.schemas:
        errors += registry.validate(cfg.raw, "site", cfg.source.name if cfg.source else "site.yaml")
    errors += load_collection_configs(cfg, registry)
    docs, e1 = load_docs(cfg, registry)
    pages, e2 = load_pages(cfg, registry)
    prefabs, e3 = load_prefabs(cfg, registry)
    errors += e1 + e2 + e3
    if not errors:
        errors += check_refs(docs, registry, pages)
        errors += cross_checks(cfg, docs, pages, prefabs)
    return Loaded(registry, docs, pages, prefabs, errors)


def cross_checks(cfg: Config, docs: dict, pages: list, prefabs: dict) -> list[str]:
    errors = []
    sources = cfg.dynamic_sources
    for page in pages:
        where = page.get("_source", page["id"])
        cid = page.get("_collection")
        for doc_ref in page.get("docs") or []:
            if not resolve_doc_id(doc_ref, cid, docs):
                errors.append(f"{where}: docs lists unknown doc id '{doc_ref}'")
        for index, sec in enumerate(page.get("sections") or []):
            sid = sec.get("id") or f"section-{index + 1}"
            if sec["type"] == "prefab" and sec.get("prefab") not in prefabs:
                errors.append(f"{where}: section '{sid}': unknown prefab '{sec.get('prefab')}'")
            if sec["type"] == "dynamic":
                name = sec.get("source")
                if name not in sources and not (cid and f"{cid}.{name}" in sources):
                    errors.append(f"{where}: section '{sid}': unknown dynamic source '{name}'")
    return errors


def _safe_clear(out_dir: Path, cfg: Config) -> None:
    out_dir = out_dir.resolve()
    if out_dir == cfg.root or out_dir in cfg.root.parents:
        raise BuildError(f"refusing to clear {out_dir}: it contains the site root")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def _copy_tree(src: Path, dst: Path) -> None:
    if src and src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _glob_paths(root: Path, pattern: str) -> set[Path]:
    """Paths under root matching pattern, relative to root. A trailing '/' names a directory and
    everything beneath it (so `exclude: [prototype/]` drops a whole subtree, not just its name)."""
    pattern = (pattern or "").strip().lstrip("/")
    if not pattern:
        return set()
    if pattern.endswith("/"):
        base = pattern.rstrip("/")
        dirs = list(root.glob(base)) if base else [root]
        found: set[Path] = set()
        for d in dirs:
            if d.is_dir():
                found.add(d)
                found |= set(d.rglob("*"))
        return found
    return set(root.glob(pattern))


def _mount_files(src: Path, include: list[str], exclude: list[str]) -> list[Path]:
    """Files to copy from src: no `include` means everything (today's behaviour); `include`
    restricts to matches (non-recursive `*.html` vs. recursive `**/*.html` are distinguishable);
    `exclude` then removes matches from that set, winning over `include`."""
    if include:
        matched: set[Path] = set()
        for pattern in include:
            matched |= {p for p in _glob_paths(src, pattern) if p.is_file()}
    else:
        matched = {p for p in src.rglob("*") if p.is_file()}
    if exclude:
        excluded: set[Path] = set()
        for pattern in exclude:
            excluded |= _glob_paths(src, pattern)
        matched = {p for p in matched if not any(p == e or e in p.parents for e in excluded)}
    return sorted(matched)


def _copy_filtered(src: Path, dst: Path, include: list[str], exclude: list[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for f in _mount_files(src, include, exclude):
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def _mount(cfg: Config, out: Path, base: Path, spec: dict, target: str, prefix: str, warnings: list[str]) -> None:
    targets = spec.get("targets") or ["private"]
    if target not in targets:
        return
    src = (base / spec["path"]).resolve()
    at = "/".join(p for p in ((prefix or "").strip("/"), spec["at"].strip("/")) if p)
    dst = (out / at).resolve()
    if dst != out and out not in dst.parents:
        raise BuildError(f"mount '{spec['at']}' escapes the output directory")
    # A mount source may leave its collection and may not leave the site (adr-032, gh-22). Leaving
    # the collection is a supported affordance, not an accident: during a migration a placeholder
    # collection mounts the legacy generator's output so the dead-link gate sees a complete tree.
    # The site root is the boundary because a mount is a copy instruction whose bytes end up
    # published, and `include` sections already draw the line in exactly this place - the check
    # below is worded to match `render.py`'s. This was previously enforced by nothing at all, which
    # meant any folder on the machine was mountable and a later symmetry fix would have broken
    # every consumer relying on the escape with no deprecation window.
    try:
        src.relative_to(cfg.root.resolve())
    except ValueError as exc:
        raise BuildError(
            f"mount '{spec['path']}' escapes the site root: {src}\n"
            f"  a mount source may sit outside its collection but not outside the site"
        ) from exc
    if not src.is_dir():
        warnings.append(f"mount {spec['path']} -> /{at}/: source folder missing, skipped")
        return
    include = spec.get("include") or []
    exclude = spec.get("exclude") or []
    if include or exclude:
        _copy_filtered(src, dst, include, exclude)
    else:
        _copy_tree(src, dst)


LINK_RE = re.compile(r'\b(?:href|src)="([^"]*)"')
LINK_SKIP_PREFIXES = ("http://", "https://", "//", "mailto:", "data:", "javascript:", "#")


def _dead_links(rendered: list[tuple[str, str]], out: Path, base_url: str) -> list[str]:
    """Every local href/src a rendered page emits that the output does not actually carry (gh-14).

    `dead_refs` proves an *anchor* exists; nothing proved the *file* did, so a collection whose
    stylesheet was emitted at the wrong path 404'd through validate, check, scan and build all
    green. This asks the same question of a page's links that `dead_refs` asks of its references.

    Rendered pages only, never mounted trees: a mount's contents are the collection's own business
    (adr-021), and hand-authored links inside one are not ours to police. A link *into* a mount is
    checked like any other, because the page emitting it is ours.
    """
    base = base_url if base_url.endswith("/") else base_url + "/"
    seen: set[tuple[str, str]] = set()
    dead: list[str] = []

    def report(route: str, href: str, detail: str) -> None:
        key = (route, href)
        if key in seen:
            return
        seen.add(key)
        dead.append(f'dead link on page {route}: href="{href}" -> {detail}')

    for route, html in rendered:
        page_dir = route.strip("/")
        for raw in LINK_RE.findall(html):
            href = htmlmod.unescape(raw).strip()
            if not href or href.startswith(LINK_SKIP_PREFIXES):
                continue
            path = href.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue  # a bare query or fragment on the current page
            if path.startswith("/"):
                if not path.startswith(base):
                    report(route, href, f"{path} is outside base_url {base}")
                    continue
                rel = path[len(base) :]
            else:
                rel = posixpath.normpath(posixpath.join(page_dir, path) if page_dir else path)
                if rel in (".", "/"):
                    rel = ""
                if rel == ".." or rel.startswith("../"):
                    report(route, href, f"{rel} is outside the output")
                    continue
            target = (out / rel) if rel else out
            if target.is_file() or (target.is_dir() and (target / "index.html").is_file()):
                continue
            report(route, href, f"{rel or '/'} is not in the output")
    return sorted(dead)


LOCK_STALE_SECONDS = 600


def _lock_path(out: Path) -> Path:
    """The advisory build lock for an output directory: `dist/.public.build-lock`.

    Deliberately a *sibling* of the output, not a file inside it: `_safe_clear` deletes the
    directory wholesale, and nothing that reaches `dist/<target>/` may exist that the Pages
    artefact would then carry.
    """
    return out.parent / f".{out.name}.build-lock"


def _lock_age(lock: Path) -> tuple[float | None, dict]:
    """Seconds since the lock was taken, and whatever it says about its owner.

    Age is the *larger* of the file mtime age and the age of the recorded `started_at`, so a
    lock is only fresh when both agree it is.
    """
    try:
        mtime = lock.stat().st_mtime
    except OSError:
        return None, {}
    info: dict = {}
    try:
        info = json.loads(lock.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        info = {}
    now = datetime.now(timezone.utc)
    ages = [max(0.0, now.timestamp() - mtime)]
    started = info.get("started_at") if isinstance(info, dict) else None
    if isinstance(started, str):
        try:
            stamp = datetime.fromisoformat(started)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            ages.append(max(0.0, (now - stamp).total_seconds()))
        except ValueError:
            pass
    return max(ages), (info if isinstance(info, dict) else {})


def _acquire_lock(out: Path, target: str) -> tuple[Path, list[str]]:
    """Take the advisory lock for `out`, or refuse the build (gh-19).

    Two builders on one `dist/<target>/` race silently: the second one's `_safe_clear` deletes
    the first one's output while it is still writing into it, and the first still prints its
    success line. The lock makes the collision loud and early instead.

    A lock older than `LOCK_STALE_SECONDS` is assumed abandoned and replaced with a warning. We
    never liveness-check the recorded pid: `os.kill(pid, 0)` *terminates* the process on Windows.
    """
    lock = _lock_path(out)
    warnings: list[str] = []
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "target": target,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            return lock, warnings
        age, info = _lock_age(lock)
        if age is None:
            continue  # it vanished between the open and the stat; try again
        if age <= LOCK_STALE_SECONDS:
            raise BuildError(
                f"another build owns {out} (lock {lock}, pid {info.get('pid')}, "
                f"started {info.get('started_at')}); wait for it or delete the lock if that process is gone"
            )
        warnings.append(
            f"replaced a stale build lock {lock} (pid {info.get('pid')}, started {info.get('started_at')}, "
            f"{int(age)}s old, stale after {LOCK_STALE_SECONDS}s)"
        )
        try:
            lock.unlink()
        except OSError:
            pass
    raise BuildError(f"could not take the build lock {lock}; delete it if no build is running")


def _release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def output_ok(report: "BuildReport") -> bool:
    """Did the build's own manifest survive? `BuildReport.summary()` is composed from in-memory
    counts and never stats the filesystem, so a concurrent builder can clear `out` between the
    last write and the return and the summary still claims success (gh-19)."""
    return (report.out_dir / "build.json").is_file()


def missing_output_message(report: "BuildReport") -> str:
    label = report.out_label or report.out_dir
    return (
        f"[{report.target}] build reported success but {label}/build.json is missing"
        " - another process (a watching yss serve?) probably cleared the output"
    )


def build(
    cfg: Config,
    target: str,
    out_dir: Path | None = None,
    strict: bool | None = None,
    run_dynamic: bool = True,
    loaded: Loaded | None = None,
    check_evidence: bool = True,
) -> BuildReport:
    cfg.target(target)  # raises on unknown target
    if strict is None:  # --strict/--no-strict override site.yaml build.strict
        strict = bool(cfg.build.get("strict", False))
    loaded = loaded or load_all(cfg)
    if loaded.errors:
        raise BuildError("validation failed:\n  " + "\n  ".join(loaded.errors))

    hidden_collections = {c.id for c in cfg.collections() if target == "public" and c.visibility == "private"}
    docs_t = {
        doc_id: filter_for_target(doc, target)
        for doc_id, doc in loaded.docs.items()
        if is_visible(doc, target) and doc.get("_collection", "") not in hidden_collections
    }
    pages_t = [
        filter_for_target(page, target)
        for page in loaded.pages
        if is_visible(page, target) and page.get("_collection", "") not in hidden_collections
    ]
    out = (out_dir or cfg.out_dir(target)).resolve()
    lock, lock_warnings = _acquire_lock(out, target)
    try:
        _safe_clear(out, cfg)
        report = BuildReport(target=target, out_dir=out)
        try:
            report.out_label = out.relative_to(cfg.root).as_posix()
        except ValueError:
            report.out_label = out.name
        report.warnings += lock_warnings

        # evidence (paths, globs, symbols, git recency; commands only when configured)
        evidence_rows: list[dict] = []
        if check_evidence:
            report.evidence = evidence_check(cfg, docs_t, loaded.registry, run_commands=False)
            evidence_inject(docs_t, report.evidence)
            evidence_rows = [c.as_dict() for c in report.evidence.claims]
            for claim in report.evidence.stale:
                report.warnings.append(f"stale: {claim.doc} {claim.field} -> {claim.target}: {claim.detail}")

        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        build_info = {
            "target": target,
            "built_at": built_at,
            "version": __version__,
            "site": cfg.site.get("name"),
            "base_url": cfg.base_url(target),
            "repo": cfg.site.get("repo"),
            **git_commit(cfg.root),
        }
        renderer = Renderer(cfg, target, docs_t, pages_t, loaded.prefabs, list(loaded.docs), build_info, evidence_rows)

        # hooks: before_render
        for collection in cfg.collections():
            if collection.id in hidden_collections:
                continue
            try:
                call(load_hooks(collection.hooks_path, cfg.root), "before_render", cfg, target, collection.summary())
            except HookError as exc:
                raise BuildError(str(exc)) from exc

        # Strict failures are collected, not raised on the spot (gh-21). Two of these gates fire
        # before the assets, the data export, the mounts and - crucially - the leak scan, so
        # raising early would leave behind a partial tree that `scan_tree` never looked at. The
        # build therefore runs to completion, the leak scan keeps its own containment `rmtree`,
        # and everything strict has to say is reported together at the end over an output tree
        # that is whole and scanned. Nothing hazardous survives a failure; a wrong page does.
        strict_failures: list[str] = []

        # pages
        duplicate_ids: list[str] = []
        rendered: list[tuple[str, str]] = []  # (route, html) for the dead-link gate below
        for page in pages_t:
            try:
                html = renderer.render_page(page)
            except RenderError as exc:
                raise BuildError(str(exc)) from exc
            path = route_to_path(out, page["route"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            report.pages.append(page["route"])
            rendered.append((page["route"], html))
            # duplicate anchors: two elements on one page claiming the same id (gh-12). `dead_refs`
            # cannot see this - it asks whether an anchor is *present*, a set membership test - so a
            # prefab that derives an id from a label happily emits it once per bucket and every
            # [[doc#item]] link into the page silently lands on whichever comes first.
            counts = Counter(a for a in ANCHOR_RE.findall(html) if a)
            duplicate_ids += [
                f"duplicate anchor id '{anchor}' on page {page['route']} ({n} times)"
                for anchor, n in sorted(counts.items())
                if n > 1
            ]
        report.warnings += duplicate_ids
        if strict and duplicate_ids:
            strict_failures.append(
                f"{len(duplicate_ids)} duplicate anchor id(s).\n  " + "\n  ".join(duplicate_ids)
            )

        # binding warnings the renderer collected while filling the pages (gh-12)
        report.warnings += renderer.warnings

        # dead references: an anchor a rendered [[doc#item]] link points at that no page emits (gh-11)
        dead = renderer.dead_refs()
        report.warnings += dead
        if strict and dead:
            strict_failures.append(f"{len(dead)} dead reference(s).\n  " + "\n  ".join(dead))

        # data export (agent readable, and available to client-side JS)
        data_dir = out / "data"
        (data_dir / "docs").mkdir(parents=True, exist_ok=True)
        index = []
        for doc_id, doc in docs_t.items():
            path = data_dir / "docs" / f"{doc_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
            index.append(
                {
                    "id": doc_id,
                    "kind": doc.get("kind"),
                    "title": doc.get("title"),
                    "summary": doc.get("summary"),
                    "status": doc.get("status"),
                    "updated": doc.get("updated"),
                    "tags": doc.get("tags") or [],
                    "collection": doc.get("_collection", ""),
                    "evidence": (doc.get("_evidence") or {}).get("status", "ok"),
                    "page": renderer.doc_url(doc_id, exact=True) or None,
                    "json": renderer.url(f"data/docs/{doc_id}.json"),
                    "source": doc.get("_source"),
                }
            )
            report.docs.append(doc_id)
        (data_dir / "docs.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        (data_dir / "pages.json").write_text(
            json.dumps(
                [
                    {"id": p["id"], "route": p["route"], "url": renderer.url(p["route"]), "title": p["title"], "summary": p.get("summary"), "docs": p.get("docs") or [], "collection": p.get("_collection", "")}
                    for p in pages_t
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        (data_dir / "collections.json").write_text(json.dumps(renderer.ctx["collections"], indent=2, default=str), encoding="utf-8")
        (data_dir / "evidence.json").write_text(json.dumps(evidence_rows, indent=2), encoding="utf-8")
        (data_dir / "site.json").write_text(json.dumps({"site": cfg.site, **build_info}, indent=2), encoding="utf-8")

        # schemas (so agents and the site can read them from the output too)
        schemas_dir = out / "schemas"
        schemas_dir.mkdir(exist_ok=True)
        for name, schema in loaded.registry.schemas.items():
            (schemas_dir / f"{name}.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")

        # assets: package defaults, then site overrides, then collections, then generated prefab css/js
        assets_out = out / "assets"
        _copy_tree(PKG_DIR / "assets", assets_out)
        _copy_tree(cfg.path("assets"), assets_out)
        for collection in cfg.collections():
            if collection.is_root or collection.id in hidden_collections:
                continue
            # Route-addressed, not id-addressed: a collection with an `at:` prefix serves its
            # theme.css and emblem from /<at>/<id>/assets/, which is where the hrefs point (gh-14).
            _copy_tree(collection.assets_dir, out / collection.route_path("assets"))
        (assets_out / "prefabs.css").write_text(renderer.prefab_css(), encoding="utf-8")
        # Linked after prefabs.css and before any collection's own theme.css, so the collection
        # stylesheet wins on ordinary cascade order rather than losing to an inline attribute (gh-24).
        (assets_out / "collections.css").write_text(renderer.collection_css(), encoding="utf-8")
        (assets_out / "prefabs.js").write_text(renderer.prefab_js(), encoding="utf-8")
        (out / ".nojekyll").write_text("", encoding="utf-8")

        # mounts (site-level and per collection)
        for spec in cfg.mounts:
            _mount(cfg, out, cfg.root, spec, target, "", report.warnings)
        for collection in cfg.collections():
            if collection.is_root or collection.id in hidden_collections:
                continue
            for spec in collection.data.get("mounts") or []:
                # Prefixed by the collection's full route (id, plus any `at` prefix - gh-4) so a
                # mounted static tree lands at the same depth as the pages that link into it, and
                # hand-authored relative links inside it (`../../index.html`) keep resolving. Same
                # helper as the asset emit, so route-addressing has exactly one definition (gh-14).
                _mount(cfg, out, collection.root, spec, target, collection.route_path(), report.warnings)

        # dynamic sources
        if run_dynamic:
            report.dynamic = write_all(cfg, target, out, only_on_build=True)
            for name, env in report.dynamic.items():
                if not env.get("ok"):
                    report.warnings.append(f"dynamic source '{name}' failed: {env.get('error')}")

        # hooks: after_build
        for collection in cfg.collections():
            if collection.id in hidden_collections:
                continue
            try:
                call(load_hooks(collection.hooks_path, cfg.root), "after_build", cfg, target, out, collection.summary())
            except HookError as exc:
                raise BuildError(str(exc)) from exc

        # manifest (relative paths only)
        manifest = {
            **build_info,
            "pages": report.pages,
            "docs": report.docs,
            "collections": [c["id"] for c in renderer.ctx["collections"]],
            "evidence": {"stale": len(report.evidence.stale), "warn": len(report.evidence.warnings)} if report.evidence else None,
            "dynamic": {n: {"ok": e.get("ok"), "collected_at": e.get("collected_at")} for n, e in report.dynamic.items()},
        }
        (out / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # dead local links: an href/src a rendered page emits that the output does not carry (gh-14).
        # Last of the output gates, because it is the only one that asks about files: it has to see
        # everything the build emits - the hooks' artefacts, and the manifest every footer links to.
        dead_links = _dead_links(rendered, out, cfg.base_url(target))
        report.warnings += dead_links
        if strict and dead_links:
            strict_failures.append(f"{len(dead_links)} dead link(s).\n  " + "\n  ".join(dead_links))

        # leak scan
        forbidden, flags = cfg.redaction_lists(target)
        fhits, whits = scan_tree(out, forbidden, flags, skip_dirs=())
        report.flags = [f"{rel}:{line}: flagged string {masked}" for rel, line, masked in whits]
        if fhits:
            shutil.rmtree(out, ignore_errors=True)
            lines = [f"{rel}:{line}: forbidden string {masked}" for rel, line, masked in fhits[:50]]
            more = f"\n  ... and {len(fhits) - 50} more" if len(fhits) > 50 else ""
            raise BuildError(
                f"[{target}] output contained forbidden strings; output removed.\n  " + "\n  ".join(lines) + more
            )
        if strict and report.flags:
            strict_failures.append("flagged strings present.\n  " + "\n  ".join(report.flags))
        if strict and report.evidence and report.evidence.stale:
            strict_failures.append(
                f"{len(report.evidence.stale)} stale evidence claim(s); run `yss check`"
            )
        if strict_failures:
            joiner = f"\n[{target}] strict mode: "
            raise BuildError(
                f"[{target}] strict mode: " + joiner.join(strict_failures)
                + f"\n{report.out_label} was left in place for inspection."
            )
        return report
    finally:
        _release_lock(lock)


def build_targets(cfg: Config, targets: list[str], **kwargs) -> list[BuildReport]:
    loaded = load_all(cfg)
    return [build(cfg, t, loaded=loaded, **kwargs) for t in targets]
