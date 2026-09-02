"""Visibility filtering (public/private) and forbidden-string scanning."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PRIVATE_KEYS = ("private_notes",)
TEXT_SUFFIXES = {
    ".html", ".htm", ".json", ".css", ".js", ".txt", ".md", ".xml", ".svg",
    ".yaml", ".yml", ".csv", ".map", ".py", ".toml", ".cfg", ".ini",
}
DEFAULT_SKIP_DIRS = (".git", "__pycache__", "node_modules", ".yss", "dist", ".venv", "venv")

_REMOVED = object()


def is_visible(obj: Any, target: str) -> bool:
    if target != "public":
        return True
    if isinstance(obj, dict):
        return obj.get("visibility", "public") != "private"
    return True


def filter_for_target(data: Any, target: str) -> Any:
    """Return a deep copy of data with private objects removed for the public target."""
    result = _filter(data, target)
    return None if result is _REMOVED else result


def _filter(data: Any, target: str) -> Any:
    if isinstance(data, dict):
        if not is_visible(data, target):
            return _REMOVED
        out = {}
        for key, value in data.items():
            if target == "public" and key in PRIVATE_KEYS:
                continue
            filtered = _filter(value, target)
            if filtered is _REMOVED:
                continue
            out[key] = filtered
        return out
    if isinstance(data, list):
        items = []
        for value in data:
            filtered = _filter(value, target)
            if filtered is _REMOVED:
                continue
            items.append(filtered)
        return items
    return data


def mask(pattern: str) -> str:
    if len(pattern) <= 2:
        return "*" * len(pattern)
    return pattern[0] + "*" * (len(pattern) - 2) + pattern[-1]


def scan_text(text: str, patterns: list[str]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    if not patterns:
        return hits
    lowered = [(p, p.lower()) for p in patterns]
    for lineno, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for original, needle in lowered:
            if needle in low:
                hits.append((original, lineno))
    return hits


def iter_text_files(root: Path, skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix != "":
            continue
        yield path


def scan_tree(root: Path, forbidden: list[str], flags: list[str], skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS):
    """Scan text files under root. Returns (forbidden_hits, flag_hits) as (relpath, line, masked)."""
    fhits, whits = [], []
    for path in iter_text_files(root, skip_dirs):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for pattern, line in scan_text(text, forbidden):
            fhits.append((rel, line, mask(pattern)))
        for pattern, line in scan_text(text, flags):
            whits.append((rel, line, mask(pattern)))
    return fhits, whits


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: Any) -> str:
    return _slug_re.sub("-", str(value).lower()).strip("-") or "x"
