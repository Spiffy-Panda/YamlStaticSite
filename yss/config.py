"""Site configuration (site.yaml) with defaults, target lookup and redaction lists."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PKG_DIR = Path(__file__).resolve().parent
CONFIG_NAMES = ("site.yaml", "site.yml")

DEFAULTS: dict[str, Any] = {
    "site": {"name": "Untitled site", "description": "", "repo": ""},
    "paths": {
        "docs": "docs",
        "pages": "site/pages",
        "prefabs": "site/prefabs",
        "layouts": "site/layouts",
        "assets": "site/assets",
        "schemas": "schemas",
        "out": "dist",
    },
    "targets": {
        "public": {"base_url": "/", "redact": True, "description": "Public build (GitHub Pages)."},
        "private": {"base_url": "/", "redact": False, "description": "Local-only build with private content."},
    },
    "dynamic": {"sources": {}},
    "serve": {
        "host": "127.0.0.1",
        "private_port": 8800,
        "public_port": 8801,
        "coop_coep": True,
        "live_dynamic": True,
        "dynamic_ttl": 30,
        "watch": [],
    },
    "redaction": {
        "local_file": ".yss/local.yaml",
        "env_forbidden": "YSS_FORBIDDEN_STRINGS",
        "env_flag": "YSS_FLAG_STRINGS",
        "forbid_root_path": True,
    },
}


class ConfigError(Exception):
    pass


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = deep_merge(base[key], value) if key in base else copy.deepcopy(value)
        return out
    return copy.deepcopy(override)


def find_root(start: str | Path | None = None) -> Path:
    cur = Path(start or os.getcwd()).resolve()
    for candidate in (cur, *cur.parents):
        for name in CONFIG_NAMES:
            if (candidate / name).is_file():
                return candidate
    raise ConfigError(f"no site.yaml found in {cur} or any parent directory")


def _split_env(value: str | None) -> list[str]:
    if not value:
        return []
    parts = value.replace("\n", ";").split(";")
    return [p.strip() for p in parts if p.strip()]


class Config:
    def __init__(self, root: Path, data: dict | None, source: Path | None = None):
        self.root = Path(root).resolve()
        self.source = source
        self.raw = data or {}
        self.data = deep_merge(DEFAULTS, self.raw)

    @classmethod
    def load(cls, root: str | Path | None = None) -> "Config":
        root_path = find_root(root) if root is None else Path(root).resolve()
        for name in CONFIG_NAMES:
            candidate = root_path / name
            if candidate.is_file():
                with open(candidate, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                return cls(root_path, data, candidate)
        raise ConfigError(f"no site.yaml in {root_path}")

    # --- paths -----------------------------------------------------------
    def path(self, key: str) -> Path:
        return self.root / self.data["paths"][key]

    def out_dir(self, target: str) -> Path:
        return self.path("out") / target

    def schema_dirs(self) -> list[Path]:
        return [PKG_DIR / "schemas", self.path("schemas")]

    def prefab_dirs(self) -> list[Path]:
        return [PKG_DIR / "prefabs", self.path("prefabs")]

    def watch_paths(self) -> list[Path]:
        paths = [self.source] if self.source else []
        paths += [self.path(k) for k in ("docs", "pages", "prefabs", "layouts", "assets", "schemas")]
        paths += [self.root / p for p in self.serve.get("watch") or []]
        return [p for p in paths if p is not None]

    # --- sections --------------------------------------------------------
    @property
    def site(self) -> dict:
        return self.data["site"]

    @property
    def targets(self) -> dict:
        return self.data["targets"]

    def target(self, name: str) -> dict:
        if name not in self.targets:
            raise ConfigError(f"unknown target '{name}' (known: {', '.join(self.targets)})")
        return self.targets[name]

    def base_url(self, target: str) -> str:
        base = self.target(target).get("base_url") or "/"
        if not base.startswith("/"):
            base = "/" + base
        if not base.endswith("/"):
            base += "/"
        return base

    @property
    def dynamic_sources(self) -> dict:
        return self.data["dynamic"].get("sources") or {}

    def dynamic_sources_for(self, target: str) -> dict:
        out = {}
        for name, spec in self.dynamic_sources.items():
            targets = spec.get("targets") or list(self.targets)
            if target in targets:
                out[name] = spec
        return out

    @property
    def serve(self) -> dict:
        return self.data["serve"]

    # --- redaction -------------------------------------------------------
    def local_data(self) -> dict:
        path = self.root / self.data["redaction"]["local_file"]
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        return {}

    def redaction_lists(self, target: str) -> tuple[list[str], list[str]]:
        """Return (forbidden, flagged) strings for a target.

        Forbidden strings fail a redacting build; flagged strings only warn.
        Sources: .yss/local.yaml (gitignored) and environment variables.
        """
        tcfg = self.target(target)
        local = self.local_data()
        red = self.data["redaction"]
        flags = list(local.get("flag_strings") or []) + _split_env(os.environ.get(red["env_flag"]))
        forbidden: list[str] = []
        if tcfg.get("redact"):
            forbidden = list(local.get("forbidden_strings") or []) + _split_env(os.environ.get(red["env_forbidden"]))
            if red.get("forbid_root_path"):
                forbidden.append(str(self.root))
                forbidden.append(self.root.as_posix())

        def dedupe(values):
            return list(dict.fromkeys(str(s) for s in values if s))

        return dedupe(forbidden), dedupe(flags)
