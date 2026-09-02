"""YAML loading and JSON-Schema validation for docs, pages, prefabs and site config."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .config import Config

SCHEMA_SUFFIX = ".schema.yaml"


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


class SchemaRegistry:
    """Loads *.schema.yaml from a list of dirs (later dirs override earlier ones)."""

    def __init__(self, dirs: list[Path]):
        self.schemas: dict[str, dict] = {}
        self.sources: dict[str, Path] = {}
        for d in dirs:
            if not d.is_dir():
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

    def validate(self, instance: Any, name: str, where: str) -> list[str]:
        validator = Draft202012Validator(self.get(name), format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        return [_fmt_error(where, e) for e in errors]


def _rel(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.root).as_posix()
    except ValueError:
        return path.name


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [p for p in directory.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file()]
    return sorted(files)


def load_docs(cfg: Config, reg: SchemaRegistry) -> tuple[dict[str, dict], list[str]]:
    docs: dict[str, dict] = {}
    errors: list[str] = []
    kinds = reg.doc_kinds()
    for path in _yaml_files(cfg.path("docs")):
        rel = _rel(cfg, path)
        try:
            data = load_yaml(path, rel)
        except LoadError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(data, dict):
            errors.append(f"{rel}: top level must be a mapping")
            continue
        data.setdefault("id", path.stem)
        kind = data.get("kind")
        errs = reg.validate(data, "doc.envelope", rel)
        if kind and f"doc.{kind}" in reg.schemas:
            errs += reg.validate(data, f"doc.{kind}", rel)
        elif kind:
            errs.append(f"{rel}: unknown doc kind '{kind}' (known kinds: {', '.join(kinds)})")
        if errs:
            errors.extend(errs)
            continue
        if data["id"] in docs:
            errors.append(f"{rel}: duplicate doc id '{data['id']}' (also in {docs[data['id']]['_source']})")
            continue
        data["_source"] = rel
        docs[data["id"]] = data
    return docs, errors


def load_pages(cfg: Config, reg: SchemaRegistry) -> tuple[list[dict], list[str]]:
    pages: list[dict] = []
    errors: list[str] = []
    routes: dict[str, str] = {}
    for path in _yaml_files(cfg.path("pages")):
        rel = _rel(cfg, path)
        try:
            data = load_yaml(path, rel)
        except LoadError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(data, dict):
            errors.append(f"{rel}: top level must be a mapping")
            continue
        data.setdefault("id", path.stem)
        data.setdefault("route", "/" if data["id"] == "index" else f"/{data['id']}/")
        errs = reg.validate(data, "page", rel)
        if errs:
            errors.extend(errs)
            continue
        if data["route"] in routes:
            errors.append(f"{rel}: route '{data['route']}' already used by {routes[data['route']]}")
            continue
        routes[data["route"]] = rel
        data["_source"] = rel
        pages.append(data)
    pages.sort(key=lambda p: ((p.get("nav") or {}).get("order", 100), p["title"]))
    return pages, errors


def load_prefabs(cfg: Config, reg: SchemaRegistry) -> tuple[dict[str, dict], list[str]]:
    prefabs: dict[str, dict] = {}
    errors: list[str] = []
    builtin_dir = cfg.prefab_dirs()[0]
    for directory in cfg.prefab_dirs():
        for path in _yaml_files(directory):
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
