"""Where a rendered region came from: section spec -> a sentence a reader can check (gh-29).

A reader looking at a card grid has no way to see which structured query produced it. The page
header names the whole doc set, which is per-page and too coarse to answer "why these four?".

This module is the pure half. It turns a section's YAML into an `Attribution` - a phrase, the doc
it read, and that doc's source path - without rendering anything, so it can be unit tested on
spec dictionaries alone. `render.py` calls it from `render_section` and decides how to present the
result.

Two things it deliberately does not do:

- It never re-resolves a binding to count items. Counts arrive from the caller, which already has
  them; the phrase reads the same with or without.
- It never picks silently between several docs. A section that binds two docs says so and names
  both, because "from plan.yaml" over a region half of which came from design.yaml is worse than
  no attribution at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .binding import BINDING_KEYS, ITEMS_SUFFIX, BindError, is_binding, resolve_from, source_doc

# The binding keys that select and shape, as opposed to naming a source. These are authored in the
# page YAML and never reach dist/ today, so they are the half that is target-gated: a `where:`
# clause naming a person would otherwise start being published.
FILTER_KEYS = ("where", "sort", "limit", "group_by", "map", "fields")

# A phrase per virtual root, since `source_doc` returns None for all of them and there is no file
# to name. Five of this repository's pages headline on one of these.
VIRTUAL_ROOTS = {
    "docs": "every structured doc in this build",
    "pages": "every page in this build",
    "prefabs": "the prefab registry",
    "collections": "the collections in this build",
    "evidence": "the evidence claims this build checked",
    "build": "this build's own metadata",
    "site": "site.yaml",
}


@dataclass
class Attribution:
    """What one section can say about where it came from."""

    text: str = ""
    doc: str | None = None          # global doc id, when a single doc is the answer
    src: str | None = None          # that doc's `_source`, a repo-relative path
    secondary: list[str] = field(default_factory=list)  # scalar bindings folded in as a second line

    def __bool__(self) -> bool:
        return bool(self.text)

    def full(self) -> str:
        return " · ".join([self.text, *self.secondary]) if self.secondary else self.text


def binding_specs(sec: dict) -> list[tuple[str | None, dict]]:
    """Every binding a section declares, as (arg name or None, spec).

    Scans the section spec rather than hooking `Renderer._bind`, which is not a chokepoint: it is
    reached only from the markdown and prefab handlers, so `dynamic`, `include`, `embed` and `html`
    - four of the six section types - would contribute nothing. Looking at the YAML instead gives
    every type one place to say what it has.
    """
    out: list[tuple[str | None, dict]] = []
    if "from" in sec and isinstance(sec["from"], str):
        out.append((None, {k: v for k, v in sec.items() if k in BINDING_KEYS}))
    for name, value in (sec.get("args") or {}).items():
        if is_binding(value):
            out.append((name, value))
    return out


def _doc_label(doc: dict | None, doc_id: str | None) -> str:
    """What to print for a doc: its file when a reader could open it, else its id.

    A hook-generated doc's `_source` is a real string but not a path - `hooks.py:load_docs()[0]` -
    so it is shown as provenance rather than offered as a file.
    """
    src = (doc or {}).get("_source")
    if isinstance(src, str) and src:
        return src
    return doc_id or "an unnamed source"


def _list_field(expr: str) -> str | None:
    """`plan.milestones` -> `milestones`; `design.$items` and bare doc ids have no field."""
    _, _, rest = expr.partition(".")
    if not rest or rest == ITEMS_SUFFIX:
        return None
    return rest


def _is_list_binding(spec: dict, ctx: dict) -> bool:
    """Whether a binding produces the list a region is *of*, as opposed to a scalar beside it.

    Resolved against the docs when they are available, because the expression alone cannot tell
    `design.components` from `design.overview`. A binding carrying a list op is treated as a list
    either way - `where`/`sort`/`limit` are meaningless on a scalar and `resolve_binding` would
    already have refused it.
    """
    if any(key in spec for key in ("where", "sort", "limit", "group_by", "fields")):
        return True
    try:
        return isinstance(resolve_from(str(spec.get("from", "")), ctx), (list, dict))
    except (BindError, KeyError, TypeError):
        return bool(_list_field(str(spec.get("from", ""))))


def _where_phrase(where: dict) -> str:
    parts = []
    for key, cond in where.items():
        if isinstance(cond, dict):
            parts.append(f"{key} {', '.join(f'{op} {v}' for op, v in cond.items())}")
        elif isinstance(cond, list):
            parts.append(f"{key} is one of {', '.join(str(v) for v in cond)}")
        else:
            parts.append(f"{key} = {cond}")
    return " and ".join(parts)


def describe(
    spec: dict,
    docs: dict | None = None,
    collection: str | None = None,
    counts: tuple[int, int] | None = None,
    detail: bool = True,
) -> str:
    """One binding as a sentence.

    `counts` is (shown, candidates) if the caller has them. The candidate count is measured after
    normalisation - a dict-of-dicts becomes a list, and `$items` flattens the type arrays - so it
    means "of 17 candidate items", not "of 17 entries in the file".

    `detail=False` omits the filter clauses. They are authored in page YAML, have never reached
    `dist/`, and one naming a person would start to; the redaction scan is the backstop for that,
    not the mitigation.
    """
    expr = str(spec.get("from", "")).strip()
    if not expr:
        return ""
    ctx = {"docs": docs or {}, "collection": collection}

    if expr.startswith("$"):
        head = expr[1:].partition(".")[0]
        head_phrase = VIRTUAL_ROOTS.get(head, f"${head}")
        rest = expr[1:].partition(".")[2]
        lead = f"{rest} from {head_phrase}" if rest else head_phrase
    else:
        doc = source_doc(expr, ctx)
        doc_id = expr.partition(".")[0].lstrip("/")
        if collection and doc and doc.get("id"):
            doc_id = doc["id"]
        field_name = _list_field(expr)
        where_from = _doc_label(doc, doc_id)
        if field_name == ITEMS_SUFFIX or expr.endswith("." + ITEMS_SUFFIX):
            lead = f"every item in {where_from}"
        elif field_name:
            lead = f"{field_name} in {where_from}"
        else:
            lead = where_from

    clauses = []
    if detail:
        if spec.get("where"):
            clauses.append(f"where {_where_phrase(spec['where'])}")
        if spec.get("group_by"):
            clauses.append(f"grouped by {spec['group_by']}")
        if spec.get("sort"):
            sort = spec["sort"]
            clauses.append(f"sorted by {sort if isinstance(sort, str) else ', '.join(map(str, sort))}")
        if spec.get("limit"):
            clauses.append(f"first {spec['limit']}")
        if spec.get("fields"):
            clauses.append(f"keeping {', '.join(str(f) for f in spec['fields'])}")
        if spec.get("map"):
            clauses.append("renamed for the prefab")

    text = lead + ("" if not clauses else ", " + ", ".join(clauses))
    if counts:
        shown, candidates = counts
        text += f" - showing {shown} of {candidates}" if shown != candidates else f" - {shown} item(s)"
    return text


def attribute(
    sec: dict,
    page: dict,
    docs: dict | None = None,
    collection: str | None = None,
    counts: dict[str | None, tuple[int, int]] | None = None,
    detail: bool = True,
) -> Attribution:
    """The one line a section shows, choosing a headline when it binds more than one thing.

    Order of the headline rule:

    1. Group the bindings by the doc they resolve to. One doc (or one virtual root) is the
       headline, and `$site`/`$build` scalars go on a secondary line rather than being dropped.
    2. Several docs: prefer the list-valued binding, since that is what the region is *of*.
    3. Still ambiguous: name them all and stop. Never pick one silently.

    A section with no binding falls back to what it does have - the file it includes, the live
    source it names, or the page that authored it.
    """
    counts = counts or {}
    specs = binding_specs(sec)
    if not specs:
        return _authored(sec, page)

    ctx = {"docs": docs or {}, "collection": collection}
    grouped: dict[str, list[tuple[str | None, dict]]] = {}
    scalars: list[tuple[str | None, dict]] = []
    for name, spec in specs:
        expr = str(spec.get("from", ""))
        if expr.startswith("$") and expr[1:].partition(".")[0] in ("site", "build"):
            scalars.append((name, spec))
            continue
        doc = source_doc(expr, ctx)
        key = (doc or {}).get("id") or expr.partition(".")[0]
        grouped.setdefault(key, []).append((name, spec))

    if not grouped:                      # every binding was a $site/$build scalar
        grouped, scalars = {s[1]["from"]: [s] for s in scalars}, []

    if len(grouped) > 1:
        listed = [n for n, ss in grouped.items() if any(_is_list_binding(s, ctx) for _, s in ss)]
        if len(listed) == 1:
            chosen = grouped[listed[0]]
        else:
            names = ", ".join(sorted(_doc_label(source_doc(k, ctx), k) for k in grouped))
            return Attribution(text=f"from {len(grouped)} docs: {names}")
    else:
        chosen = next(iter(grouped.values()))

    name, spec = chosen[0]
    text = describe(spec, docs, collection, counts.get(name), detail)
    doc = source_doc(str(spec.get("from", "")), ctx)
    secondary = [describe(s, docs, collection, None, detail) for _, s in scalars]
    return Attribution(
        text=text,
        doc=(doc or {}).get("id"),
        src=(doc or {}).get("_source"),
        secondary=[s for s in secondary if s],
    )


def _authored(sec: dict, page: dict) -> Attribution:
    """Sections with no binding: dynamic, include, embed, html, and literal markdown."""
    stype = sec.get("type")
    if stype == "dynamic" and sec.get("source"):
        return Attribution(text=f"live source `{sec['source']}`, read at build time")
    if stype == "include" and sec.get("path"):
        return Attribution(text=f"the file {sec['path']}, included verbatim", src=str(sec["path"]))
    if stype == "embed" and sec.get("src"):
        return Attribution(text=f"an embedded {sec.get('kind', 'iframe')}: {sec['src']}")
    src = page.get("_source")
    where = f"authored in {src}" if src else "authored on this page"
    sid = sec.get("id")
    return Attribution(text=f"{where}, section {sid}" if sid else where, src=src)
