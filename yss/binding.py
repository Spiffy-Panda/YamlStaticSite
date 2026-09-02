"""Data binding: resolve `from/where/sort/limit/map/group_by` specs against loaded docs."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable

BINDING_KEYS = ("from", "where", "sort", "limit", "map", "group_by", "fields")


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


def resolve_from(expr: str, ctx: dict) -> Any:
    """`plan.milestones` -> docs['plan']['milestones']; `$docs`, `$pages`, `$site` are virtual roots."""
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
        }
        if head not in roots:
            raise BindError(f"unknown virtual root '${head}' (use $docs, $pages, $site, $prefabs, $collections, $evidence)")
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


def group_items(items: list, key: str) -> list[dict]:
    groups: "OrderedDict[Any, list]" = OrderedDict()
    for item in items:
        gk = get_path_or_none(item, key) if isinstance(item, dict) else None
        groups.setdefault(gk, []).append(item)
    return [{"key": k, "items": v} for k, v in groups.items()]


def resolve_binding(spec: dict, ctx: dict, render: Callable[[str, dict], str] | None = None) -> Any:
    unknown = set(spec) - set(BINDING_KEYS)
    if unknown:
        raise BindError(
            f"binding has unknown keys: {', '.join(sorted(unknown))} (allowed: {', '.join(BINDING_KEYS)})"
        )
    value = resolve_from(spec["from"], ctx)
    list_ops = [k for k in ("where", "sort", "limit", "map", "group_by", "fields") if k in spec]
    if not list_ops:
        return value
    if isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
        value = [dict(v, id=v.get("id", k)) for k, v in value.items()]
    if not isinstance(value, list):
        raise BindError(
            f"'{spec['from']}' resolves to {type(value).__name__}, but {', '.join(list_ops)} need a list"
        )
    items = list(value)
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
        items = group_items(items, spec["group_by"])
    return items
