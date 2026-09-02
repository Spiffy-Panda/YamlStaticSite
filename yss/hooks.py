"""Hook scripts: a `hooks.py` next to site.yaml (site.yaml: hooks: path) or inside a collection.

A hook module may define any of these functions; all are optional.

    configure(collection: dict, cfg) -> dict | None      # adjust collection.yaml data before loading
    load_docs(collection: dict, cfg) -> list[dict]        # extra docs (dicts with kind/title...), e.g. generated from data files
    load_pages(collection: dict, cfg) -> list[dict]       # extra pages
    markdown(text: str) -> str                            # custom markdown renderer for this collection
    before_render(cfg, target, collection: dict) -> None  # e.g. build a playable from source
    after_build(cfg, target, out_dir, collection: dict) -> None   # e.g. copy generated artefacts into out_dir
    providers = {"name": callable(cfg, spec)}             # dynamic sources referenced as provider: hooks:<name>

Hooks run in-process with the repo root on sys.path. Keep them small and deterministic.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


class HookError(Exception):
    pass


_cache: dict[str, ModuleType] = {}


def load_hooks(path: Path | None, root: Path | None = None) -> ModuleType | None:
    if path is None or not Path(path).is_file():
        return None
    key = str(Path(path).resolve())
    if key in _cache:
        return _cache[key]
    if root and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    name = "yss_hooks_" + "".join(ch if ch.isalnum() else "_" for ch in key)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HookError(f"cannot load hooks from {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - author error, report where
        raise HookError(f"{path}: {type(exc).__name__}: {exc}") from exc
    _cache[key] = module
    return module


def call(hooks: ModuleType | None, name: str, *args: Any, default: Any = None) -> Any:
    if hooks is None:
        return default
    func = getattr(hooks, name, None)
    if func is None or not callable(func):
        return default
    try:
        result = func(*args)
    except Exception as exc:  # noqa: BLE001
        raise HookError(f"hook {name}() in {getattr(hooks, '__file__', '?')}: {type(exc).__name__}: {exc}") from exc
    return default if result is None else result


def provider(hooks: ModuleType | None, name: str):
    table = getattr(hooks, "providers", None) if hooks else None
    if isinstance(table, dict) and callable(table.get(name)):
        return table[name]
    return None
