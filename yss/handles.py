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
from typing import Any, Iterable, Iterator, NamedTuple

#: A mnemonic is any short unique slug (H.3). `.` and `/` are the two characters it may not
#: contain, because they are already the handle separator and the collection/doc separator.
MNEMONIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: `PREFIX.<tag><n>` - the whole string, anchored. The tag is an optional short run of letters that
#: splits one mnemonic into **sub-series**: a doc whose items are questions, decisions and beats
#: writes `VFB.Q5`, `VFB.D4`, `VFB.B1` rather than three mnemonics. It is capped at three letters so
#: an ordinary word after a dot (`OS.Design1`) cannot read as a handle.
TAG = r"[A-Za-z]{0,3}"
HANDLE_RE = re.compile(rf"^([A-Za-z0-9][A-Za-z0-9_-]*)\.({TAG})(0|[1-9][0-9]*)$")
#: `.<tag><n>` - the in-scope form, resolved against the enclosing doc's own mnemonic.
SCOPED_RE = re.compile(rf"^\.({TAG})(0|[1-9][0-9]*)$")
#: The same shape found loose in running text, for the mention sweep. Word-bounded on both sides so
#: `v1.4`, `Fig.2` and a version string cannot be read as handles unless the prefix is a real one.
MENTION_RE = re.compile(rf"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_-]*)\.({TAG})(0|[1-9][0-9]*)(?![0-9.])")

#: Files the mention sweep reads by default. Overridable per collection (`register.sweep_ext`).
DEFAULT_SWEEP_EXT = (".yaml", ".yml", ".md", ".rs", ".py", ".svg", ".toml", ".html", ".txt")
#: Never swept: build output, version control, caches and vendored trees.
SWEEP_SKIP_DIRS = {"dist", ".git", "__pycache__", "node_modules", ".venv", "venv", "target", ".yss"}


def canon(text: str) -> str:
    """The canonical spelling of a mnemonic or handle: uppercase."""
    return text.upper()


class Ref(NamedTuple):
    """A parsed handle. `VFB.Q5` -> mnemonic VFB, tag Q, number 5.

    The distinction that matters: **mnemonic** is what a doc declares and what makes a handle
    unique across the site; **series** is what an ordinal counts within. A doc with sub-series has
    one mnemonic and several series, so `VFB.D4` does not leave a gap at 4 in `VFB.Q`.
    """

    mnemonic: str
    tag: str
    number: int

    @property
    def series(self) -> str:
        """The key gap checks and mint pointers count within: `VFB.Q`, or `PLN` when untagged."""
        return f"{self.mnemonic}.{self.tag}" if self.tag else self.mnemonic

    @property
    def handle(self) -> str:
        return f"{self.mnemonic}.{self.tag}{self.number}"


def parse_handle(text: str) -> Ref | None:
    """`'pln.7'` -> `Ref('PLN', '', 7)`; `'vfb.q5'` -> `Ref('VFB', 'Q', 5)`; else None."""
    match = HANDLE_RE.match(text.strip())
    if not match:
        return None
    return Ref(canon(match.group(1)), canon(match.group(2)), int(match.group(3)))


def parse_scoped(text: str) -> tuple[str, int] | None:
    """`'.7'` -> `('', 7)`; `'.Q5'` -> `('Q', 5)`; anything else -> None."""
    match = SCOPED_RE.match(text.strip())
    return (canon(match.group(1)), int(match.group(2))) if match else None


def series_of(name: str) -> str:
    """Canonical form of a series key or mnemonic, as written in config or on a doc."""
    return canon(str(name).strip())


def format_handle(mnemonic: str, number: int, tag: str = "") -> str:
    return Ref(canon(mnemonic), canon(tag), number).handle


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

    handle: str  #: canonical `PLN.7` / `VFB.Q5`
    spelled: str  #: as the file wrote it
    mnemonic: str  #: the doc's declared prefix - `VFB`
    tag: str  #: the sub-series letters, `` when there are none - `Q`
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
    def series(self) -> str:
        """What this handle's ordinal counts within: `VFB.Q`, or `PLN` when untagged."""
        return f"{self.mnemonic}.{self.tag}" if self.tag else self.mnemonic

    @property
    def archived(self) -> bool:
        return self.tier == "archived"


