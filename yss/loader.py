"""YAML loading, JSON-Schema validation with data-driven annotations, and reference checking.

Annotations understood in schemas (resolved per collection from site.yaml / collection.yaml):
  x-vocab: <name>      -> enum from `vocabularies.<name>`
  x-limit: <name>      -> maxLength from `limits.<name>`
  x-ref: item | doc    -> value(s) must resolve to an item id (same doc, or `doc/item`) or a doc id
  x-evidence: path | glob | command | symbol -> checked by `yss check` (see evidence.py)

Files and folders whose name starts with `_` are never loaded (archives, scratch).
Inline references `[[doc]]`, `[[doc#item]]`, `[[#item]]` in any string are validated too.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterator

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .config import Collection, Config
from .hooks import HookError, call, load_hooks

SCHEMA_SUFFIX = ".schema.yaml"
INLINE_REF_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")
CODE_SPAN_RE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")
ANNOTATION_KEYS = ("x-ref", "x-evidence")


def strip_code(text: str) -> str:
    """Blank out code spans and fences so references inside them are ignored."""
    return CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)


class LoadError(Exception):
    pass


class _StringDateLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates/timestamps as plain strings (JSON friendly)."""


_StringDateLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
)


def load_yaml(path: Path, where: str | None = None) -> Any:
    with open(path, encoding="utf-8") as fh:
        try:
            return yaml.load(fh, Loader=_StringDateLoader)
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark
            loc = f" line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise LoadError(f"{where or path.name}:{loc}: YAML parse error: {exc.problem}") from exc
        except yaml.YAMLError as exc:
            raise LoadError(f"{where or path.name}: YAML parse error: {exc}") from exc


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _fmt_error(where: str, err) -> str:
    loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{where}: at {loc}: {err.message}"


def _is_hidden(path: Path, base: Path) -> bool:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith("_") for part in parts)


def yaml_files(directory: Path) -> list[Path]:
    """All *.yaml / *.yml under directory, skipping `_`-prefixed files and folders."""
    if not directory.is_dir():
        return []
    files = [
        p for p in directory.rglob("*")
        if p.suffix in (".yaml", ".yml") and p.is_file() and not _is_hidden(p, directory)
    ]
    return sorted(files)


