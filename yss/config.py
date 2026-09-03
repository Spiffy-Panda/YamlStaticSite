"""Site configuration (site.yaml) with defaults, collections, targets and redaction lists.

Vocabularies and limits are data: schemas carry `x-vocab: <name>` / `x-limit: <name>` annotations
that are resolved from this config, and a collection can override both in its collection.yaml.
"""
from __future__ import annotations

import copy
import glob as globmod
import os
from dataclasses import dataclass, field
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
    "collections": [],
    "targets": {
        "public": {"base_url": "/", "redact": True, "description": "Public build (GitHub Pages)."},
        "private": {"base_url": "/", "redact": False, "description": "Local-only build with private content."},
    },
    "vocabularies": {
        "lifecycle": ["active", "stable", "deprecated", "archived"],
        "work_status": ["planned", "active", "blocked", "done", "dropped"],
        "record_status": ["proposed", "accepted", "rejected", "superseded"],
        "risk_status": ["open", "mitigated", "accepted", "closed"],
        "question_status": ["open", "answered", "deferred"],
        "release_status": ["released", "unreleased", "yanked"],
    },
    "limits": {"title": 120, "summary": 300, "line": 240, "markdown": 2400},
    "build": {"strict": False},
    "evidence": {"git_recency": True, "run_commands": False},
    "markdown": {"renderer": None},
    "hooks": None,
    "mounts": [],
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

