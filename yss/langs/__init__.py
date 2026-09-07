"""Per-language symbol indexers: the parsers behind code map deep links.

One module per language, each answering the same question - which lines does a name a code map
cites occupy? - and registered here by file suffix. `yss/symbols.py` is the dispatcher; nothing
in this package knows about docs, targets or rendering, and nothing here imports or executes the
code it reads (adr-024).

A language module supplies:

    SUFFIXES      file suffixes it claims, lowercase and dotted
    PACKAGE_STEMS file stems that contribute *bare* names when a directory is indexed
                  (`__init__` for Python; `mod`, `lib` and `main` for Rust). Every other file in
                  the directory contributes names prefixed with its stem and MEMBER_SEP.
    MEMBER_SEP    how a member of a container is spelled - `.` for Python, `::` for Rust
    ALIAS_SEPS    other separators a code map author may write for a member, normalised on lookup
    LABEL         a short language name the site uses to choose a client-side highlighter
    file_index(path, rel) -> {name: (start, end)}

Adding a language is adding a module and one entry in REGISTRY (adr-037).
"""
from __future__ import annotations

from .base import Range, SymbolError  # noqa: F401 - re-exported; yss.symbols is the public door
from . import python, rust

#: Every language this build can parse. Order is irrelevant; suffixes are unique.
REGISTRY = (python, rust)

#: Suffixes any registered language claims.
SUFFIXES = frozenset(s for lang in REGISTRY for s in lang.SUFFIXES)


def language_for(suffix: str):
    """The module that parses this suffix, or None."""
    suffix = (suffix or "").lower()
    for lang in REGISTRY:
        if suffix in lang.SUFFIXES:
            return lang
    return None


def label_for(suffix: str) -> str:
    """A short language name for a suffix (`python`, `rust`), or '' when nothing parses it.

    The site uses this to choose a highlighter, so it is deliberately one string per language
    rather than the suffix itself.
    """
    lang = language_for(suffix)
    return lang.LABEL if lang else ""


def spellings(suffix: str, name: str) -> list[str]:
    """Every way a code map may write `name` for this language, best first.

    Rust members are `Type::method`, but a code map author coming from the Python side writes
    `Type.method`; both should find the same lines rather than one of them reading as a moved
    export.
    """
    lang = language_for(suffix)
    if not lang:
        return [name]
    out = [name]
    for alias in getattr(lang, "ALIAS_SEPS", ()):
        if alias in name:
            swapped = name.replace(alias, lang.MEMBER_SEP)
            if swapped not in out:
                out.append(swapped)
    return out