# --- schemas ----------------------------------------------------------------
class SchemaRegistry:
    """Loads *.schema.yaml from a list of dirs (later dirs override earlier ones)."""

    def __init__(self, dirs: list[Path]):
        self.schemas: dict[str, dict] = {}
        self.sources: dict[str, Path] = {}
        self._resolved: dict[str, dict] = {}
        self._annotations: dict[str, dict] = {}
        for d in dirs:
            if not d or not d.is_dir():
                continue
            for p in sorted(d.rglob(f"*{SCHEMA_SUFFIX}")):
                name = p.name[: -len(SCHEMA_SUFFIX)]
                self.schemas[name] = load_yaml(p)
                self.sources[name] = p
        self._fold_envelope()

    def _fold_envelope(self) -> None:
        """Merge envelope properties/$defs into each doc.<kind> schema so kinds can be strict."""
        env = self.schemas.get("doc.envelope")
        if not env:
            return
        for name, schema in self.schemas.items():
            if not name.startswith("doc.") or name == "doc.envelope":
                continue
            props = schema.setdefault("properties", {})
            for key, value in env.get("properties", {}).items():
                props.setdefault(key, copy.deepcopy(value))
            defs = schema.setdefault("$defs", {})
            for key, value in env.get("$defs", {}).items():
                defs.setdefault(key, copy.deepcopy(value))
            pp = schema.setdefault("patternProperties", {})
            for key, value in env.get("patternProperties", {}).items():
                pp.setdefault(key, copy.deepcopy(value))

    def names(self) -> list[str]:
        return sorted(self.schemas)

    def doc_kinds(self) -> list[str]:
        return sorted(n[4:] for n in self.schemas if n.startswith("doc.") and n != "doc.envelope")

    def get(self, name: str) -> dict:
        if name not in self.schemas:
            raise LoadError(f"unknown schema '{name}' (known: {', '.join(self.names())})")
        return self.schemas[name]

    def resolved(self, name: str, vocab: dict | None = None, limits: dict | None = None) -> dict:
        """Deep copy of a schema with x-vocab / x-limit resolved from the given tables."""
        vocab = vocab or {}
        limits = limits or {}
        key = json.dumps([name, vocab, limits], sort_keys=True, default=str)
        if key in self._resolved:
            return self._resolved[key]
        schema = copy.deepcopy(self.get(name))
        for node in _walk_nodes(schema):
            if "x-vocab" in node:
                vname = node["x-vocab"]
                if vname not in vocab:
                    raise LoadError(f"schema '{name}' uses x-vocab '{vname}' which is not defined in vocabularies")
                node["enum"] = list(vocab[vname])
            if "x-limit" in node:
                lname = node["x-limit"]
                if lname in limits and limits[lname]:
                    node["maxLength"] = int(limits[lname])
        self._resolved[key] = schema
        return schema

    def validate(self, instance: Any, name: str, where: str, vocab: dict | None = None, limits: dict | None = None) -> list[str]:
        validator = Draft202012Validator(self.resolved(name, vocab, limits), format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        return [_fmt_error(where, e) for e in errors]

    def annotations(self, name: str) -> dict[str, dict[str, str]]:
        """{"ref": {field: item|doc}, "evidence": {field: path|glob|command|symbol}} for a schema."""
        if name in self._annotations:
            return self._annotations[name]
        found: dict[str, dict[str, str]] = {"ref": {}, "evidence": {}}
        if name in self.schemas:
            for props in _walk_properties(self.schemas[name]):
                for field, sub in props.items():
                    if not isinstance(sub, dict):
                        continue
                    for node in (sub, sub.get("items") if isinstance(sub.get("items"), dict) else {}):
                        if "x-ref" in node:
                            found["ref"][field] = node["x-ref"]
                        if "x-evidence" in node:
                            found["evidence"][field] = node["x-evidence"]
        self._annotations[name] = found
        return found


def _walk_nodes(schema: Any) -> Iterator[dict]:
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk_nodes(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_nodes(value)


def _walk_properties(schema: Any) -> Iterator[dict]:
    for node in _walk_nodes(schema):
        if isinstance(node.get("properties"), dict):
            yield node["properties"]


# --- ids and references -----------------------------------------------------
def index_ids(doc: dict) -> tuple[dict[str, dict], list[str]]:
    """Map every list item id in a doc to the item. Duplicate ids are errors."""
    index: dict[str, dict] = {}
    dupes: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, sub in value.items():
                walk(sub, f"{path}/{key}" if path else key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    if item["id"] in index:
                        dupes.append(f"{path}/{i}/id: duplicate item id '{item['id']}'")
                    else:
                        index[item["id"]] = item
                walk(item, f"{path}/{i}")

    for key, value in doc.items():
        if key.startswith("_"):
            continue
        walk(value, key)
    return index, dupes


def resolve_doc_id(ref: str, collection_id: str | None, docs: dict) -> str | None:
    """A doc reference is collection-local first (`plan` inside `x` -> `x/plan`), then exact; `/plan` forces the root doc."""
    if ref.startswith("/"):
        return ref[1:] if ref[1:] in docs else None
    if collection_id and f"{collection_id}/{ref}" in docs:
        return f"{collection_id}/{ref}"  # collection-local wins over a root doc with the same name
    if ref in docs:
        return ref
    return None


def parse_ref(text: str) -> tuple[str | None, str | None]:
    """'doc#item' -> (doc, item); 'doc' -> (doc, None); '#item' -> (None, item)."""
    text = text.strip()
    if "#" in text:
        doc, _, item = text.partition("#")
        return (doc.strip() or None), (item.strip() or None)
    return text or None, None


def iter_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            yield from iter_strings(sub, f"{path}/{key}" if path else key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from iter_strings(item, f"{path}/{i}")


def _check_one_ref(value: str, ref_type: str, doc: dict, index: dict, docs: dict, indexes: dict) -> str | None:
    cid = doc.get("_collection") or None
    if ref_type == "doc":
        return None if resolve_doc_id(value, cid, docs) else f"unknown doc '{value}'"
    # item: same doc, or doc/item
    if value in index:
        return None
    if "/" in value:
        doc_ref, _, item = value.rpartition("/")
        target = resolve_doc_id(doc_ref, cid, docs)
        if target is None:
            # maybe the whole thing is a collection-qualified doc id without an item
            return f"unknown doc '{doc_ref}' in reference '{value}'"
        if item in indexes.get(target, {}):
            return None
        return f"doc '{target}' has no item '{item}'"
    return f"no item '{value}' in this doc (use doc/item for other docs)"


def check_refs(docs: dict[str, dict], reg: SchemaRegistry, pages: list[dict] | None = None) -> list[str]:
    errors: list[str] = []
    indexes: dict[str, dict] = {}
    for doc_id, doc in docs.items():
        index, dupes = index_ids(doc)
        indexes[doc_id] = index
        errors += [f"{doc['_source']}: at {d}" for d in dupes]
    for doc_id, doc in docs.items():
        ann = reg.annotations(f"doc.{doc.get('kind')}")["ref"]
        index = indexes[doc_id]

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, sub in value.items():
                    if isinstance(key, str) and key.startswith("_"):
                        continue
                    here = f"{path}/{key}" if path else key
                    if key in ann and sub is not None:
                        values = sub if isinstance(sub, list) else [sub]
                        for i, v in enumerate(values):
                            if not isinstance(v, str):
                                continue
                            problem = _check_one_ref(v, ann[key], doc, index, docs, indexes)
                            if problem:
                                loc = f"{here}/{i}" if isinstance(sub, list) else here
                                errors.append(f"{doc['_source']}: at {loc}: {problem}")
                    walk(sub, here)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    walk(item, f"{path}/{i}")

        walk(doc, "")
        errors += _check_inline(doc, doc["_source"], doc_id, doc.get("_collection"), docs, indexes)
    for page in pages or []:
        errors += _check_inline(page, page["_source"], None, page.get("_collection"), docs, indexes)
    return errors


def _check_inline(obj: dict, where: str, own_doc: str | None, cid: str | None, docs: dict, indexes: dict) -> list[str]:
    errors = []
    for path, text in iter_strings(obj):
        if "[[" not in text:
            continue
        for match in INLINE_REF_RE.finditer(strip_code(text)):
            doc_ref, item = parse_ref(match.group(1))
            target = own_doc if doc_ref is None else resolve_doc_id(doc_ref, cid, docs)
            if target is None:
                errors.append(f"{where}: at {path}: inline reference [[{match.group(1)}]] names an unknown doc")
                continue
            if item and item not in indexes.get(target, {}):
                errors.append(f"{where}: at {path}: inline reference [[{match.group(1)}]]: doc '{target}' has no item '{item}'")
    return errors


def find_inline_refs(text: str) -> list[tuple[str, str | None, str | None, str | None]]:
    """[(raw, doc, item, label)] for every [[...]] in text."""
    out = []
    for match in INLINE_REF_RE.finditer(strip_code(text)):
        doc, item = parse_ref(match.group(1))
        out.append((match.group(0), doc, item, match.group(2)))
    return out


# --- loading ----------------------------------------------------------------
def _rel(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.root).as_posix()
    except ValueError:
        return path.name


def _collection_items(cfg: Config, collection: Collection, directory: Path, hook_name: str) -> tuple[list[tuple[str, dict]], list[str]]:
    """(where, data) pairs from YAML files plus the collection's hook, if any."""
    items: list[tuple[str, dict]] = []
    errors: list[str] = []
    for path in yaml_files(directory):
        where = _rel(cfg, path)
        try:
            data = load_yaml(path, where)
        except LoadError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(data, dict):
            errors.append(f"{where}: top level must be a mapping")
            continue
        data.setdefault("id", path.stem)
        items.append((where, data))
    try:
        hooks = load_hooks(collection.hooks_path, cfg.root)
        extra = call(hooks, hook_name, collection.summary() | {"root": _rel(cfg, collection.root)}, cfg, default=[])
    except HookError as exc:
        errors.append(str(exc))
        extra = []
    hook_where = _rel(cfg, collection.hooks_path) if collection.hooks_path else "hooks"
    for i, data in enumerate(extra or []):
        if not isinstance(data, dict):
            errors.append(f"{hook_where}: {hook_name}() item {i} is not a mapping")
            continue
        data.setdefault("id", f"{hook_name.replace('load_', '')}-{i + 1}")
        items.append((f"{hook_where}:{hook_name}()[{i}]", data))
    return items, errors


def load_docs(cfg: Config, reg: SchemaRegistry) -> tuple[dict[str, dict], list[str]]:
    docs: dict[str, dict] = {}
    errors: list[str] = []
    kinds = reg.doc_kinds()
    for collection in cfg.collections():
        vocab, limits = collection.vocab(cfg), collection.limits(cfg)
        items, errs = _collection_items(cfg, collection, collection.docs_dir, "load_docs")
        errors += errs
        for where, data in items:
            local_id = str(data.get("id"))
            kind = data.get("kind")
            errs = reg.validate(data, "doc.envelope", where, vocab, limits)
            if kind and f"doc.{kind}" in reg.schemas:
                errs += reg.validate(data, f"doc.{kind}", where, vocab, limits)
            elif kind:
                errs.append(f"{where}: unknown doc kind '{kind}' (known kinds: {', '.join(kinds)})")
            if errs:
                errors.extend(errs)
                continue
            doc_id = collection.doc_id(local_id)
            if doc_id in docs:
                errors.append(f"{where}: duplicate doc id '{doc_id}' (also in {docs[doc_id]['_source']})")
                continue
            data["id"] = doc_id
            data["_local_id"] = local_id
            data["_collection"] = collection.id
            data["_source"] = where
            docs[doc_id] = data
    return docs, errors


def _prefix_route(collection: Collection, route: str) -> str:
    if collection.is_root:
        return route
    return collection.route_prefix.rstrip("/") + route


def load_pages(cfg: Config, reg: SchemaRegistry) -> tuple[list[dict], list[str]]:
    pages: list[dict] = []
    errors: list[str] = []
    routes: dict[str, str] = {}
    for collection in cfg.collections():
        items, errs = _collection_items(cfg, collection, collection.pages_dir, "load_pages")
        errors += errs
        for where, data in items:
            local_id = str(data.get("id"))
            data.setdefault("route", "/" if local_id == "index" else f"/{local_id}/")
            errs = reg.validate(data, "page", where)
            if errs:
                errors.extend(errs)
                continue
            data["route"] = _prefix_route(collection, data["route"])
            if data["route"] in routes:
                errors.append(f"{where}: route '{data['route']}' already used by {routes[data['route']]}")
                continue
            routes[data["route"]] = where
            data["id"] = local_id if collection.is_root else f"{collection.id}/{local_id}"
            data["_local_id"] = local_id
            data["_collection"] = collection.id
            data["_source"] = where
            if not collection.is_root:
                nav = data.setdefault("nav", {})
                nav.setdefault("group", collection.title)
            pages.append(data)
    pages.sort(key=lambda p: ((p.get("nav") or {}).get("order", 100), p["title"]))
    return pages, errors


def load_prefabs(cfg: Config, reg: SchemaRegistry) -> tuple[dict[str, dict], list[str]]:
    prefabs: dict[str, dict] = {}
    errors: list[str] = []
    dirs = cfg.prefab_dirs()
    builtin_dir = dirs[0]
    for directory in dirs:
        for path in yaml_files(directory):
            rel = f"yss/prefabs/{path.name}" if directory == builtin_dir else _rel(cfg, path)
            try:
                data = load_yaml(path, rel)
            except LoadError as exc:
                errors.append(str(exc))
                continue
            if not isinstance(data, dict):
                errors.append(f"{rel}: top level must be a mapping")
                continue
            data.setdefault("name", path.stem)
            errs = reg.validate(data, "prefab", rel)
            if errs:
                errors.extend(errs)
                continue
            data["_source"] = rel
            data["_builtin"] = directory == builtin_dir
            prefabs[data["name"]] = data  # later dirs override built-ins
    return prefabs, errors


def load_collection_configs(cfg: Config, reg: SchemaRegistry) -> list[str]:
    """Validate each collection.yaml against the collection schema and run configure() hooks."""
    errors: list[str] = []
    if "collection" not in reg.schemas:
        return errors
    for collection in cfg.collections():
        if collection.is_root:
            continue
        where = _rel(cfg, collection.root) + "/collection.yaml"
        try:
            hooks = load_hooks(collection.hooks_path, cfg.root)
            updated = call(hooks, "configure", dict(collection.data), cfg, default=None)
            if isinstance(updated, dict):
                collection.data = updated
        except HookError as exc:
            errors.append(str(exc))
        errors += reg.validate(collection.data, "collection", where, collection.vocab(cfg), collection.limits(cfg))
    return errors
