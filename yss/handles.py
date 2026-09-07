"""Persistent item handles: `PLN.7` as a second key on the index yss already has.

A doc declares `mnemonic: PLN` in its envelope and every one of its list items declares
`handle: PLN.7` explicitly. Nothing is derived - not from list position, which changes the first
time somebody sorts a list, and not from the item's slug, which is a coincidence rather than a
rule. The pair is what makes a handle citable from anywhere: `[[PLN.7]]` and `[[plan#m14-audit]]`
go through the same resolver to the same item.

Two classes, and the difference is what each can promise:

- A **bound handle** lives on a doc item. It resolves to that item, so its text can be quoted
  exactly - which is what powers a citation tooltip and a contradiction register's quote rule.
- A **mention** is a handle written anywhere else: page prose, a Rust comment, a commit message.
  `sweep()` finds it and reports where it is, but a mention has no boundary - there is no honest
  answer to where the item's text stops - so it resolves to a *location*, never to a quote.

Canonical form is uppercase and lookup folds case, so `agp.1` and `AGP.1` are one handle with one
spelling in the index. Storage stays whatever the author wrote in the file, because a `contains:`
evidence claim is a raw substring match and would not find a spelling the index invented.

Archived docs are indexed at tier `archived`. The archive rule means *not loaded into a working
agent's context*, not absent from the index: a handle whose item was archived still resolves and
still quotes, and reads as spent rather than missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

#: A mnemonic is any short unique slug (H.3). `.` and `/` are the two characters it may not
#: contain, because they are already the handle separator and the collection/doc separator.
MNEMONIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: `PREFIX.<n>` - the whole string, anchored.
HANDLE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]*)\.(0|[1-9][0-9]*)$")
#: `.<n>` - the in-scope form, resolved against the enclosing doc's own mnemonic.
SCOPED_RE = re.compile(r"^\.(0|[1-9][0-9]*)$")
#: The same shape found loose in running text, for the mention sweep. Word-bounded on both sides so
#: `v1.4`, `Fig.2` and a version string cannot be read as handles unless the prefix is a real one.
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_-]*)\.(0|[1-9][0-9]*)(?![0-9.])")

#: Files the mention sweep reads by default. Overridable per collection (`register.sweep_ext`).
DEFAULT_SWEEP_EXT = (".yaml", ".yml", ".md", ".rs", ".py", ".svg", ".toml", ".html", ".txt")
#: Never swept: build output, version control, caches and vendored trees.
SWEEP_SKIP_DIRS = {"dist", ".git", "__pycache__", "node_modules", ".venv", "venv", "target", ".yss"}


def canon(text: str) -> str:
    """The canonical spelling of a mnemonic or handle: uppercase."""
    return text.upper()


def parse_handle(text: str) -> tuple[str, int] | None:
    """`'pln.7'` -> `('PLN', 7)`; anything that is not a handle -> None."""
    match = HANDLE_RE.match(text.strip())
    if not match:
        return None
    return canon(match.group(1)), int(match.group(2))


def parse_scoped(text: str) -> int | None:
    """`'.7'` -> `7`; anything else -> None. The in-scope form, like `[[#item]]`."""
    match = SCOPED_RE.match(text.strip())
    return int(match.group(1)) if match else None


def format_handle(mnemonic: str, number: int) -> str:
    return f"{canon(mnemonic)}.{number}"


@dataclass
class Mnemonic:
    """One doc's declared series."""

    name: str  #: canonical, uppercase
    spelled: str  #: as the doc wrote it
    doc_id: str
    doc_title: str
    source: str
    label: str = ""  #: what the series is *about*; set from a collection's `register.series`

    @property
    def shown(self) -> str:
        """The first tooltip line: the series label when a register names one, else the doc title."""
        return self.label or self.doc_title


@dataclass
class Handle:
    """One bound handle: a resolved doc item."""

    handle: str  #: canonical `PLN.7`
    spelled: str  #: as the file wrote it
    mnemonic: str
    number: int
    doc_id: str
    item_id: str | None
    item: dict
    source: str
    tier: str = "live"  #: live | archived
    field: str = ""  #: the doc field the item was found under, e.g. `milestones`

    @property
    def text(self) -> str:
        """The item's own words - never a paraphrase, never a second copy."""
        return item_text(self.item)

    @property
    def archived(self) -> bool:
        return self.tier == "archived"


