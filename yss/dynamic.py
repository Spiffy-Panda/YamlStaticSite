"""Dynamic data sources: JSON files the site fetches at runtime (test runs, git log, build info...).

A source is declared in site.yaml under dynamic.sources.<name> with exactly one of:
  provider: "package.module:function"   -> function(cfg, spec) returns JSON-able data
  command:  "shell command"             -> stdout must be JSON
  file:     "relative/path.json"        -> copied as-is
Optional keys: targets [public, private], on_build (bool), timeout (seconds), ttl (seconds).
Every output is wrapped as {"source", "collected_at", "ok", "data" | "error"}.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config


class DynamicError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect(cfg: Config, name: str, spec: dict) -> Any:
    if "provider" in spec:
        module_name, _, func_name = spec["provider"].partition(":")
        if not func_name:
            raise DynamicError(f"source '{name}': provider must look like 'module:function'")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise DynamicError(f"source '{name}': cannot import provider module '{module_name}': {exc}") from exc
        func = getattr(module, func_name, None)
        if func is None:
            raise DynamicError(f"source '{name}': provider '{module_name}' has no function '{func_name}'")
        return func(cfg, spec)
    if "command" in spec:
        proc = subprocess.run(
            spec["command"],
            shell=True,
            cwd=cfg.root,
            capture_output=True,
            text=True,
            timeout=spec.get("timeout", 120),
        )
        if proc.returncode != 0:
            raise DynamicError(f"source '{name}': command failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise DynamicError(f"source '{name}': command output is not JSON: {exc}") from exc
    if "file" in spec:
        path = cfg.root / spec["file"]
        if not path.is_file():
            raise DynamicError(f"source '{name}': file not found: {spec['file']}")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    raise DynamicError(f"source '{name}': needs one of provider/command/file")


def collect_envelope(cfg: Config, name: str, spec: dict) -> dict:
    started = time.time()
    try:
        data = collect(cfg, name, spec)
        return {"source": name, "collected_at": _now(), "ok": True, "seconds": round(time.time() - started, 3), "data": data}
    except Exception as exc:  # noqa: BLE001 - dynamic data must never break the site
        return {"source": name, "collected_at": _now(), "ok": False, "seconds": round(time.time() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


def write_source(cfg: Config, target: str, out_dir: Path, name: str) -> dict:
    sources = cfg.dynamic_sources_for(target)
    if name not in sources:
        raise DynamicError(f"dynamic source '{name}' is not enabled for target '{target}'")
    envelope = collect_envelope(cfg, name, sources[name])
    path = out_dir / "dynamic" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return envelope


def write_all(cfg: Config, target: str, out_dir: Path, names: list[str] | None = None, only_on_build: bool = False) -> dict[str, dict]:
    results = {}
    for name, spec in cfg.dynamic_sources_for(target).items():
        if names and name not in names:
            continue
        if only_on_build and not spec.get("on_build", True):
            continue
        results[name] = write_source(cfg, target, out_dir, name)
    return results


def is_stale(path: Path, ttl: int) -> bool:
    if not path.is_file():
        return True
    return (time.time() - path.stat().st_mtime) > ttl


def python_executable() -> str:
    return sys.executable
