"""Data binding: resolve `from/where/sort/limit/map/group_by` specs against loaded docs."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable

BINDING_KEYS = ("from", "where", "sort", "limit", "map", "group_by", "fields")

ITEMS_SUFFIX = "$items"

# Envelope lists every doc kind shares. They describe the doc, not its subject matter, so `$items`
# steps over them: `groups:` is the vocabulary `group_by` resolves against, and `links`, `evidence`,
# `tags`, `owners` and `related` are metadata a reader never wants mixed in with the real items.
ENVELOPE_LISTS = ("groups", "links", "evidence", "tags", "owners", "related")


class BindError(Exception):
    pass


def is_binding(value: Any) -> bool:
    return isinstance(value, dict) and "from" in value and isinstance(value["from"], str)


def get_path(obj: Any, path: str) -> Any:
    """Dotted path lookup; list indexes are digits. Raises KeyError when missing."""
    cur = obj
    if not path:
        return cur
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(part)
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(part)
    return cur


def get_path_or_none(obj: Any, path: str) -> Any:
    try:
        return get_path(obj, path)
    except KeyError:
        return None


def doc_items(doc: dict) -> list[dict]:
    """Every item in every type array of one doc, flattened, each tagged with the array it came from.

    `design.$items` is one list of principles *and* components *and* constraints, so a single
    binding can group across them (gh-12). Each item is a copy carrying `_type: <key>`, which
    follows the existing `_`-prefixed metadata convention (`_collection`, `_evidence`) and so is
    filterable (`where: {_type: [principles, constraints]}`) and mappable (`map: {badge: _type}`) -
    an item never loses track of which array it belongs to.

    Skipped: `_`-prefixed loader metadata, the shared envelope lists (`ENVELOPE_LISTS`), and any
    value that is not a list of mappings. Top-level key order is the doc's authored order, so the
    flattening is stable and reviewable.
    """
    out: list[dict] = []
    for key, value in doc.items():
        if key.startswith("_") or key in ENVELOPE_LISTS:
            continue
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(item, dict) for item in value):
            continue
        out += [dict(item, _type=key, **_source_stamp(doc)) for item in value]
    return out


def _source_stamp(doc: dict | None) -> dict:
    """`_src` and `_doc` for items drawn from `doc`, or nothing for a virtual root (gh-30).

    Reserved now rather than when per-item attribution ships, because `prefab()` copies every arg
    key into the template namespace and only type-checks *declared* params - so a prefab written
    before the names exist either happens to be compatible or is not, depending on a convention
    nobody has written down. `card` already reads `_evidence` off items that never declared it.

    Same `_`-prefix item-metadata convention as `_type`, and stamped the same way: onto a copy.
    The doc's own lists must not be mutated - the build dumps them to `data/docs/<id>.json` after
    rendering, so an in-place stamp would put `_src` in every export.
    """
    if not isinstance(doc, dict):
        return {}
    stamp = {}
    if doc.get("_source"):
        stamp["_src"] = doc["_source"]
    if doc.get("id"):
        stamp["_doc"] = doc["id"]
    return stamp


def source_doc(expr: str, ctx: dict) -> dict | None:
    """The doc a `from:` expression reads, or None for a virtual root or an unknown doc.

    Used to find authored `groups:` for `group_by`; never raises, because a `from` that cannot be
    resolved is reported by resolve_from with a much better message.
    """
    expr = expr.strip()
    if expr.startswith("$"):
        return None
    doc_id = expr.partition(".")[0]
    docs = ctx.get("docs") or {}
    collection = ctx.get("collection")
    if doc_id.startswith("/"):
        doc_id = doc_id[1:]
    elif collection and f"{collection}/{doc_id}" in docs:
        doc_id = f"{collection}/{doc_id}"
    doc = docs.get(doc_id)
    return doc if isinstance(doc, dict) else None


def resolve_from(expr: str, ctx: dict) -> Any:
    """`plan.milestones` -> docs['plan']['milestones']; `$docs`, `$pages`, `$site`, `$build` are virtual roots.

    One doc-local virtual root: `design.$items` is every item of every type array in that doc,
    each carrying `_type` (see `doc_items`). It exists so a `group_by` can span type arrays.
    """
    expr = expr.strip()
    if expr.startswith("$"):
        head, _, rest = expr[1:].partition(".")
        roots = {
            "docs": lambda: list(ctx["docs"].values()),
            "pages": lambda: list(ctx["pages"]),
            "site": lambda: ctx["site"],
            "prefabs": lambda: list(ctx.get("prefabs", {}).values()),
            "collections": lambda: list(ctx.get("collections") or []),
            "evidence": lambda: list(ctx.get("evidence") or []),
            "build": lambda: ctx.get("build") or {},
        }
        if head not in roots:
            raise BindError(f"unknown virtual root '${head}' (use $docs, $pages, $site, $prefabs, $collections, $evidence, $build)")
        base = roots[head]()
        try:
            return get_path(base, rest)
        except KeyError as exc:
            raise BindError(f"path '{expr}': no field '{exc.args[0]}'") from exc
    doc_id, _, rest = expr.partition(".")
    docs = ctx["docs"]
    collection = ctx.get("collection")
    if doc_id.startswith("/"):
        doc_id = doc_id[1:]  # `/plan` forces the root doc from inside a collection
    elif collection and f"{collection}/{doc_id}" in docs:
        doc_id = f"{collection}/{doc_id}"  # collection-local wins over a root doc with the same name
    if doc_id not in docs:
        if doc_id in ctx.get("all_doc_ids", ()):
            hint = "it is private in this target; mark the section `visibility: private`"
        else:
            hint = "misspelled?"
        raise BindError(f"unknown doc '{doc_id}' ({hint}); visible docs: {', '.join(sorted(docs)) or 'none'}")
    if rest == ITEMS_SUFFIX:
        return doc_items(docs[doc_id])
    if rest.startswith(ITEMS_SUFFIX + "."):
        raise BindError(
            f"path '{expr}': '{ITEMS_SUFFIX}' is a whole-doc root, not a field to index into; "
            f"write '{doc_id}.{ITEMS_SUFFIX}' and select with where/sort/limit"
        )
    try:
        return get_path(docs[doc_id], rest)
    except KeyError as exc:
        raise BindError(f"path '{expr}': doc '{doc_id}' has no field '{exc.args[0]}'") from exc


def match(item: Any, where: dict) -> bool:
    if not isinstance(item, dict):
        return False
    for key, cond in where.items():
        value = get_path_or_none(item, key)
        if isinstance(cond, dict):
            if "not" in cond:
                bad = cond["not"]
                if (value in bad) if isinstance(bad, list) else (value == bad):
                    return False
            if "contains" in cond:
                if not (isinstance(value, (list, str)) and cond["contains"] in value):
                    return False
            if "exists" in cond:
                if (value is not None) != bool(cond["exists"]):
                    return False
            if "gte" in cond and not (value is not None and value >= cond["gte"]):
                return False
            if "lte" in cond and not (value is not None and value <= cond["lte"]):
                return False
        elif isinstance(cond, list):
            if value not in cond:
                return False
        elif value != cond:
            return False
    return True


def _sort_key(value: Any):
    if value is None:
        return (1, 0, "")
    if isinstance(value, bool):
        return (0, 1, str(value))
    if isinstance(value, (int, float)):
        return (0, 0, value)
    return (0, 1, str(value).lower())


def sort_items(items: list, sort: str | list[str]) -> list:
    keys = [sort] if isinstance(sort, str) else list(sort)
    out = list(items)
    for key in reversed(keys):
        desc = key.startswith("-")
        field = key.lstrip("-+")
        out.sort(
            key=lambda it: _sort_key(get_path_or_none(it, field) if isinstance(it, dict) else it),
            reverse=desc,
        )
    return out


def map_items(items: list, mapping: dict, render: Callable[[str, dict], str] | None) -> list:
    out = []
    for item in items:
        new = dict(item) if isinstance(item, dict) else {"value": item}
        for key, src in mapping.items():
            if isinstance(src, str) and ("{{" in src or "{%" in src):
                if render is None:
                    raise BindError("template expressions in map need a renderer")
                new[key] = render(src, new)
            elif isinstance(src, str):
                new[key] = get_path_or_none(new, src)
            else:
                new[key] = src
        out.append(new)
    return out


def group_items(items: list, key: str, defs: Any = None) -> list[dict]:
    """Bucket items by a field.

    Without `defs` this is the original behaviour: `[{key, items}]` in order of first appearance.

    With `defs` - the source doc's authored `groups:` list - every bucket whose key names a declared
    group also carries that group's own fields (`title`, `blurb`, `notes`, `tags`, ...), and the
    declared order becomes the display order, with undeclared keys appended in order of first
    appearance. That is how a heading and a paragraph written once in the doc reach the page: the
    prefab reads `g.title` / `g.blurb` instead of rendering a bare key.
    """
    groups: "OrderedDict[Any, list]" = OrderedDict()
    for item in items:
        gk = get_path_or_none(item, key) if isinstance(item, dict) else None
        groups.setdefault(gk, []).append(item)
    declared: "OrderedDict[Any, dict]" = OrderedDict()
    for g in defs or []:
        if isinstance(g, dict) and isinstance(g.get("id"), str) and g["id"] not in declared:
            declared[g["id"]] = g
    order = [k for k in declared if k in groups] + [k for k in groups if k not in declared]
    return [
        {**declared.get(k, {}), "key": k, "items": groups[k]}
        for k in order
    ]


def _group_collapse_warning(spec: dict, items: list, groups: list[dict]) -> str | None:
    """The message for a `group_by` that bucketed everything under `None`, or None when it did not.

    A collapsed grouping is not an error - a doc may legitimately have items with no group yet -
    but when *every* item lands in the `None` bucket the page silently renders one unnamed section
    instead of the authored groups, and the cause is almost always an earlier list op that removed
    the field. `fields:` is the one that does it (it runs before `group_by` and rebuilds each item
    from the named keys only); `map:` does not, because `map_items` starts from `dict(item)`.
    """
    if not items or len(groups) != 1 or groups[0].get("key") is not None:
        return None
    key = spec["group_by"]
    message = (
        f"group_by '{key}' put all {len(items)} item(s) from '{spec['from']}' into a single "
        f"unnamed bucket: no item has a '{key}' field, so the authored groups are not rendered"
    )
    if "fields" in spec:
        listed = ", ".join(str(f) for f in spec["fields"])
        message += (
            f" - `fields: [{listed}]` runs before `group_by` and dropped it;"
            f" add '{key}' to `fields:` (or drop `fields:`)"
        )
    return message


def _stamped(value: Any, stamp: dict) -> Any:
    """Copy each dict item with `_src`/`_doc` added; anything else passes through untouched."""
    if not stamp or not isinstance(value, list):
        return value
    return [dict(it, **stamp) if isinstance(it, dict) else it for it in value]


def resolve_binding(
    spec: dict,
    ctx: dict,
    render: Callable[[str, dict], str] | None = None,
    warn: Callable[[str], None] | None = None,
    stats: dict | None = None,
) -> Any:
    """Resolve a binding. Pass `stats` to learn how much was selected out of how much (gh-29).

    It is filled with `candidates` (the item count *after* normalisation - the dict-of-dicts
    conversion and `$items` flattening both change it, so it means "candidate items", not "entries
    in the file") and `shown` (what survived the list ops). Attribution needs both and the numbers
    are free here; recomputing them would mean resolving the binding twice.
    """
    unknown = set(spec) - set(BINDING_KEYS)
    if unknown:
        raise BindError(
            f"binding has unknown keys: {', '.join(sorted(unknown))} (allowed: {', '.join(BINDING_KEYS)})"
        )
    value = resolve_from(spec["from"], ctx)
    stamp = _source_stamp(source_doc(spec["from"], ctx))
    list_ops = [k for k in ("where", "sort", "limit", "map", "group_by", "fields") if k in spec]
    if not list_ops:
        return _stamped(value, stamp)
    if isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
        value = [dict(v, id=v.get("id", k)) for k, v in value.items()]
    if not isinstance(value, list):
        raise BindError(
            f"'{spec['from']}' resolves to {type(value).__name__}, but {', '.join(list_ops)} need a list"
        )
    items = _stamped(list(value), stamp)
    if stats is not None:
        stats["candidates"] = len(items)
    if "where" in spec:
        items = [it for it in items if match(it, spec["where"])]
    if "sort" in spec:
        items = sort_items(items, spec["sort"])
    if "limit" in spec:
        items = items[: int(spec["limit"])]
    if "map" in spec:
        items = map_items(items, spec["map"], render)
    if "fields" in spec:
        items = [
            {k: get_path_or_none(it, k) for k in spec["fields"]} if isinstance(it, dict) else it
            for it in items
        ]
    if "group_by" in spec:
        doc = source_doc(spec["from"], ctx)
        defs = doc.get("groups") if doc else None
        grouped = group_items(items, spec["group_by"], defs if isinstance(defs, list) else None)
        if warn is not None:
            message = _group_collapse_warning(spec, items, grouped)
            if message:
                warn(message)
        items = grouped
    if stats is not None:
        stats["shown"] = sum(len(g.get("items") or []) for g in items) if "group_by" in spec else len(items)
    return items
