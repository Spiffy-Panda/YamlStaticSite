"""Build orchestration: validate -> filter for target -> render -> export data -> scan for leaks."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import PKG_DIR, Config
from .dynamic import write_all
from .loader import SchemaRegistry, load_docs, load_pages, load_prefabs
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

    out_label: str = ""

    def summary(self) -> str:
        return (
            f"[{self.target}] {len(self.pages)} pages, {len(self.docs)} docs, "
            f"{len(self.dynamic)} dynamic sources -> {self.out_label or self.out_dir}"
        )


def load_all(cfg: Config) -> Loaded:
    registry = SchemaRegistry(cfg.schema_dirs())
    errors: list[str] = []
    if cfg.raw and "site" in registry.schemas:
        errors += registry.validate(cfg.raw, "site", cfg.source.name if cfg.source else "site.yaml")
    docs, e1 = load_docs(cfg, registry)
    pages, e2 = load_pages(cfg, registry)
    prefabs, e3 = load_prefabs(cfg, registry)
    errors += e1 + e2 + e3
    errors += cross_checks(cfg, docs, pages, prefabs)
    return Loaded(registry, docs, pages, prefabs, errors)


def cross_checks(cfg: Config, docs: dict, pages: list, prefabs: dict) -> list[str]:
    errors = []
    for page in pages:
        where = page.get("_source", page["id"])
        for doc_id in page.get("docs") or []:
            if doc_id not in docs:
                errors.append(f"{where}: docs lists unknown doc id '{doc_id}'")
        for index, sec in enumerate(page.get("sections") or []):
            sid = sec.get("id") or f"section-{index + 1}"
            if sec["type"] == "prefab" and sec.get("prefab") not in prefabs:
                errors.append(f"{where}: section '{sid}': unknown prefab '{sec.get('prefab')}'")
            if sec["type"] == "dynamic" and sec.get("source") not in cfg.dynamic_sources:
                errors.append(f"{where}: section '{sid}': unknown dynamic source '{sec.get('source')}'")
    for doc in docs.values():
        for rel in doc.get("related") or []:
            if rel not in docs:
                errors.append(f"{doc['_source']}: related lists unknown doc id '{rel}'")
    return errors


def _safe_clear(out_dir: Path, cfg: Config) -> None:
    out_dir = out_dir.resolve()
    if out_dir == cfg.root or out_dir in cfg.root.parents:
        raise BuildError(f"refusing to clear {out_dir}: it contains the site root")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def build(
    cfg: Config,
    target: str,
    out_dir: Path | None = None,
    strict: bool = False,
    run_dynamic: bool = True,
    loaded: Loaded | None = None,
) -> BuildReport:
    cfg.target(target)  # raises on unknown target
    loaded = loaded or load_all(cfg)
    if loaded.errors:
        raise BuildError("validation failed:\n  " + "\n  ".join(loaded.errors))

    docs_t = {
        doc_id: filter_for_target(doc, target)
        for doc_id, doc in loaded.docs.items()
        if is_visible(doc, target)
    }
    pages_t = [filter_for_target(page, target) for page in loaded.pages if is_visible(page, target)]
    out = (out_dir or cfg.out_dir(target)).resolve()
    _safe_clear(out, cfg)
    report = BuildReport(target=target, out_dir=out)
    try:
        report.out_label = out.relative_to(cfg.root).as_posix()
    except ValueError:
        report.out_label = out.name

    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    build_info = {
        "target": target,
        "built_at": built_at,
        "version": __version__,
        "site": cfg.site.get("name"),
        "base_url": cfg.base_url(target),
    }
    renderer = Renderer(cfg, target, docs_t, pages_t, loaded.prefabs, list(loaded.docs), build_info)

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
        (data_dir / "docs" / f"{doc_id}.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        index.append(
            {
                "id": doc_id,
                "kind": doc.get("kind"),
                "title": doc.get("title"),
                "summary": doc.get("summary"),
                "status": doc.get("status"),
                "updated": doc.get("updated"),
                "tags": doc.get("tags") or [],
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
                {"id": p["id"], "route": p["route"], "url": renderer.url(p["route"]), "title": p["title"], "summary": p.get("summary"), "docs": p.get("docs") or []}
                for p in pages_t
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (data_dir / "site.json").write_text(json.dumps({"site": cfg.site, **build_info}, indent=2), encoding="utf-8")

    # schemas (so agents and the site can read them from the output too)
    schemas_dir = out / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    for name, schema in loaded.registry.schemas.items():
        (schemas_dir / f"{name}.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")

    # assets: package defaults, then site overrides, then generated prefab css/js
    assets_out = out / "assets"
    _copy_tree(PKG_DIR / "assets", assets_out)
    _copy_tree(cfg.path("assets"), assets_out)
    (assets_out / "prefabs.css").write_text(renderer.prefab_css(), encoding="utf-8")
    (assets_out / "prefabs.js").write_text(renderer.prefab_js(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # dynamic sources
    if run_dynamic:
        report.dynamic = write_all(cfg, target, out, only_on_build=True)
        for name, env in report.dynamic.items():
            if not env.get("ok"):
                report.warnings.append(f"dynamic source '{name}' failed: {env.get('error')}")

    # manifest (relative paths only)
    manifest = {
        **build_info,
        "pages": report.pages,
        "docs": report.docs,
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
    return report


def build_targets(cfg: Config, targets: list[str], **kwargs) -> list[BuildReport]:
    loaded = load_all(cfg)
    return [build(cfg, t, loaded=loaded, **kwargs) for t in targets]
