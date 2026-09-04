"""Page inflation: prefab rendering, section rendering and layout wrapping (Jinja2).

Markdown goes through markdown-it-py unless site.yaml `markdown.renderer` or a collection's
hooks.py `markdown()` replaces it. Inline references `[[doc]]`, `[[doc#item]]`, `[[#item]]` and
`[[doc#item|label]]` become links to the page that presents the doc.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import jinja2
from markdown_it import MarkdownIt
from markupsafe import Markup, escape

from .binding import BindError, is_binding, resolve_binding
from .config import PKG_DIR, Collection, Config
from .hooks import call, load_hooks
from .loader import CODE_SPAN_RE, find_inline_refs, index_ids, iter_strings, resolve_doc_id
from .symbols import lookup as symbol_lookup, supported as symbols_supported
from .visibility import slugify

_md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")

ANCHOR_RE = re.compile(r'\sid="([^"]*)"')

# The nav group whose members are the site's collections rather than its pages (gh-18). Reserved:
# a page declaring it in `nav.group` falls back to the default page group.
COLLECTION_NAV_GROUP = "collections"

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
        self.current_page: dict | None = None
        self._rendered_refs: list[dict] = []
        self._page_anchors: dict[str, set[str]] = {}
        self._ref_sources: dict[str, list[str]] | None = None
        # Non-fatal things a binding noticed while rendering; the build folds these into
        # report.warnings, so a page that quietly renders the wrong shape still says so (gh-12).
        self.warnings: list[str] = []
        self.ctx = {
            "docs": docs,
            "pages": pages,
            "site": cfg.site,
            "prefabs": prefabs,
            "all_doc_ids": list(all_doc_ids or docs),
            "collections": [self._collection_summary(c) for c in self.collections if not c.is_root],
            "evidence": evidence or [],
            "build": self.build_info,
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
            fail=self._fail,
            prefab=self.prefab,
            url=self.url,
            doc_url=self.doc_url,
            ref_url=self.ref_url,
            site=cfg.site,
            target=target,
            docs=docs,
            base_url=self.base_url,
            collections=self.ctx["collections"],
            build=self.build_info,
            symbol_range=self.symbol_range,
        )
        self.nav = self._nav()
        self._doc_pages = self._index_doc_pages()

    # --- helpers ---------------------------------------------------------
    def symbol_range(self, path: str, name: str) -> list | None:
        """`{{ symbol_range(m.path, e.name) }}` -> [start, end] for a code map export, or None.

        Resolved at render time so a deep link carries real line anchors in the static HTML and
        works with JavaScript off (p-static-first). Parsing is cached by mtime in yss.symbols, so
        a page with many exports parses each module once.
        """
        if not path or not name or not symbols_supported(path):
            return None
        span = symbol_lookup(self.cfg.root, str(path), str(name))
        return list(span) if span else None

    @staticmethod
    def _fail(message: str) -> str:
        """`{{ fail('...') }}` - a prefab rejecting its own arguments.

        Jinja has no raise, and the workaround (forcing an UndefinedError with a bogus lookup)
        buries the reason in a message about dict attributes. prefab() already turns a
        TemplateError into a RenderError naming the prefab, so raising one here is enough.
        """
        raise jinja2.TemplateError(message)

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
            return self.url(self.current_collection.route_path(path))
        return self.url(path)

    def _mounted_prefixes(self, c: Collection) -> set[str]:
        """First path segment of every mount this collection carries in this target."""
        out = set()
        for spec in c.data.get("mounts") or []:
            if self.target in (spec.get("targets") or ["private"]):
                out.add(spec["at"].strip("/").split("/")[0])
        return out

    def _card_links(self, c: Collection) -> list[dict]:
        """Resolve the card contract's links against the collection route for this target.

        A relative link into a mount the target does not carry would be a dead card link on the
        public site, so it is dropped instead of rendered.
        """
        carried = self._mounted_prefixes(c)
        declared = {(spec.get("at") or "").strip("/").split("/")[0] for spec in c.data.get("mounts") or []}
        links = []
        for link in c.links:
            href = str(link.get("href") or "")
            absolute = href.startswith(("http://", "https://", "mailto:", "/", "#", "data:"))
            if not absolute:
                head = href.lstrip("/").split("/")[0]
                if head in declared and head not in carried:
                    continue
                href = self.url(c.route_path(href))
            else:
                href = self.url(href)
            links.append(dict(link, href=href, kind=link.get("kind") or "page"))
        return links

    def _emblem_url(self, c: Collection) -> str | None:
        """The href for a collection's emblem when it is a file, None when it is a glyph (gh-14).

        `collection.yaml`'s `emblem` is either a literal grapheme ("🧪") or a path relative to the
        collection ("assets/emblem.svg"). Templates used to re-derive the href themselves from
        `<id>/<emblem>`, which ignored `at:` and re-broke on every new consumer, so the resolved
        url is computed once here and published on `$collections` (and data/collections.json).
        `summary()` keeps `emblem` as the authored value.
        """
        emblem = str(c.data.get("emblem") or "")
        if not emblem or ("/" not in emblem and "." not in emblem):
            return None  # a glyph, not a path
        if emblem.startswith(("http://", "https://", "/", "data:")):
            return self.url(emblem)
        return self.url(c.route_path(emblem))

    def _collection_summary(self, c: Collection) -> dict:
        info = c.summary()
        info["docs"] = sorted(d for d, doc in self.docs.items() if doc.get("_collection") == c.id)
        info["pages"] = [p["route"] for p in self.pages if p.get("_collection") == c.id]
        info["href"] = self.url(c.route_prefix)
        info["links"] = self._card_links(c)
        info["emblem_url"] = self._emblem_url(c)
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

    def _resolve_doc_ref(self, doc_ref: str, exact: bool) -> str:
        """Which doc a reference names, honouring whether it is authored or already global (gh-23).

        `resolve_doc_id` is a *scope-relative* resolver: inside collection `c` it answers `c/plan`
        for `plan`, which is right for a reference somebody wrote and wrong for an id the build
        generated. `Claim.doc` and `$docs`' `id` are already the exact keys of `self.docs`, and
        pushing one back through the resolver silently rewrites it to a collection-local doc that
        merely shares the short name. The two cases are the same string, so the caller has to say
        which it holds; `_expand_plain` makes the same distinction for the links it expands.
        """
        if exact and doc_ref in self.docs:
            return doc_ref
        cid = self.current_collection.id if self.current_collection else None
        return resolve_doc_id(doc_ref, cid, self.docs) or doc_ref

    def doc_url(self, doc_ref: str, exact: bool = False) -> str:
        doc_id = self._resolve_doc_ref(doc_ref, exact)
        route = self._doc_pages.get(doc_id)
        return self.url(route) if route else ""

    def ref_url(self, ref: str, exact: bool = False) -> str:
        """`doc`, `doc#item` or `#item` -> href (empty when the doc has no page in this target).

        Pass `exact=True` when the doc half is a generated global id rather than an authored
        reference - see `_resolve_doc_ref`.
        """
        doc_ref, _, item = ref.partition("#")
        base = self.doc_url(doc_ref, exact) if doc_ref else (self.doc_url(self.current_doc, True) if self.current_doc else "")
        if not base:
            return ""
        if not item:
            return base
        doc_id = (self._resolve_doc_ref(doc_ref, exact) if doc_ref else self.current_doc) or doc_ref
        self.note_ref(doc_id, item, base)
        return f"{base}#{item}"

    def _page_state(self, page: dict) -> dict:
        """What the docs a page presents say about it, for the nav to read.

        Nothing here is declared on the page: a page is finished because the docs it presents are
        archived, and a worksheet is still asking because some of its questions have no
        `resolution` yet (adr-009 - state is derived, never hand-set). `waiting` is the number of
        calls a reader would have to make if they opened it.
        """
        ids = [d for d in (page.get("docs") or []) if d in self.docs]
        docs = [self.docs[d] for d in ids]
        if not docs:
            return {"archived": False, "waiting": 0}
        # The first doc a page lists is its subject; the rest are supporting data it also binds.
        # A page is finished when its subject is archived, not when everything it touches is.
        waiting = sum(
            1
            for doc in docs
            if doc.get("kind") == "worksheet"
            for q in (doc.get("questions") or [])
            if not q.get("resolution")
        )
        return {"archived": docs[0].get("status") == "archived", "waiting": waiting}

    def _nav(self) -> list[dict]:
        # `collections` is reserved: its members are collections, not pages (gh-18). `nav.group` is
        # a free string in page.schema.yaml, so a page could otherwise declare it and land in a
        # group it has no business in; it falls back to the default page group instead.
        groups = [g["id"] for g in (self.cfg.nav.get("groups") or []) if g["id"] != COLLECTION_NAV_GROUP]
        default_group = groups[0] if groups else None
        items = []
        for page in self.pages:
            nav = page.get("nav") or {}
            if nav.get("hidden"):
                continue
            state = self._page_state(page)
            if state["archived"]:
                continue  # a page whose docs are all archived is a record, not a destination
            group = nav.get("group") or default_group
            items.append(
                {
                    "id": page["id"],
                    "label": nav.get("label") or page["title"],
                    "href": self.url(page["route"]),
                    "route": page["route"],
                    "order": nav.get("order", 100),
                    "group": group if group in groups else default_group,
                    "waiting": state["waiting"],
                    "collection": page.get("_collection", ""),
                    "visibility": page.get("visibility", "public"),
                }
            )
        items.sort(key=lambda n: (n["order"], n["label"]))
        return items

    def _collection_nav(self, current_cid: str = "") -> list[dict]:
        """The collections as nav items, shaped exactly like page items so one macro draws both.

        Every collection used to get an unconditional pill in a hard-coded span with no label, no
        ordering and no menu branch, so nine musings wrapped the top bar onto a second row and
        nothing could be done about it (gh-18). As a group they inherit everything page groups
        already had. Ordered by the collection's own (order, title) - the same order the landing
        cards use - and `active` is set here rather than compared against the route, because a
        reader is inside a collection on all of its pages, not just its landing page.
        """
        items = []
        for c in sorted(self.ctx["collections"], key=lambda c: (c.get("order", 100), c.get("title") or "")):
            items.append(
                {
                    "id": c["id"],
                    "label": c["title"],
                    "href": c["href"],
                    "route": c["route"],
                    "order": c.get("order", 100),
                    "group": COLLECTION_NAV_GROUP,
                    "collection": c["id"],
                    "visibility": c.get("visibility", "public"),
                    "kind": "collection",
                    "active": c["id"] == current_cid,
                    # A file emblem is a picture, and the bar is text; only a glyph goes in a pill.
                    "emblem": None if c.get("emblem_url") else c.get("emblem"),
                    "waiting": 0,
                }
            )
        return items

    def _nav_groups(self, items: list[dict], collection_items: list[dict] | None = None) -> list[dict]:
        """The nav as the template wants it: ordered groups, each with its visible items.

        The `collections` group's members come from `collection_items`, not from `items`. A site
        that customised `nav.groups` before gh-18 has no `collections` entry, so the group is
        appended after the page groups - which is where it always used to be drawn.
        """
        collection_items = list(collection_items or [])
        specs = list(self.cfg.nav.get("groups") or [])
        out = []
        for spec in specs:
            members = collection_items if spec["id"] == COLLECTION_NAV_GROUP else [n for n in items if n["group"] == spec["id"]]
            if members:
                out.append({**spec, "items": members, "waiting": sum(n.get("waiting") or 0 for n in members)})
        if collection_items and not any(s["id"] == COLLECTION_NAV_GROUP for s in specs):
            out.append({"id": COLLECTION_NAV_GROUP, "label": "", "items": collection_items, "waiting": 0})
        if not out and items:
            out.append({"id": "", "label": "", "items": items, "waiting": 0})
        return out

    def render_str(self, source: str, ctx: dict) -> str:
        return self.env.from_string(source).render(**ctx)

    def _bind(self, spec: dict, sid: str | None = None) -> Any:
        return resolve_binding(spec, self.ctx, self.render_str, self._binding_warner(sid))

    def _binding_warner(self, sid: str | None) -> Callable[[str], None]:
        """Prefix a binding's own complaint with the page and section a human would go and edit."""

        def warn(message: str) -> None:
            page = self.current_page or {}
            where = f"page '{page.get('id', '?')}' ({page.get('_source', '?')})"
            if sid:
                where += f" section '{sid}'"
            entry = f"{where}: {message}"
            if entry not in self.warnings:
                self.warnings.append(entry)

        return warn

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
            base = self.url(route) if route else ""
            if base and item:
                self.note_ref(doc_id, item, base)
            href = (base + (f"#{item}" if item else "")) if base else ""
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
            text = self._bind(spec, sid)
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
            args[key] = self._bind(value, sid) if is_binding(value) else value
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
        self.current_page = page
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
            site_css = [self.url(p) for p in (self.cfg.data.get("theme") or {}).get("css") or []]
            css_links = site_css + [self.collection_url(p) for p in theme.get("css") or []] + [self.collection_url(p) for p in head.get("css") or []]
            doc_ids = [resolve_doc_id(d, cid, self.docs) or d for d in page.get("docs") or []]
            freshness = [self.docs[d].get("_evidence", {}).get("status", "ok") for d in doc_ids if d in self.docs]
            sub_nav = [n for n in self.nav if n["collection"] == cid] if cid else []
            html = template.render(
                page=page,
                sections=sections,
                toc=toc,
                nav=[n for n in self.nav if not n["collection"]],
                nav_groups=self._nav_groups([n for n in self.nav if not n["collection"]], self._collection_nav(cid)),
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
            self._page_anchors[self.url(page["route"])] = set(ANCHOR_RE.findall(html))
            return html
        finally:
            self.current_collection = None
            self.current_doc = None
            self.current_page = None
            self.ctx["collection"] = None

    # --- rendered references (gh-11) -------------------------------------
    def note_ref(self, doc_id: str | None, item: str, target_url: str) -> None:
        """Remember that this page emitted a link to `target_url#item`.

        Called from the two places that turn a reference into an href - `[[doc#item]]` expansion
        and `ref_url()` - so every reference a reader can click is recorded with the page it was
        clicked from. `dead_refs()` then asks whether the anchor was ever emitted.
        """
        if not item or not target_url or self.current_page is None:
            return
        self._rendered_refs.append(
            {
                "doc": doc_id or "",
                "item": item,
                "target": target_url,
                "page": self.current_page.get("route", ""),
                "source": self.current_page.get("_source", ""),
            }
        )

    def _ref_source_index(self) -> dict[str, list[str]]:
        """`{"plan#r-x": ["docs/pending.yaml at questions/2/help", ...]}` - where each reference is written."""
        if self._ref_sources is not None:
            return self._ref_sources
        index: dict[str, list[str]] = {}

        def scan(obj: dict, where: str, cid: str | None, own_doc: str | None) -> None:
            for path, text in iter_strings(obj):
                if "[[" not in text:
                    continue
                for _raw, doc_ref, item, _label in find_inline_refs(text):
                    if not item:
                        continue
                    target = resolve_doc_id(doc_ref, cid, self.docs) if doc_ref else own_doc
                    if target:
                        index.setdefault(f"{target}#{item}", []).append(f"{where} at {path}")

        def scan_item_refs(obj: dict, where: str, cid: str | None) -> None:
            """`x-ref: item` fields spell the same reference as `doc/item`; index those too."""
            for path, text in iter_strings(obj):
                if "/" not in text or len(text) > 120 or " " in text:
                    continue
                doc_ref, _, item = text.rpartition("/")
                target = resolve_doc_id(doc_ref, cid, self.docs)
                if target and item:
                    index.setdefault(f"{target}#{item}", []).append(f"{where} at {path}")

        for doc_id, doc in self.docs.items():
            scan(doc, doc.get("_source", doc_id), doc.get("_collection"), doc_id)
            scan_item_refs(doc, doc.get("_source", doc_id), doc.get("_collection"))
        for page in self.pages:
            primary = (page.get("docs") or [None])[0]
            cid = page.get("_collection")
            own = resolve_doc_id(primary, cid, self.docs) if primary else None
            scan(page, page.get("_source", page["id"]), cid, own)
        self._ref_sources = index
        return index

    def dead_refs(self) -> list[str]:
        """Every reference this build rendered as a link to an anchor no page actually emitted.

        `check_refs` proves the *item* exists in the data; this proves the *anchor* exists in the
        rendering, which is where a reader meets it. Call it after every page has been rendered.
        A reference is dead either because the prefab presenting the item emits no `id`, or
        because the section that would present it filters the item out.
        """
        sources = self._ref_source_index()
        seen: set[tuple[str, str, str]] = set()
        out: list[str] = []
        for ref in self._rendered_refs:
            anchors = self._page_anchors.get(ref["target"])
            if anchors is None or ref["item"] in anchors:
                continue  # target page not in this build, or the anchor is there
            key = (ref["doc"], ref["item"], ref["page"])
            if key in seen:
                continue
            seen.add(key)
            written = sorted(set(sources.get(f"{ref['doc']}#{ref['item']}") or [])) or [ref["source"] or "?"]
            out.append(
                f"dead reference [[{ref['doc']}#{ref['item']}]] on page {ref['page']}: "
                f"{ref['target']} emits no anchor '{ref['item']}' "
                f"(written in {'; '.join(written[:3])})"
            )
        return sorted(out)


def route_to_path(out_dir: Path, route: str) -> Path:
    route = route.strip("/")
    return (out_dir / route / "index.html") if route else (out_dir / "index.html")