COLLECTION_DEFAULTS: dict[str, Any] = {
    "root": "",
    "docs": "docs",
    "pages": "pages",
    "prefabs": "prefabs",
    "schemas": "schemas",
    "assets": "assets",
    "config": "collection.yaml",
    "hooks": "hooks.py",
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


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


@dataclass
class Collection:
    """A folder holding an isolated doc set (a musing, a sub-project). id "" is the site root."""

    id: str
    root: Path
    docs_dir: Path
    pages_dir: Path
    prefabs_dir: Path | None = None
    schemas_dir: Path | None = None
    assets_dir: Path | None = None
    hooks_path: Path | None = None
    data: dict = field(default_factory=dict)

    @property
    def is_root(self) -> bool:
        return self.id == ""

    @property
    def title(self) -> str:
        return self.data.get("title") or (self.id or "site")

    @property
    def order(self) -> int:
        return int(self.data.get("order", 100))

    @property
    def hero(self) -> bool:
        return bool(self.data.get("hero", False))

    @property
    def links(self) -> list[dict]:
        """Card links declared in collection.yaml, hrefs still relative to the collection."""
        return [dict(link) for link in self.data.get("links") or []]

    @property
    def visibility(self) -> str:
        return self.data.get("visibility", "public")

    @property
    def route_prefix(self) -> str:
        return "/" if self.is_root else f"/{self.id}/"

    def doc_id(self, stem: str) -> str:
        return stem if self.is_root else f"{self.id}/{stem}"

    def vocab(self, cfg: "Config") -> dict:
        return deep_merge(cfg.vocabularies, self.data.get("vocabularies") or {})

    def limits(self, cfg: "Config") -> dict:
        return deep_merge(cfg.limits, self.data.get("limits") or {})

    def evidence(self, cfg: "Config") -> dict:
        return deep_merge(cfg.evidence, self.data.get("evidence") or {})

    def summary(self) -> dict:
        """JSON-able description used by the $collections virtual root."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.data.get("summary", ""),
            "emblem": self.data.get("emblem"),
            "order": self.order,
            "hero": self.hero,
            "links": self.links,
            "route": self.route_prefix,
            "visibility": self.visibility,
            "tags": self.data.get("tags") or [],
            "status": self.data.get("status"),
            "theme": self.data.get("theme") or {},
        }


class Config:
    def __init__(self, root: Path, data: dict | None, source: Path | None = None):
        self.root = Path(root).resolve()
        self.source = source
        self.raw = data or {}
        self.data = deep_merge(DEFAULTS, self.raw)
        self._collections: list[Collection] | None = None

    @classmethod
    def load(cls, root: str | Path | None = None) -> "Config":
        root_path = find_root(root) if root is None else Path(root).resolve()
        for name in CONFIG_NAMES:
            candidate = root_path / name
            if candidate.is_file():
                return cls(root_path, _read_yaml(candidate), candidate)
        raise ConfigError(f"no site.yaml in {root_path}")

    # --- paths -----------------------------------------------------------
    def path(self, key: str) -> Path:
        return self.root / self.data["paths"][key]

    def out_dir(self, target: str) -> Path:
        return self.path("out") / target

    def schema_dirs(self) -> list[Path]:
        dirs = [PKG_DIR / "schemas", self.path("schemas")]
        dirs += [c.schemas_dir for c in self.collections() if c.schemas_dir and not c.is_root]
        return dirs

    def prefab_dirs(self) -> list[Path]:
        dirs = [PKG_DIR / "prefabs", self.path("prefabs")]
        dirs += [c.prefabs_dir for c in self.collections() if c.prefabs_dir and not c.is_root]
        return dirs

    def watch_paths(self) -> list[Path]:
        paths = [self.source] if self.source else []
        paths += [self.path(k) for k in ("docs", "pages", "prefabs", "layouts", "assets", "schemas")]
        paths += [c.root for c in self.collections() if not c.is_root]
        paths += [self.root / p for p in self.serve.get("watch") or []]
        return [p for p in paths if p is not None]

    # --- collections -----------------------------------------------------
    def collections(self) -> list[Collection]:
        if self._collections is not None:
            return self._collections
        found: list[Collection] = [
            Collection(
                id="",
                root=self.root,
                docs_dir=self.path("docs"),
                pages_dir=self.path("pages"),
                prefabs_dir=self.path("prefabs"),
                schemas_dir=self.path("schemas"),
                assets_dir=self.path("assets"),
                hooks_path=(self.root / self.data["hooks"]) if self.data.get("hooks") else None,
                data={"title": self.site.get("name", "site"), "order": 0},
            )
        ]
        seen: set[str] = set()
        for spec in self.data.get("collections") or []:
            spec = deep_merge(COLLECTION_DEFAULTS, spec if isinstance(spec, dict) else {"root": str(spec)})
            pattern = spec["root"]
            if not pattern:
                raise ConfigError("collections[].root is required (a folder or glob, e.g. musings/*)")
            matches = sorted(globmod.glob(str(self.root / pattern)))
            for match in matches:
                folder = Path(match)
                if not folder.is_dir() or folder.name.startswith(("_", ".")):
                    continue
                cid = folder.name
                if cid in seen:
                    raise ConfigError(f"collection id '{cid}' matched twice (patterns overlap)")
                seen.add(cid)
                config_path = folder / spec["config"]
                data = _read_yaml(config_path) if config_path.is_file() else {}
                data.setdefault("title", cid.replace("-", " ").replace("_", " ").title())
                hooks_path = folder / spec["hooks"]
                collection = Collection(
                    id=cid,
                    root=folder,
                    docs_dir=folder / spec["docs"],
                    pages_dir=folder / spec["pages"],
                    prefabs_dir=folder / spec["prefabs"],
                    schemas_dir=folder / spec["schemas"],
                    assets_dir=folder / spec["assets"],
                    hooks_path=hooks_path if hooks_path.is_file() else None,
                    data=data,
                )
                found.append(collection)
        self._collections = found
        return found

    def collection(self, cid: str) -> Collection:
        for c in self.collections():
            if c.id == cid:
                return c
        raise ConfigError(f"unknown collection '{cid}'")

    # --- sections --------------------------------------------------------
    @property
    def site(self) -> dict:
        return self.data["site"]

    @property
    def targets(self) -> dict:
        return self.data["targets"]

    @property
    def vocabularies(self) -> dict:
        return self.data["vocabularies"]

    @property
    def limits(self) -> dict:
        return self.data["limits"]

    @property
    def build(self) -> dict:
        return self.data["build"]

    @property
    def evidence(self) -> dict:
        return self.data["evidence"]

    def evidence_for(self, collection_id: str | None = "") -> dict:
        """Evidence policy for one collection's docs: site.yaml `evidence` plus its own override."""
        if not collection_id:
            return dict(self.evidence)
        for c in self.collections():
            if c.id == collection_id:
                return c.evidence(self)
        return dict(self.evidence)

    @property
    def mounts(self) -> list[dict]:
        return list(self.data.get("mounts") or [])

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
        """Site sources plus collection sources, the latter namespaced as <collection>.<name>."""
        out = dict(self.data["dynamic"].get("sources") or {})
        for c in self.collections():
            if c.is_root:
                continue
            for name, spec in ((c.data.get("dynamic") or {}).get("sources") or {}).items():
                out[f"{c.id}.{name}"] = dict(spec, _collection=c.id)
        return out

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
