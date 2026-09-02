"""Page inflation: prefab rendering, section rendering and layout wrapping (Jinja2).

Markdown goes through markdown-it-py unless site.yaml `markdown.renderer` or a collection's
hooks.py `markdown()` replaces it. Inline references `[[doc]]`, `[[doc#item]]`, `[[#item]]` and
`[[doc#item|label]]` become links to the page that presents the doc.
"""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any, Callable

import jinja2
from markdown_it import MarkdownIt
from markupsafe import Markup, escape

from .binding import BindError, is_binding, resolve_binding
from .config import PKG_DIR, Collection, Config
from .hooks import call, load_hooks
from .loader import CODE_SPAN_RE, find_inline_refs, index_ids, resolve_doc_id
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


def default_markdown(text: str) -> str:
    return _md.render(text)


def default_markdown_inline(text: str) -> str:
    return _md.renderInline(text)


def load_renderer(spec: str | None) -> Callable[[str], str] | None:
    if not spec:
        return None
    module_name, _, func_name = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RenderError(f"markdown.renderer '{spec}': cannot import {module_name}: {exc}") from exc
    func = getattr(module, func_name, None)
    if not callable(func):
        raise RenderError(f"markdown.renderer '{spec}': no callable '{func_name}'")
    return func


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
        evidence: list[dict] | None = None,
    ):
        self.cfg = cfg
        self.target = target
        self.docs = docs
        self.pages = pages
        self.prefabs = prefabs
        self.base_url = cfg.base_url(target)
        self.build_info = build_info or {}
        self.dynamic_sources = cfg.dynamic_sources_for(target)
        self.collections = [c for c in cfg.collections() if target != "public" or c.visibility != "private"]
        self.current_collection: Collection | None = None
        self.current_doc: str | None = None
        self.site_renderer = load_renderer((cfg.data.get("markdown") or {}).get("renderer"))
        self._item_index: dict[str, dict] = {}
        self.ctx = {
            "docs": docs,
            "pages": pages,
            "site": cfg.site,
            "prefabs": prefabs,
            "all_doc_ids": list(all_doc_ids or docs),
            "collections": [self._collection_summary(c) for c in self.collections if not c.is_root],
            "evidence": evidence or [],
            "collection": None,
        }
        layouts = [str(cfg.path("layouts")), str(PKG_DIR / "templates")]
        templates = {f"prefab:{name}": p["template"] for name, p in prefabs.items()}
        self.env = jinja2.Environment(
            loader=jinja2.ChoiceLoader([jinja2.FileSystemLoader(layouts), jinja2.DictLoader(templates)]),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["md"] = self.md
        self.env.filters["md_inline"] = self.md_inline
        self.env.filters["slug"] = slugify
        self.env.globals.update(
            prefab=self.prefab,
            url=self.url,
            doc_url=self.doc_url,
            ref_url=self.ref_url,
            site=cfg.site,
            target=target,
            docs=docs,
            base_url=self.base_url,
            collections=self.ctx["collections"],
        )
        self.nav = self._nav()
        self._doc_pages = self._index_doc_pages()

    # --- helpers ---------------------------------------------------------
    def url(self, path: str) -> str:
        path = str(path or "")
        if path.startswith(("http://", "https://", "mailto:", "#", "data:")):
            return path
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def collection_url(self, path: str) -> str:
        """A path relative to the current collection (assets/..., play/...)."""
        if path.startswith(("http://", "https://", "/", "#", "data:")):
            return self.url(path)
        if self.current_collection and not self.current_collection.is_root:
            return self.url(f"{self.current_collection.id}/{path}")
        return self.url(path)

    def _collection_summary(self, c: Collection) -> dict:
        info = c.summary()
        info["docs"] = sorted(d for d, doc in self.docs.items() if doc.get("_collection") == c.id)
        info["pages"] = [p["route"] for p in self.pages if p.get("_collection") == c.id]
        info["href"] = self.url(c.route_prefix)
        statuses = [self.docs[d].get("_evidence", {}).get("status", "ok") for d in info["docs"]]
        info["evidence"] = "stale" if "stale" in statuses else ("warn" if "warn" in statuses else "ok")
        return info

    def _index_doc_pages(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for page in self.pages:
            for doc_ref in page.get("docs") or []:
                doc_id = resolve_doc_id(doc_ref, page.get("_collection"), self.docs)
                if doc_id:
                    index.setdefault(doc_id, page["route"])
        for page in self.pages:  # convention: page id == doc id
            if page["id"] in self.docs:
                index.setdefault(page["id"], page["route"])
        return index

    def doc_url(self, doc_ref: str) -> str:
        cid = self.current_collection.id if self.current_collection else None
        doc_id = resolve_doc_id(doc_ref, cid, self.docs) or doc_ref
        route = self._doc_pages.get(doc_id)
        return self.url(route) if route else ""

    def ref_url(self, ref: str) -> str:
        """`doc`, `doc#item` or `#item` -> href (empty when the doc has no page in this target)."""
        doc_ref, _, item = ref.partition("#")
        base = self.doc_url(doc_ref) if doc_ref else (self.doc_url(self.current_doc) if self.current_doc else "")
        if not base:
            return ""
        return f"{base}#{item}" if item else base

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
                    "collection": page.get("_collection", ""),
                    "visibility": page.get("visibility", "public"),
                }
            )
        items.sort(key=lambda n: (n["order"], n["label"]))
        return items

    def render_str(self, source: str, ctx: dict) -> str:
        return self.env.from_string(source).render(**ctx)

    def _bind(self, spec: dict) -> Any:
        return resolve_binding(spec, self.ctx, self.render_str)

    # --- markdown --------------------------------------------------------
    def _markdown_fn(self) -> Callable[[str], str]:
        if self.current_collection and self.current_collection.hooks_path:
            hooks = load_hooks(self.current_collection.hooks_path, self.cfg.root)
            fn = getattr(hooks, "markdown", None) if hooks else None
            if callable(fn):
                return fn
        return self.site_renderer or default_markdown

    def _item_title(self, doc_id: str, item: str) -> str | None:
        if doc_id not in self._item_index:
            self._item_index[doc_id], _ = index_ids(self.docs[doc_id]) if doc_id in self.docs else ({}, [])
        entry = self._item_index[doc_id].get(item)
        if isinstance(entry, dict):
            return entry.get("title") or entry.get("name") or entry.get("term") or entry.get("question")
        return None

    def expand_refs(self, text: str) -> str:
        """Replace [[...]] with markdown links (or plain text when the target is not in this target)."""
        if "[[" not in text:
            return text
        parts = CODE_SPAN_RE.split(text)
        codes = CODE_SPAN_RE.findall(text)
        expanded = [self._expand_plain(part) for part in parts]
        out = []
        for i, part in enumerate(expanded):
            out.append(part)
            if i < len(codes):
                out.append(codes[i])
        return "".join(out)

    def _expand_plain(self, text: str) -> str:
        cid = self.current_collection.id if self.current_collection else None
        for raw, doc_ref, item, label in find_inline_refs(text):
            doc_id = resolve_doc_id(doc_ref, cid, self.docs) if doc_ref else self.current_doc
            if doc_id and doc_id not in self.docs:
                doc_id = None
            if doc_id is None:
                text = text.replace(raw, f"`{label or (doc_ref or '') + ('#' + item if item else '')}`")
                continue
            title = self.docs[doc_id].get("title", doc_id)
            if item:
                shown = label or self._item_title(doc_id, item) or item
            else:
                shown = label or title
            route = self._doc_pages.get(doc_id)  # exact global id: never re-resolved against the collection
            href = (self.url(route) + (f"#{item}" if item else "")) if route else ""
            text = text.replace(raw, f"[{shown}]({href})" if href else shown)
        return text

    def md(self, text: Any) -> Markup:
        if text is None or isinstance(text, jinja2.Undefined):
            return Markup("")
        return Markup(self._markdown_fn()(self.expand_refs(str(text))))

    def md_inline(self, text: Any) -> Markup:
        if text is None or isinstance(text, jinja2.Undefined):
            return Markup("")
        text = self.expand_refs(str(text))
        fn = self._markdown_fn()
        if fn is default_markdown:
            return Markup(default_markdown_inline(text))
        html = fn(text).strip()
        if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
            html = html[3:-4]
        return Markup(html)

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
            parts.append(Markup('<div class="section-intro">') + self.md(sec["intro"]) + Markup("</div>"))
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
        return Markup('<div class="prose">') + self.md(text) + Markup("</div>")

    def _section_prefab(self, page: dict, sec: dict, sid: str) -> Markup:
        args = {}
        for key, value in (sec.get("args") or {}).items():
            args[key] = self._bind(value) if is_binding(value) else value
        return self.prefab(sec["prefab"], args)

    def _resolve_source(self, name: str) -> str | None:
        if name in self.cfg.dynamic_sources:
            return name
        if self.current_collection and not self.current_collection.is_root:
            qualified = f"{self.current_collection.id}.{name}"
            if qualified in self.cfg.dynamic_sources:
                return qualified
        return None

    def _section_dynamic(self, page: dict, sec: dict, sid: str) -> Markup:
        name = self._resolve_source(sec["source"])
        if name is None:
            raise RenderError(
                f"unknown dynamic source '{sec['source']}' (declare it under dynamic.sources in site.yaml or "
                f"collection.yaml; known: {', '.join(self.cfg.dynamic_sources) or 'none'})"
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
        src = self.collection_url(sec["src"])
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
        base = self.current_collection.root if self.current_collection and not self.current_collection.is_root else self.cfg.root
        path = (base / rel).resolve()
        if not path.is_file():
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
            return Markup('<div class="prose">') + self.md(text) + Markup("</div>")
        if mode == "html":
            return Markup(text)
        return Markup(f'<pre class="include-text"><code>{escape(text)}</code></pre>')

    # --- pages -----------------------------------------------------------
    def render_page(self, page: dict) -> str:
        cid = page.get("_collection") or ""
        self.current_collection = next((c for c in self.collections if c.id == cid), None)
        self.ctx["collection"] = cid or None
        primary = (page.get("docs") or [None])[0]
        self.current_doc = resolve_doc_id(primary, cid, self.docs) if primary else None
        try:
            sections = []
            for index, sec in enumerate(page.get("sections") or []):
                sid = sec.get("id") or f"section-{index + 1}"
                try:
                    html = self.render_section(page, sec, sid)
                except (BindError, RenderError, jinja2.TemplateError) as exc:
                    raise RenderError(f"page '{page['id']}' ({page.get('_source')}) section '{sid}': {exc}") from exc
                sections.append({"id": sid, "heading": sec.get("heading"), "html": html, "type": sec["type"]})
            toc = [s for s in sections if s["heading"]]
            layout = page.get("layout", "default")
            try:
                template = self.env.get_template(f"{layout}.html")
            except jinja2.TemplateNotFound as exc:
                raise RenderError(f"page '{page['id']}': layout '{layout}.html' not found") from exc
            head = page.get("head") or {}
            collection = self.current_collection
            theme = (collection.data.get("theme") or {}) if collection and not collection.is_root else {}
            css_links = [self.collection_url(p) for p in theme.get("css") or []] + [self.collection_url(p) for p in head.get("css") or []]
            doc_ids = [resolve_doc_id(d, cid, self.docs) or d for d in page.get("docs") or []]
            freshness = [self.docs[d].get("_evidence", {}).get("status", "ok") for d in doc_ids if d in self.docs]
            sub_nav = [n for n in self.nav if n["collection"] == cid] if cid else []
            return template.render(
                page=page,
                sections=sections,
                toc=toc,
                nav=[n for n in self.nav if not n["collection"]],
                sub_nav=sub_nav,
                collection=next((c for c in self.ctx["collections"] if c["id"] == cid), None) if cid else None,
                theme=theme,
                head=head,
                build=self.build_info,
                current_route=page["route"],
                css_links=css_links,
                js_links=[self.collection_url(p) for p in head.get("js") or []],
                page_docs=[{"id": d, "json": self.url(f"data/docs/{d}.json")} for d in doc_ids],
                freshness="stale" if "stale" in freshness else ("warn" if "warn" in freshness else "ok"),
            )
        finally:
            self.current_collection = None
            self.current_doc = None
            self.ctx["collection"] = None


def route_to_path(out_dir: Path, route: str) -> Path:
    route = route.strip("/")
    return (out_dir / route / "index.html") if route else (out_dir / "index.html")
