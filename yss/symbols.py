"""Resolve a code map export to its line range by parsing, never importing.

`yss check` and the `symbols` dynamic source both need to turn a (module path, export name) pair
into a line range. Importing the module would execute its top level, it still would not answer for
constants or for dotted members such as `Config.evidence_for`, and for a language that is not
Python there is nothing to import at all. So the file is parsed instead. Nothing here runs project
code, and nothing here shells out to a compiler or a build tool.

This module is the dispatcher and the cache; the parsers live one per language in `yss/langs/`
and are registered there by file suffix (adr-037). Today that is Python (by `ast`) and Rust (by
scanning). `supported()` says whether a code map module can carry line ranges at all; a module in
any other language gets a plain file link and no range (adr-024).

Names understood for a file path (`yss/config.py`, `src/ballistics.rs`) and for a directory
(`yss/providers/`, `prototype/tsg-range/src/`) are the language's business - see each parser's
docstring. A directory mixes languages freely: every file in it that some parser claims is read,
and the ones a language calls its package root (`__init__.py`, `mod.rs`, `lib.rs`, `main.rs`)
contribute bare names while the rest are prefixed with their own stem.
"""
from __future__ import annotations

from pathlib import Path

from .langs import REGISTRY, SUFFIXES, SymbolError, label_for, language_for, spellings

__all__ = ["SUPPORTED_SUFFIXES", "SymbolError", "Located", "Range", "file_index", "index_for",
           "label_for", "lookup", "supported"]

#: Suffixes this resolver can parse. A code map module with any other suffix is linkable but not
#: seekable, and callers should fall back to a whole-file link.
SUPPORTED_SUFFIXES = SUFFIXES

#: (start, end) within one file.
Range = tuple[int, int]
#: (start, end, file) - what `index_for` returns, because a code map module may name a directory
#: while the export itself lives in one of its files.
Located = tuple[int, int, str]


def file_index(path: Path, rel: str | None = None) -> dict[str, Range]:
    """Every name a code map may cite in one file, mapped to its line range.

    Empty for a file no registered language claims - that is not an error, it is a module the
    site links to whole.
    """
    rel = rel or path.name
    lang = language_for(path.suffix)
    return lang.file_index(path, rel) if lang else {}


#: Keyed by (absolute target, the spelling it was asked for, mtime). The spelling belongs in the
#: key because it is what the stored file paths are built from: the same directory reached as
#: `src/` and as `examples/rust-musing/prototype/lifelike/src/` must not share an entry, or the
#: second caller gets the first one's paths.
_CACHE: dict[tuple[str, str, float], dict[str, Located]] = {}


def _mtime(target: Path) -> float:
    """Newest mtime under a path, so a cached index expires when any parsed file changes."""
    try:
        if target.is_dir():
            children = [c.stat().st_mtime for c in target.iterdir()
                        if c.is_file() and c.suffix.lower() in SUFFIXES]
            return max([target.stat().st_mtime, *children])
        return target.stat().st_mtime
    except (OSError, ValueError):
        return 0.0


def index_for(root: Path, rel: str) -> dict[str, Located]:
    """Resolve one code map `modules[].path` - a file or a directory - to its names.

    For a directory, a submodule's names are prefixed with its stem and its language's member
    separator (`buildinfo.collect`, `ballistics::GRAVITY`), and the language's package-root files
    contribute bare names. Each entry names the file it was found in, so a deep link points at
    that file and not at the directory.
    """
    rel = rel.strip().rstrip("/")
    target = root / rel
    key = (str(target), rel, _mtime(target))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = _index_uncached(target, rel)
    _CACHE[key] = result
    return result


def _index_uncached(target: Path, rel: str) -> dict[str, Located]:
    if target.is_dir():
        index: dict[str, Located] = {}
        for child in sorted(target.iterdir()):
            lang = language_for(child.suffix) if child.is_file() else None
            if lang is None:
                continue
            child_rel = f"{rel}/{child.name}"
            prefix = "" if child.stem in lang.PACKAGE_STEMS else f"{child.stem}{lang.MEMBER_SEP}"
            for name, span in lang.file_index(child, child_rel).items():
                index.setdefault(f"{prefix}{name}", (span[0], span[1], child_rel))
        return index
    lang = language_for(target.suffix)
    if lang is None:
        return {}
    return {name: (span[0], span[1], rel) for name, span in lang.file_index(target, rel).items()}


def supported(rel: str) -> bool:
    """Can a module at this path carry line ranges? A directory counts if some parser claims it."""
    rel = rel.strip().rstrip("/")
    if rel.endswith("/"):
        return True
    suffix = Path(rel).suffix
    return not suffix or suffix.lower() in SUFFIXES


def lookup(root: Path, rel: str, name: str) -> Located | None:
    """(start, end, file) for one export, or None when the name is not defined where the doc says.

    A member may be spelled with either language's separator: `Approach::is_hit` is what Rust
    calls it and what the index stores, and `Approach.is_hit` finds the same lines, so a code map
    written in one house style does not read as a wall of moved exports (see langs.spellings).
    """
    rel = rel.strip().rstrip("/")
    try:
        index = index_for(root, rel)
    except SymbolError:
        return None
    suffix = Path(rel).suffix
    for candidate in _candidates(index, suffix, name):
        span = index.get(candidate)
        if span:
            return span
    return None


def _candidates(index: dict[str, Located], suffix: str, name: str) -> list[str]:
    """Spellings to try for `name`. A directory has no suffix of its own, so every language's."""
    if suffix:
        return spellings(suffix, name)
    seen: list[str] = []
    for lang in REGISTRY:
        for candidate in spellings(next(iter(lang.SUFFIXES)), name):
            if candidate not in seen:
                seen.append(candidate)
    return seen