@dataclass
class Mention:
    """A handle written outside a doc item. Resolves to a location, never to a quote."""

    handle: str
    spelled: str
    mnemonic: str
    tag: str
    number: int
    path: str
    line: int
    known: bool = False  #: whether a bound handle of this name exists

    @property
    def series(self) -> str:
        return f"{self.mnemonic}.{self.tag}" if self.tag else self.mnemonic


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
        ref = parse_handle(text)
        return self.handles.get(ref.handle) if ref else None

    def resolve(self, text: str, doc_id: str | None = None) -> Handle | None:
        """`PLN.7` anywhere, or `.7` / `.Q5` against `doc_id`'s own mnemonic."""
        scoped = parse_scoped(text)
        if scoped is not None:
            tag, number = scoped
            mnemonic = self._by_doc.get(doc_id or "")
            return self.handles.get(format_handle(mnemonic, number, tag)) if mnemonic else None
        return self.get(text)

    def mnemonic_of(self, doc_id: str) -> str | None:
        return self._by_doc.get(doc_id)

    def series(self, name: str) -> list[Handle]:
        """Every handle in a series key (`VFB.Q`) or under a whole mnemonic (`VFB`)."""
        key = series_of(name)
        return sorted(
            (h for h in self.handles.values() if h.series == key or h.mnemonic == key),
            key=lambda h: (h.tag, h.number),
        )

    def series_keys(self, mnemonic: str | None = None) -> list[str]:
        """Every series key, or the keys belonging to one mnemonic. `PLN` has one; `VFB` may have five."""
        keys = {h.series for h in self.handles.values()
                if mnemonic is None or h.mnemonic == series_of(mnemonic)}
        return sorted(keys)

    def next_number(self, series_key: str) -> int:
        """The mint pointer: one past the highest number this *series* has reached, live or archived.

        Takes a series key, not a mnemonic: a doc with sub-series mints independently in each, and
        answering `VFB` rather than `VFB.Q` would hand out a number the wrong series already used.
        """
        key = series_of(series_key)
        numbers = [h.number for h in self.handles.values() if h.series == key]
        return (max(numbers) + 1) if numbers else 1

    def gaps(self, series_key: str) -> list[int]:
        """Missing numbers below the high-water mark. Append-only means a gap is a missing row."""
        key = series_of(series_key)
        numbers = {h.number for h in self.handles.values() if h.series == key}
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
            if not isinstance(spelled, str) or not (ref := parse_handle(spelled)):
                errors.append(
                    f"{source}: at {field_name}: '{spelled}' is not a handle (expected PREFIX.<n>, "
                    f"or PREFIX.<tag><n> with up to three tag letters)")
                continue
            if own is None:
                errors.append(
                    f"{source}: at {field_name}: item carries handle '{spelled}' but the doc "
                    f"declares no mnemonic"
                )
                continue
            if ref.mnemonic != own:
                errors.append(
                    f"{source}: at {field_name}: handle '{spelled}' does not use this doc's "
                    f"mnemonic '{index.mnemonics[own].spelled}'"
                )
                continue
            _claim(index, Handle(
                handle=ref.handle, spelled=spelled, mnemonic=ref.mnemonic, tag=ref.tag,
                number=ref.number, doc_id=doc_id, item_id=item.get("id"), item=item,
                source=source, tier="live", field=field_name,
            ), errors)

    for source, doc in archived:
        for field_name, item in _walk_items(doc):
            spelled = item.get("handle")
            if not isinstance(spelled, str):
                continue
            ref = parse_handle(spelled)
            if not ref:
                errors.append(
                    f"{source}: at {field_name}: '{spelled}' is not a handle (expected PREFIX.<n>, "
                    f"or PREFIX.<tag><n> with up to three tag letters)")
                continue
            owner = index.mnemonics.get(ref.mnemonic)
            if owner is None:
                errors.append(
                    f"{source}: at {field_name}: archived handle '{spelled}' uses mnemonic "
                    f"'{ref.mnemonic}', which no doc declares"
                )
                continue
            _claim(index, Handle(
                handle=ref.handle, spelled=spelled, mnemonic=ref.mnemonic, tag=ref.tag,
                number=ref.number, doc_id=owner.doc_id, item_id=item.get("id"), item=item,
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
                tag, number = canon(match.group(2)), int(match.group(3))
                handle = format_handle(mnemonic, number, tag)
                found.append(Mention(
                    handle=handle, spelled=match.group(0), mnemonic=mnemonic, tag=tag,
                    number=number, path=rel, line=lineno, known=handle in index.handles,
                ))
    return found


def unregistered(mentions: Iterable[Mention], allowed: Iterable[str] = ()) -> list[Mention]:
    """Mentions of a handle no doc item binds and no `unminted` declaration excuses."""
    excused = {canon(a) for a in allowed}
    return [m for m in mentions if not m.known and m.handle not in excused]
