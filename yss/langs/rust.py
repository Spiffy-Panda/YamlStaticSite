"""Rust symbol index, by scanning. There is no `ast` for Rust in the standard library and a real
parser would be a dependency, so this reads the text the way a reader does: blank out everything
that is not code, then walk the braces (adr-037).

Nothing is compiled, expanded or executed - `cargo` is never invoked - so the index is as cheap
and as safe on an unbuildable working tree as the Python one is.

Names understood for one file:

    level_drop_table          a free `fn`, `struct`, `enum`, `union`, `trait`, `type` or `mod`
    GRAVITY                   a `const` or `static`
    Projectile::new           an inherent or trait `impl` method, or a trait's own method
    Sight::Iron               an enum variant
    Approach::is_hit          which a code map may also spell `Approach.is_hit`
    tests::drop_is_monotonic  an item inside an inline `mod`
    dim                       a name this module renames with `use ... as` or re-exports

and for a crate source directory (`prototype/tsg-range/src/`):

    ballistics::GRAVITY       `GRAVITY` inside src/ballistics.rs
    run                       a name in that directory's mod.rs, lib.rs or main.rs

Deliberately not indexed: struct fields (they would shadow methods of the same name, and
`Type::field` is not how Rust spells one), anything nested inside a function body, and
macro-generated items, which no amount of scanning can see.
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import Range, SymbolError

SUFFIXES = frozenset({".rs"})
#: Inside a directory these files contribute bare names; every other file is prefixed by its stem.
PACKAGE_STEMS = frozenset({"mod", "lib", "main"})
MEMBER_SEP = "::"
ALIAS_SEPS = (".",)
LABEL = "rust"

#: Words that may precede an item keyword without being one.
_MODIFIERS = frozenset({"pub", "default", "async", "unsafe", "extern", "auto", "move"})
#: Words that introduce an item. `const` and `static` are both - `const fn` is a function.
_KEYWORDS = frozenset({"fn", "struct", "enum", "union", "trait", "type", "mod", "impl", "use",
                       "const", "static", "macro_rules"})
#: Items whose body holds more items worth indexing.
_CONTAINERS = frozenset({"impl", "trait", "mod"})

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENT = _WORD
_OPEN = {"{": "}", "(": ")", "[": "]"}
_CLOSE = frozenset("})]")


# --- stage one: blank everything that is not code ---------------------------------------------

class _Blanked:
    """The source with comments, literal bodies and attributes replaced by spaces.

    Offsets and line breaks are preserved exactly, so an offset into `text` is still an offset
    into the original file. `doc_lines` and `attr_lines` are what an item's start line extends
    backwards over, the way a decorator extends a Python one.
    """

    __slots__ = ("text", "doc_lines", "attr_lines", "starts")

    def __init__(self, text: str, doc_lines: set[int], attr_lines: set[int], starts: list[int]):
        self.text = text
        self.doc_lines = doc_lines
        self.attr_lines = attr_lines
        self.starts = starts

    def line_of(self, offset: int) -> int:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def head_line(self, offset: int) -> int:
        """The line an item really begins on: back over its attributes and doc comments."""
        line = self.line_of(offset)
        while line > 1 and ((line - 1) in self.doc_lines or (line - 1) in self.attr_lines):
            line -= 1
        return line


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _blank(source: str) -> _Blanked:
    out = list(source)
    doc_lines: set[int] = set()
    attr_lines: set[int] = set()
    starts = _line_starts(source)
    n = len(source)

    probe = _Blanked(source, doc_lines, attr_lines, starts)

    def wipe(a: int, b: int, record: set[int] | None = None) -> None:
        b = min(b, n)
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
        if record is not None and b > a:
            for line in range(probe.line_of(a), probe.line_of(b - 1) + 1):
                record.add(line)

    i = 0
    while i < n:
        ch = source[i]

        if ch == "/" and source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            doc = source.startswith("///", i) or source.startswith("//!", i)
            doc = doc and not source.startswith("////", i)
            wipe(i, end, doc_lines if doc else None)
            i = end
            continue

        if ch == "/" and source.startswith("/*", i):
            doc = (source.startswith("/**", i) or source.startswith("/*!", i)) and not source.startswith("/**/", i)
            depth, j = 0, i
            while j < n:
                if source.startswith("/*", j):
                    depth += 1
                    j += 2
                elif source.startswith("*/", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            wipe(i, j, doc_lines if doc else None)
            i = j
            continue

        if ch == "#" and (source.startswith("#[", i) or source.startswith("#![", i)):
            j = source.index("[", i)
            depth = 0
            while j < n:
                c = source[j]
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                elif c in "\"'" or _literal_prefix(source, j):
                    nxt = _skip_literal(source, j)
                    if nxt > j + 1:
                        j = nxt
                        continue
                j += 1
            wipe(i, j, attr_lines)
            i = j
            continue

        if ch in "\"'" or _literal_prefix(source, i):
            j = _skip_literal(source, i)
            if j > i + 1:
                wipe(i, j)
                i = j
                continue

        i += 1

    return _Blanked("".join(out), doc_lines, attr_lines, starts)


def _literal_prefix(source: str, i: int) -> bool:
    """Is `i` the start of a prefixed literal - `r"`, `r#"`, `b"`, `br#"`, `b'`?"""
    if source[i] not in "rb":
        return False
    if i and (source[i - 1].isalnum() or source[i - 1] == "_"):
        return False
    j = i
    if source[j] == "b":
        j += 1
    if j < len(source) and source[j] == "r":
        j += 1
        while j < len(source) and source[j] == "#":
            j += 1
    return i < j < len(source) and source[j] in "\"'"


def _skip_literal(source: str, i: int) -> int:
    """Offset just past the literal starting at `i`; `i + 1` when it is not a literal at all.

    The case that matters is `'`: `'flight:` is a loop label and `'a` a lifetime, neither of
    which ever closes, so reading one as a character would swallow the rest of the file.
    """
    n = len(source)
    j = i
    hashes = 0
    if j < n and source[j] == "b":
        j += 1
    raw = j < n and source[j] == "r"
    if raw:
        j += 1
        while j < n and source[j] == "#":
            hashes += 1
            j += 1
    if j >= n or source[j] not in "\"'":
        return i + 1
    quote = source[j]

    if quote == "'" and not raw:
        m = _IDENT.match(source, j + 1)
        if m and (m.end() >= n or source[m.end()] != "'"):
            return i + 1  # a lifetime or a loop label, not a character

    j += 1
    if raw:
        closing = '"' + "#" * hashes
        end = source.find(closing, j)
        return n if end < 0 else end + len(closing)
    while j < n:
        if source[j] == "\\":
            j += 2
            continue
        if source[j] == quote:
            return j + 1
        if source[j] == "\n" and quote == "'":
            return i + 1
        j += 1
    return n


# --- stage two: walk the braces ----------------------------------------------------------------

def _match(text: str, i: int) -> int:
    """Offset just past the bracket group opening at `i`; len(text) when it never closes."""
    stack = [_OPEN[text[i]]]
    j = i + 1
    while j < len(text) and stack:
        c = text[j]
        if c in _OPEN:
            stack.append(_OPEN[c])
        elif c in _CLOSE:
            if c != stack[-1]:
                return len(text)  # unbalanced: give up rather than guess at where it ended
            stack.pop()
        j += 1
    return j


def _terminator(text: str, i: int, hi: int) -> tuple[int, int]:
    """From `i`, find where the item ends: (body_open, item_end).

    `body_open` is the offset of the item's `{` or -1 when it has none (`mod x;`, `type A = B;`,
    a unit struct). `item_end` is one past the item.
    """
    j = i
    while j < hi:
        c = text[j]
        if c == "{":
            return (j, _match(text, j))
        if c == ";":
            return (-1, j + 1)
        if c in ("(", "["):
            j = _match(text, j)
            continue
        if c == "=":
            # An initialiser may hold braces (`const X: A = A { .. };`); the `;` still ends it.
            k = j + 1
            while k < hi and text[k] != ";":
                if text[k] in _OPEN:
                    k = _match(text, k)
                    continue
                k += 1
            return (-1, min(k + 1, hi))
        if c in _CLOSE:
            return (-1, j)
        j += 1
    return (-1, hi)


def _type_name(header: str) -> str:
    """The type an `impl` block is about: `Plugin for BallisticsPlugin` -> `BallisticsPlugin`."""
    header = re.split(r"\bwhere\b", header)[0]
    kept, depth = [], 0
    for ch in header:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            kept.append(ch)
    flat = re.split(r"\bfor\b", "".join(kept))[-1]
    flat = re.sub(r"\b(mut|dyn|impl|const)\b", " ", flat.replace("&", " ").replace("*", " "))
    flat = flat.split("::")[-1]
    names = _IDENT.findall(flat)
    return names[-1] if names else ""


def _use_names(text: str, lo: int, hi: int) -> list[str]:
    """The names a `use` makes this module's own, mirroring how Python re-exports are indexed.

    `use crate::dimensions as dim;` -> `dim`; `use a::{b, c as d};` -> `b`, `d`. A glob adds
    nothing, because the names it brings in are not written down anywhere here.
    """
    names: list[str] = []
    for part in re.split(r"[,{}]", text[lo:hi]):
        part = part.strip().rstrip(";").strip()
        if not part or part.endswith("*"):
            continue
        alias = re.search(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)", part)
        if alias:
            names.append(alias.group(1))
            continue
        segments = [s.strip() for s in part.split("::") if s.strip()]
        if segments and _IDENT.fullmatch(segments[-1]) and segments[-1] not in {"self", "super", "crate"}:
            names.append(segments[-1])
    return names


def _variants(blank: _Blanked, lo: int, hi: int, prefix: str, index: dict[str, Range]) -> None:
    """Enum variants: an identifier at the top of the body, its payload skipped."""
    text = blank.text
    i, expect = lo, True
    while i < hi:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == ",":
            expect = True
            i += 1
            continue
        if c in _OPEN:
            i = _match(text, i)
            continue
        if c == "=":
            while i < hi and text[i] != ",":
                i += 1
            continue
        m = _IDENT.match(text, i)
        if not m:
            i += 1
            continue
        if expect:
            end = m.end()
            j = end
            while j < hi and text[j].isspace():
                j += 1
            if j < hi and text[j] in "({":
                end = _match(text, j)
            index.setdefault(prefix + m.group(0),
                             (blank.head_line(m.start()), blank.line_of(max(end - 1, m.start()))))
            expect = False
            i = end
            continue
        i = m.end()


def _scan(blank: _Blanked, lo: int, hi: int, prefix: str, index: dict[str, Range], depth: int) -> None:
    """Index every item between `lo` and `hi`, recursing only into impl, trait and inline mod.

    `depth` is what keeps a function body from ever being read as a list of items: a `fn`'s body
    is skipped whole, and only containers spend budget going inside.
    """
    text = blank.text
    i = lo
    while i < hi:
        c = text[i]
        if c.isspace() or c in ",;":
            i += 1
            continue
        if c in _OPEN:
            i = _match(text, i)
            continue
        if c in _CLOSE:
            i += 1
            continue

        head, kind, j = i, "", i
        while j < hi:
            while j < hi and text[j].isspace():
                j += 1
            m = _WORD.match(text, j)
            if not m:
                break
            word = m.group(0)
            if word in _KEYWORDS:
                kind = word
                j = m.end()
                if word == "macro_rules" and text[j:j + 1] == "!":
                    j += 1
                break
            if word not in _MODIFIERS:
                break
            j = m.end()
            if word == "pub":
                while j < hi and text[j].isspace():
                    j += 1
                if j < hi and text[j] == "(":
                    j = _match(text, j)

        # `const` and `static` are the two words that are both an item and a modifier: `const fn`
        # is a function and `static mut D` is still a static. Read on until it is clear which.
        if kind in ("const", "static"):
            k = j
            while True:
                while k < hi and text[k].isspace():
                    k += 1
                nxt = _WORD.match(text, k)
                if not nxt:
                    break
                if nxt.group(0) == "fn":
                    kind, j = "fn", nxt.end()
                    break
                if nxt.group(0) not in _MODIFIERS and nxt.group(0) != "mut":
                    break
                k = nxt.end()
                if kind == "static" and nxt.group(0) == "mut":
                    j = k

        if not kind:
            m = _WORD.match(text, i)
            i = m.end() if m else i + 1   # not an item: step over one token and keep looking
            continue

        while j < hi and text[j].isspace():
            j += 1
        body, end = _terminator(text, j, hi)
        start_line, end_line = blank.head_line(head), blank.line_of(max(end - 1, head))

        if kind == "use":
            for name in _use_names(text, j, end):
                index.setdefault(prefix + name, (start_line, end_line))
        elif kind == "impl":
            name = _type_name(text[j:body if body >= 0 else end])
            if body >= 0 and name and depth > 0:
                _scan(blank, body + 1, end - 1, f"{prefix}{name}{MEMBER_SEP}", index, depth - 1)
        else:
            m = _IDENT.match(text, j)
            if m:
                name = m.group(0)
                index.setdefault(prefix + name, (start_line, end_line))
                if body >= 0:
                    inner = f"{prefix}{name}{MEMBER_SEP}"
                    if kind == "enum":
                        _variants(blank, body + 1, end - 1, inner, index)
                    elif kind in _CONTAINERS and depth > 0:
                        _scan(blank, body + 1, end - 1, inner, index, depth - 1)

        i = end if end > i else i + 1


def file_index(path: Path, rel: str) -> dict[str, Range]:
    """Every name a code map may cite in one Rust file, mapped to its line range."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SymbolError(rel, type(exc).__name__) from exc
    index: dict[str, Range] = {}
    # Two levels: the file's own items, and the members of one impl, trait or inline mod. A third
    # would index items inside `mod tests { mod inner { .. } }`, which no code map cites and which
    # only makes the index bigger.
    _scan(_blank(source), 0, len(source), "", index, depth=2)
    return index