@dataclass
class Mention:
    """A handle written outside a doc item. Resolves to a location, never to a quote."""

    handle: str
    spelled: str
    mnemonic: str
    number: int
    path: str
    line: int
    known: bool = False  #: whether a bound handle of this name exists


#: Fields an item's display text is taken from, most specific first. Same order as the renderer's
#: `_item_title`, plus the fields a rule-shaped item actually carries its wording in.
TEXT_FIELDS = (
    "title", "name", "term", "question", "lead", "rule", "says",
    "text", "claim", "summary", "definition", "decision",
)


def item_text(item: dict) -> str:
    for key in TEXT_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class HandleIndex:
    """Canonical handle -> item, plus the mnemonic map. Lookup folds case."""

    def __init__(self) -> None:
        self.handles: dict[str, Handle] = {}
        self.mnemonics: dict[str, Mnemonic] = {}
        self._by_doc: dict[str, str] = {}

    # --- lookup ---------------------------------------------------------
    def get(self, text: str) -> Handle | None:
        parsed = parse_handle(text)
        if not parsed:
            return None
        return self.handles.get(format_handle(*parsed))

    def resolve(self, text: str, doc_id: str | None = None) -> Handle | None:
        """`PLN.7` anywhere, or `.7` against `doc_id`'s own mnemonic."""
        number = parse_scoped(text)
        if number is not None:
            mnemonic = self._by_doc.get(doc_id or "")
            return self.handles.get(format_handle(mnemonic, number)) if mnemonic else None
        return self.get(text)

    def mnemonic_of(self, doc_id: str) -> str | None:
        return self._by_doc.get(doc_id)

    def series(self, mnemonic: str) -> list[Handle]:
        name = canon(mnemonic)
        return sorted(
            (h for h in self.handles.values() if h.mnemonic == name),
            key=lambda h: h.number,
        )

    def next_number(self, mnemonic: str) -> int:
        """The mint pointer: one past the highest number the series has reached, live or archived."""
        existing = self.series(mnemonic)
        return (existing[-1].number + 1) if existing else 1

    def gaps(self, mnemonic: str) -> list[int]:
        """Missing numbers below the high-water mark. Append-only means a gap is a missing row."""
        numbers = {h.number for h in self.series(mnemonic)}
        if not numbers:
            return []
        return [n for n in range(1, max(numbers) + 1) if n not in numbers]

    def __len__(self) -> int:
        return len(self.handles)


def _walk_items(doc: dict) -> Iterator[tuple[str, dict]]:
    """(field, item) for every dict in a list anywhere in the doc. Mirrors `index_ids`."""

    def walk(value: Any, field_name: str) -> Iterator[tuple[str, dict]]:
        if isinstance(value, dict):
            for key, sub in value.items():
                if isinstance(key, str) and key.startswith("_"):
                    continue
                # The *nearest* enclosing list names the field, so a plan task nested under a
                # milestone reports `tasks` rather than `milestones`.
                yield from walk(sub, key if isinstance(sub, list) else field_name)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield field_name, item
                yield from walk(item, field_name)

    for key, value in doc.items():
        if isinstance(key, str) and key.startswith("_"):
            continue
        yield from walk(value, key)


def _claim(index: HandleIndex, entry: Handle, errors: list[str]) -> None:
    clash = index.handles.get(entry.handle)
    if clash is not None:
        errors.append(
            f"{entry.source}: handle '{entry.spelled}' is already used by "
            f"{clash.source} ({clash.item_id or 'unnamed item'}); handles are never reused"
        )
        return
    index.handles[entry.handle] = entry


