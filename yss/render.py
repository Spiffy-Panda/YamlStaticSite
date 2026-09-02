"""Page inflation: prefab rendering, section rendering and layout wrapping (Jinja2)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jinja2
from markdown_it import MarkdownIt
from markupsafe import Markup, escape

from .binding import BindError, is_binding, resolve_binding
from .config import PKG_DIR, Config
from .visibility import slugify

_md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")

PARAM_TYPES = {
    "string": (str,),
    "markdown": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "list": (list,),
    "object": (dict,),
    "any": (object,),
}


class RenderError(Exception):
    pass


def md(text: Any) -> Markup:
    if text is None:
        return Markup("")
    return Markup(_md.render(str(text)))


def md_inline(text: Any) -> Markup:
    if text is None:
        return Markup("")
    return Markup(_md.renderInline(str(text)))


def _attrs(attrs: dict) -> str:
    return " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items() if v is not None)


class Renderer:
    def __init__(
        self,
        cfg: Config,
        target: str,
        docs: dict[str, dict],
        pages: list[dict],
        prefabs: dict[str, dict],
        all_doc_ids: list[str] | None = None,
        build_info: dict | None = None,
    ):
        self.cfg = cfg
        self.target = target
        self.docs = docs
        self.pages = pages
        self.prefabs = prefabs
        self.base_url = cfg.base_url(target)
        self.build_info = build_info or {}
        self.dynamic_sources = cfg.dynamic_sources_for(target)
        self.ctx = {
            "docs": docs,
            "pages": pages,
            "site": cfg.site,
            "prefabs": prefabs,
            "all_doc_ids": list(all_doc_ids or docs),
        }
        layouts = [str(cfg.path("layouts")), str(PKG_DIR / "templates")]
        templates = {f"prefab:{name}": p["template"] for name, p in prefabs.items()}
        self.env = jinja2.Environment(
            loader=jinja2.ChoiceLoader([jinja2.FileSystemLoader(layouts), jinja2.DictLoader(templates)]),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["md"] = md
        self.env.filters["md_inline"] = md_inline
        self.env.filters["slug"] = slugify
        self.env.globals.update(
            prefab=self.prefab,
            url=self.url,
            doc_url=self.doc_url,
            site=cfg.site,
            target=target,
            docs=docs,
            base_url=self.base_url,
        )
        self.nav = self._nav()
        self._doc_pages = self._index_doc_pages()

    # --- helpers ---------------------------------------------------------
    def url(self, path: str) -> str:
        path = str(path or "")
        if path.startswith(("http://", "https://", "mailto:", "#", "data:")):
            return path
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _index_doc_pages(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for page in self.pages:
            for doc_id in page.get("docs") or []:
                index.setdefault(doc_id, page["route"])
        for page in self.pages:  # convention: page id == doc id
            if page["id"] in self.docs:
                index.setdefault(page["id"], page["route"])
        return index

    def doc_url(self, doc_id: str) -> str:
        route = self._doc_pages.get(doc_id)
        return self.url(route) if route else ""

    def _nav(self) -> list[dict]:
        items = []
        for page in self.pages:
            nav = page.get("nav") or {}
            if nav.get("hidden"):
                continue
            items.append(
                {
                    "id": page["id"],
                    "label": nav.get("label") or page["title"],
                    "href": self.url(page["route"]),
                    "route": page["route"],
                    "order": nav.get("order", 100),
                    "group": nav.get("group"),
                    "visibility": page.get("visibility", "public"),
                }
            )
        items.sort(key=lambda n: (n["order"], n["label"]))
        return items

    def render_str(self, source: str, ctx: dict) -> str:
        return self.env.from_string(source).render(**ctx)

    def _bind(self, spec: dict) -> Any:
        return resolve_binding(spec, self.ctx, self.render_str)

    # --- prefabs ---------------------------------------------------------
    def prefab(self, name: str, *arg_dicts: dict, **kwargs: Any) -> Markup:
        if name not in self.prefabs:
            raise RenderError(f"unknown prefab '{name}' (known: {', '.join(sorted(self.prefabs))})")
        spec = self.prefabs[name]
        args: dict[str, Any] = {}
        for d in arg_dicts:
            if isinstance(d, dict):
                args.update(d)
        args.update(kwargs)
        params = spec.get("params") or {}
        for pname, pdef in params.items():
            pdef = pdef or {}
            if args.get(pname) is None or isinstance(args.get(pname), jinja2.Undefined):
                if pdef.get("required") and "default" not in pdef:
                    raise RenderError(f"prefab '{name}': missing required param '{pname}'")
                args[pname] = copy.deepcopy(pdef.get("default"))
                continue
            ptype = pdef.get("type", "any")
            allowed = PARAM_TYPES.get(ptype, (object,))
            value = args[pname]
            if ptype == "integer" and isinstance(value, bool):
                raise RenderError(f"prefab '{name}': param '{pname}' expects integer, got boolean")
            if ptype in ("string", "markdown") and isinstance(value, (int, float)) and not isinstance(value, bool):
                args[pname] = str(value)
            elif not isinstance(value, allowed):
                raise RenderError(
                    f"prefab '{name}': param '{pname}' expects {ptype}, got {type(value).__name__}"
                )
        template = self.env.get_template(f"prefab:{name}")
        try:
            return Markup(template.render(**args, _prefab=name, _params=params))
        except jinja2.TemplateError as exc:
            raise RenderError(f"prefab '{name}': template error: {exc}") from exc

    def prefab_css(self) -> str:
        parts = [f"/* prefab: {n} */\n{p['css'].strip()}" for n, p in sorted(self.prefabs.items()) if p.get("css")]
        return "\n\n".join(parts) + "\n"

    def prefab_js(self) -> str:
        parts = [f"/* prefab: {n} */\n{p['js'].strip()}" for n, p in sorted(self.prefabs.items()) if p.get("js")]
        return "\n\n".join(parts) + "\n"

    # --- sections --------------------------------------------------------
    def render_section(self, page: dict, sec: dict, sid: str) -> Markup:
        stype = sec["type"]
        handler = getattr(self, f"_section_{stype}", None)
        if handler is None:
            raise RenderError(f"unknown section type '{stype}'")
        inner = handler(page, sec, sid)
        parts = [Markup(f'<section id="{escape(sid)}" class="section section-{escape(stype)} {escape(sec.get("class", ""))}">')]
        if sec.get("heading"):
            parts.append(Markup(f'<h2 class="section-heading"><a href="#{escape(sid)}">{escape(sec["heading"])}</a></h2>'))
        if sec.get("intro"):
            parts.append(Markup('<div class="section-intro">') + md(sec["intro"]) + Markup("</div>"))
        parts.append(inner)
        parts.append(Markup("</section>"))
        return Markup("").join(parts)

    def _section_markdown(self, page: dict, sec: dict, sid: str) -> Markup:
        if "from" in sec:
            spec = {k: v for k, v in sec.items() if k in ("from", "where", "sort", "limit", "map", "fields")}
            text = self._bind(spec)
            if isinstance(text, list):
                text = "\n".join(f"- {t}" for t in text)
            elif not isinstance(text, str):
                raise BindError(f"markdown section '{sid}': '{sec['from']}' is not text")
        else:
            text = sec.get("markdown", "")
        if sec.get("jinja"):
            text = self.render_str(text, {"page": page})
        return Markup('<div class="prose">') + md(text) + Markup("</div>")

    def _section_prefab(self, page: dict, sec: dict, sid: str) -> Markup:
        args = {}
        for key, value in (sec.get("args") or {}).items():
            args[key] = self._bind(value) if is_binding(value) else value
        return self.prefab(sec["prefab"], args)

    def _section_dynamic(self, page: dict, sec: dict, sid: str) -> Markup:
        name = sec["source"]
        if name not in self.cfg.dynamic_sources:
            raise RenderError(
                f"unknown dynamic source '{name}' (declare it under dynamic.sources in site.yaml; "
                f"known: {', '.join(self.cfg.dynamic_sources) or 'none'})"
            )
        available = name in self.dynamic_sources
        attrs = {
            "class": "dynamic",
            "data-dynamic": "1",
            "data-section": sid,
            "data-source": name,
            "data-view": sec.get("view", "json"),
            "data-path": sec.get("path", ""),
            "data-columns": json.dumps(sec.get("columns") or []),
            "data-empty": sec.get("empty", "No data is available for this build."),
            "data-refresh": str(sec.get("refresh", 0)),
            "data-available": "1" if available else "0",
            "data-fields": json.dumps(sec.get("card_fields") or {}),
        }
        html = Markup(f"<div {_attrs(attrs)}><p class=\"dynamic-status\">Loading {escape(name)}…</p></div>")
        if sec.get("script"):
            html += Markup(f'<script type="text/x-yss-render" data-for="{escape(sid)}">') + Markup(sec["script"]) + Markup("</script>")
        return html

    def _section_embed(self, page: dict, sec: dict, sid: str) -> Markup:
        kind = sec.get("kind", "iframe")
        src = self.url(sec["src"])
        height = int(sec.get("height", 480))
        title = sec.get("title") or sec.get("heading") or sid
        if kind in ("iframe", "godot", "wasm"):
            attrs = {
                "src": src,
                "title": title,
                "loading": "lazy",
                "allow": "fullscreen; cross-origin-isolated",
                "style": f"height:{height}px",
                "class": f"embed-frame embed-{kind}",
            }
            if sec.get("sandbox"):
                attrs["sandbox"] = sec["sandbox"]
            inner = Markup(f"<iframe {_attrs(attrs)}></iframe>")
        elif kind == "image":
            inner = Markup(f'<img src="{escape(src)}" alt="{escape(title)}" class="embed-image">')
        elif kind == "video":
            inner = Markup(f'<video src="{escape(src)}" controls class="embed-video" style="max-height:{height}px"></video>')
        else:
            raise RenderError(f"embed section '{sid}': unknown kind '{kind}'")
        caption = Markup(f'<figcaption>{escape(sec["caption"])}</figcaption>') if sec.get("caption") else Markup("")
        open_link = Markup(f'<a class="embed-open" href="{escape(src)}" target="_blank" rel="noopener">open in new tab</a>')
        return Markup('<figure class="embed">') + inner + caption + open_link + Markup("</figure>")

    def _section_html(self, page: dict, sec: dict, sid: str) -> Markup:
        return Markup(sec.get("html", ""))

    def _section_include(self, page: dict, sec: dict, sid: str) -> Markup:
        rel = sec["path"]
        path = (self.cfg.root / rel).resolve()
        try:
            path.relative_to(self.cfg.root)
        except ValueError as exc:
            raise RenderError(f"include section '{sid}': path escapes the site root: {rel}") from exc
        if not path.is_file():
            raise RenderError(f"include section '{sid}': file not found: {rel}")
        text = path.read_text(encoding="utf-8")
        mode = sec.get("as") or {".md": "markdown", ".html": "html", ".htm": "html"}.get(path.suffix.lower(), "text")
        if mode == "markdown":
            return Markup('<div class="prose">') + md(text) + Markup("</div>")
        if mode == "html":
            return Markup(text)
        return Markup(f'<pre class="include-text"><code>{escape(text)}</code></pre>')

    # --- pages -----------------------------------------------------------
    def render_page(self, page: dict) -> str:
        sections = []
        for index, sec in enumerate(page.get("sections") or []):
            sid = sec.get("id") or f"section-{index + 1}"
            try:
                html = self.render_section(page, sec, sid)
            except (BindError, RenderError, jinja2.TemplateError) as exc:
                raise RenderError(f"page '{page['id']}' ({page.get('_source')}) section '{sid}': {exc}") from exc
            sections.append(
                {"id": sid, "heading": sec.get("heading"), "html": html, "type": sec["type"]}
            )
        toc = [s for s in sections if s["heading"]]
        layout = page.get("layout", "default")
        try:
            template = self.env.get_template(f"{layout}.html")
        except jinja2.TemplateNotFound as exc:
            raise RenderError(f"page '{page['id']}': layout '{layout}.html' not found") from exc
        head = page.get("head") or {}
        return template.render(
            page=page,
            sections=sections,
            toc=toc,
            nav=self.nav,
            head=head,
            build=self.build_info,
            current_route=page["route"],
            css_links=[self.url(p) for p in head.get("css") or []],
            js_links=[self.url(p) for p in head.get("js") or []],
        )


def route_to_path(out_dir: Path, route: str) -> Path:
    route = route.strip("/")
    return (out_dir / route / "index.html") if route else (out_dir / "index.html")
