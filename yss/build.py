"""Build orchestration: validate -> check refs -> filter for target -> evidence -> render -> export -> scan."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import PKG_DIR, Config
from .dynamic import write_all
from .evidence import EvidenceReport, check as evidence_check, inject as evidence_inject
from .hooks import HookError, call, load_hooks
from .loader import SchemaRegistry, check_refs, load_collection_configs, load_docs, load_pages, load_prefabs, resolve_doc_id
from .render import Renderer, RenderError, route_to_path
from .visibility import filter_for_target, is_visible, scan_tree


class BuildError(Exception):
    pass


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


def _mount(cfg: Config, out: Path, base: Path, spec: dict, target: str, prefix: str, warnings: list[str]) -> None:
    targets = spec.get("targets") or ["private"]
    if target not in targets:
        return
    src = (base / spec["path"]).resolve()
    at = (prefix + spec["at"].strip("/")).strip("/")
    dst = (out / at).resolve()
    if dst != out and out not in dst.parents:
        raise BuildError(f"mount '{spec['at']}' escapes the output directory")
    if not src.is_dir():
        warnings.append(f"mount {spec['path']} -> /{at}/: source folder missing, skipped")
        return
    _copy_tree(src, dst)


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
    _safe_clear(out, cfg)
    report = BuildReport(target=target, out_dir=out)
    try:
        report.out_label = out.relative_to(cfg.root).as_posix()
    except ValueError:
        report.out_label = out.name

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

    # pages
    for page in pages_t:
        try:
            html = renderer.render_page(page)
        except RenderError as exc:
            raise BuildError(str(exc)) from exc
        path = route_to_path(out, page["route"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        report.pages.append(page["route"])

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
                "page": renderer.doc_url(doc_id) or None,
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
        _copy_tree(collection.assets_dir, out / collection.id / "assets")
    (assets_out / "prefabs.css").write_text(renderer.prefab_css(), encoding="utf-8")
    (assets_out / "prefabs.js").write_text(renderer.prefab_js(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # mounts (site-level and per collection)
    for spec in cfg.mounts:
        _mount(cfg, out, cfg.root, spec, target, "", report.warnings)
    for collection in cfg.collections():
        if collection.is_root or collection.id in hidden_collections:
            continue
        for spec in collection.data.get("mounts") or []:
            _mount(cfg, out, collection.root, spec, target, f"{collection.id}/", report.warnings)

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
        shutil.rmtree(out, ignore_errors=True)
        raise BuildError(f"[{target}] strict mode: flagged strings present.\n  " + "\n  ".join(report.flags))
    if strict and report.evidence and report.evidence.stale:
        shutil.rmtree(out, ignore_errors=True)
        raise BuildError(f"[{target}] strict mode: {len(report.evidence.stale)} stale evidence claim(s); run `yss check`")
    return report


def build_targets(cfg: Config, targets: list[str], **kwargs) -> list[BuildReport]:
    loaded = load_all(cfg)
    return [build(cfg, t, loaded=loaded, **kwargs) for t in targets]