def build_index(docs: dict[str, dict], archived: Iterable[tuple[str, dict]] = ()) -> tuple[HandleIndex, list[str]]:
    """Index every declared mnemonic and handle. Returns (index, errors).

    Errors are the ones no JSON Schema can state, because they compare one document with another:
    a mnemonic two docs both claim, a handle two items both carry, a handle whose prefix is not its
    own doc's mnemonic.
    """
    index = HandleIndex()
    errors: list[str] = []

    for doc_id, doc in sorted(docs.items()):
        raw = doc.get("mnemonic")
        if raw is None:
            continue
        source = doc.get("_source", doc_id)
        if not isinstance(raw, str) or not MNEMONIC_RE.match(raw):
            errors.append(
                f"{source}: at mnemonic: '{raw}' is not a mnemonic; any short slug is allowed "
                f"but not '.' or '/', which are the handle and doc separators"
            )
            continue
        name = canon(raw)
        clash = index.mnemonics.get(name)
        if clash is not None:
            errors.append(
                f"{source}: at mnemonic: '{raw}' is already declared by {clash.source} "
                f"(doc '{clash.doc_id}'); mnemonics are unique across the site, case-insensitively"
            )
            continue
        index.mnemonics[name] = Mnemonic(
            name=name,
            spelled=raw,
            doc_id=doc_id,
            doc_title=str(doc.get("title") or doc_id),
            source=source,
            label="",  # filled from a collection's `register.series` when one names this prefix
        )
        index._by_doc[doc_id] = name

    for doc_id, doc in sorted(docs.items()):
        source = doc.get("_source", doc_id)
        own = index.mnemonic_of(doc_id)
        for field_name, item in _walk_items(doc):
            spelled = item.get("handle")
            if spelled is None:
                continue
            if not isinstance(spelled, str) or not (parsed := parse_handle(spelled)):
                errors.append(f"{source}: at {field_name}: '{spelled}' is not a handle (expected PREFIX.<n>)")
                continue
            mnemonic, number = parsed
            if own is None:
                errors.append(
                    f"{source}: at {field_name}: item carries handle '{spelled}' but the doc "
                    f"declares no mnemonic"
                )
                continue
            if mnemonic != own:
                errors.append(
                    f"{source}: at {field_name}: handle '{spelled}' does not use this doc's "
                    f"mnemonic '{index.mnemonics[own].spelled}'"
                )
                continue
            _claim(index, Handle(
                handle=format_handle(mnemonic, number), spelled=spelled, mnemonic=mnemonic,
                number=number, doc_id=doc_id, item_id=item.get("id"), item=item,
                source=source, tier="live", field=field_name,
            ), errors)

    for source, doc in archived:
        for field_name, item in _walk_items(doc):
            spelled = item.get("handle")
            if not isinstance(spelled, str):
                continue
            parsed = parse_handle(spelled)
            if not parsed:
                errors.append(f"{source}: at {field_name}: '{spelled}' is not a handle (expected PREFIX.<n>)")
                continue
            mnemonic, number = parsed
            owner = index.mnemonics.get(mnemonic)
            if owner is None:
                errors.append(
                    f"{source}: at {field_name}: archived handle '{spelled}' uses mnemonic "
                    f"'{mnemonic}', which no doc declares"
                )
                continue
            _claim(index, Handle(
                handle=format_handle(mnemonic, number), spelled=spelled, mnemonic=mnemonic,
                number=number, doc_id=owner.doc_id, item_id=item.get("id"), item=item,
                source=source, tier="archived", field=field_name,
            ), errors)

    return index, errors


# --- the mention sweep ------------------------------------------------------
def sweep(root: Path, index: HandleIndex, extensions: Iterable[str] = DEFAULT_SWEEP_EXT,
          skip_dirs: Iterable[str] = SWEEP_SKIP_DIRS, only: str | None = None) -> list[Mention]:
    """Every handle written in a file, outside the doc item that binds it.

    A mention is found by text, so the sweep only accepts a prefix that some doc has actually
    declared - otherwise every `v1.4` and `Fig.2` in the repository would read as a handle. Its
    result is a location, never a quote: a mention has no boundary, so nothing here may be treated
    as the item's text.
    """
    suffixes = {e if e.startswith(".") else f".{e}" for e in extensions}
    skip = set(skip_dirs)
    base = root / only if only else root
    found: list[Mention] = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*")):
        if path.suffix not in suffixes or not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part in skip for part in rel_parts[:-1]):
            continue
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in MENTION_RE.finditer(line):
                mnemonic = canon(match.group(1))
                if mnemonic not in index.mnemonics:
                    continue
                number = int(match.group(2))
                handle = format_handle(mnemonic, number)
                found.append(Mention(
                    handle=handle, spelled=match.group(0), mnemonic=mnemonic, number=number,
                    path=rel, line=lineno, known=handle in index.handles,
                ))
    return found


def unregistered(mentions: Iterable[Mention], allowed: Iterable[str] = ()) -> list[Mention]:
    """Mentions of a handle no doc item binds and no `unminted` declaration excuses."""
    excused = {canon(a) for a in allowed}
    return [m for m in mentions if not m.known and m.handle not in excused]
