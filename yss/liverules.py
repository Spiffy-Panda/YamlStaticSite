"""The live-rules generator: what still binds, projected out of a contradiction register.

The register is the instrument for *auditing history*. It lists every handle in a series, retired
ones included, **with their original confident wording preserved** beside the rules that survived.
An agent reading it to learn the rules ingests a large body of rules that no longer bind, stated as
though they did. That is the context poisoning this file exists to prevent: the generated list
carries only what is in force, and a retired handle reaches it as a bare number with no text - so a
spent number reads as spent rather than missing, without teaching anyone what it said.

Two properties are the whole value, and every design choice here serves one of them.

**Verbatim, never a second copy.** A rule's text is the register row's own `says`, or - when the row
leaves it out - the bound item's own words read out of the handle index. The generator stores no
third copy of any rule, so it cannot drift into disagreeing with its source.

**Fail, never drop.** A rule that vanishes to a missed pattern is indistinguishable, to the agent
reading the output, from a rule that does not exist. Every check below therefore raises instead of
skipping a row, and the caller exits non-zero with the reason.

Deterministic: nothing here reads the clock. `updated` is copied from the register and provenance
carries a digest, so a no-op run is byte-identical and `--check` still means something the next day.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import Collection, Config
from .handles import DEFAULT_SWEEP_EXT, MENTION_RE, HandleIndex, canon, parse_handle, series_of, sweep
from .loader import dump_yaml

#: The closed row-state vocabulary, and which section of the generated list each state reaches.
STATES = {
    "live": "in_force",
    "met": "settled",
    "answered": "settled",
    "recorded": "settled",
    "retired": "retired",
}
#: How a strike is written when a collection declares no `strike_forms` of its own. Every pattern
#: anchors on the handle, so a `~~` merely appearing earlier in the file cannot read as a retirement.
DEFAULT_STRIKE_FORMS = ("~~{handle}", "{handle} - RETIRED", "{handle} — RETIRED")

RETIRED_TAG = "*[retired]*"


class LiveRulesError(Exception):
    """One or more refusals. Every one names the row and what to do about it."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s)")


@dataclass
class Spec:
    """What one collection's register needs before anything can be generated."""

    collection: Collection
    doc_id: str
    out_id: str
    series: list[dict] = field(default_factory=list)
    sweep_ext: list[str] = field(default_factory=list)

    @property
    def prefixes(self) -> list[str]:
        return [canon(str(s.get("prefix", ""))) for s in self.series if s.get("prefix")]

    @property
    def unminted(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for entry in self.series:
            for item in entry.get("unminted") or []:
                if item.get("handle"):
                    out[canon(str(item["handle"]))] = str(item.get("why", ""))
        return out

    def strike_forms(self, series_key: str) -> tuple[str, ...]:
        key = series_of(series_key)
        for entry in self.series:
            declared = series_of(entry.get("prefix", ""))
            if declared == key or key.startswith(f"{declared}."):
                forms = entry.get("strike_forms")
                if forms:
                    return tuple(str(f) for f in forms)
        return DEFAULT_STRIKE_FORMS


def specs(cfg: Config) -> list[Spec]:
    """Every collection that declares a `register:` block. `--list` prints these."""
    out = []
    for collection in cfg.collections():
        block = collection.register
        if not block:
            continue
        out.append(Spec(
            collection=collection,
            doc_id=str(block.get("doc") or "register"),
            out_id=str(block.get("out") or "live-rules"),
            series=[dict(s) for s in block.get("series") or [] if isinstance(s, dict)],
            sweep_ext=[str(e) for e in block.get("sweep_ext") or []],
        ))
    return out


def find_spec(cfg: Config, name: str | None) -> Spec:
    found = specs(cfg)
    if not found:
        raise LiveRulesError(["no collection declares a register: block in its collection.yaml"])
    if name is None:
        if len(found) > 1:
            names = ", ".join(s.collection.id or "<root>" for s in found)
            raise LiveRulesError([f"more than one collection has a register ({names}); name one"])
        return found[0]
    for spec in found:
        if (spec.collection.id or "") == name or spec.collection.id == name.strip("/"):
            return spec
    raise LiveRulesError([f"no collection '{name}' with a register: block"])


# --- the checks -------------------------------------------------------------
def _check_shape(register: dict, where: str, spec: Spec, problems: list[str]) -> None:
    if not register.get("updated"):
        problems.append(f"{where}: the register has no `updated:`; the generated files copy it")
    if not isinstance(register.get("entries"), list) or not register["entries"]:
        problems.append(f"{where}: the register has no `entries:` - its shape changed")
    # `covers:` is the register's own statement of what it is responsible for, and the collection's
    # `register.series` is what the generator acts on. Both exist so the doc is self-describing; a
    # mismatch is refused rather than silently resolved, because either one could be the wrong half.
    covers = {canon(str(c)) for c in register.get("covers") or []}
    if covers and spec.prefixes and covers != set(spec.prefixes):
        problems.append(
            f"{where}: `covers: {sorted(covers)}` disagrees with the collection's register series "
            f"{sorted(spec.prefixes)}. Fix whichever is wrong; this will not guess.")


def _check_rows(register: dict, where: str, spec: Spec, index: HandleIndex,
                root: Path, problems: list[str]) -> dict[str, dict]:
    """Row-level refusals. Returns the rows that survived, keyed by canonical handle."""
    rows: dict[str, dict] = {}
    for i, row in enumerate(register.get("entries") or []):
        at = f"{where}: entries[{i}]"
        raw = row.get("subject")
        state = row.get("state")
        if not raw or not state:
            problems.append(f"{at}: a row needs both `subject:` and `state:`")
            continue
        ref = parse_handle(str(raw))
        if not ref:
            problems.append(f"{at}: '{raw}' is not a handle (expected PREFIX.<n> or PREFIX.<tag><n>)")
            continue
        if spec.prefixes and not _covered(ref, spec.prefixes):
            problems.append(f"{at}: '{raw}' is not in a series this register covers ({', '.join(spec.prefixes)})")
            continue
        handle = ref.handle
        if handle in rows:
            problems.append(f"{at}: '{raw}' already has a row; two rows may not share a number")
            continue
        if state not in STATES:
            problems.append(f"{at}: state '{state}' is outside the vocabulary ({', '.join(STATES)})")
            continue

        bound = index.get(handle)
        if "says" in row and not str(row.get("says") or "").strip():
            problems.append(f"{at}: `says:` is present but empty; a rule with no text is worse than no rule")
            continue
        if not str(row.get("says") or "").strip() and not (bound and bound.text):
            problems.append(
                f"{at}: '{raw}' has no `says:` and names no bound item whose words could be quoted; "
                f"the generator will not emit a rule with no text")
            continue

        lives_in = row.get("lives_in") or (bound.source if bound else None)
        if not lives_in:
            problems.append(f"{at}: '{raw}' has no `lives_in:` and no bound item to take one from")
            continue
        # Collection root first, then the repo root - the same order evidence `path:` claims use.
        home = next((c for c in (spec.collection.root / str(lives_in), root / str(lives_in)) if c.exists()), None)
        if home is None:
            problems.append(f"{at}: lives_in '{lives_in}' does not exist")
            continue

        text = home.read_text(encoding="utf-8", errors="replace")
        spelled = bound.spelled if bound else str(raw)
        if not _mentions(text, ref):
            problems.append(
                f"{at}: '{raw}' is not mentioned in {lives_in}; the register has drifted from the "
                f"file the handle is supposed to live in")
            continue

        struck = _struck(text, spelled, spec.strike_forms(ref.series))
        if state == "retired" and not struck:
            problems.append(
                f"{at}: '{raw}' is `retired` here but not struck in place in {lives_in}. Record the "
                f"verdict where the handle lives first.")
            continue
        if state != "retired" and struck:
            problems.append(
                f"{at}: '{raw}' is struck in place in {lives_in} but this row says `{state}`. One of "
                f"the two is wrong and it is not this program's place to pick which.")
            continue
        row = dict(row)
        row["_ref"] = ref
        row["_handle"] = handle
        row["_bound"] = bound
        row["_lives_in"] = str(lives_in)
        rows[handle] = row
    return rows


def _covered(ref, prefixes: Iterable[str]) -> bool:
    """A register may cover a whole mnemonic (`VFB`) or one sub-series of it (`VFB.Q`)."""
    wanted = {series_of(p) for p in prefixes}
    return ref.mnemonic in wanted or ref.series in wanted


def _mentions(text: str, ref) -> bool:
    """Case-insensitively, because one handle has two spellings and only one namespace."""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_.-]){re.escape(ref.mnemonic)}\.{re.escape(ref.tag)}{ref.number}(?![0-9.])",
        re.IGNORECASE)
    return bool(pattern.search(text))


def _struck(text: str, spelled: str, forms: Iterable[str]) -> bool:
    """Strike marks are matched exactly, never fuzzily, and always anchored on the handle."""
    for form in forms:
        needle = form.replace("{handle}", spelled)
        if needle in text:
            return True
        if needle.lower() in text.lower():
            return True
    return False


def _check_series(rows: dict[str, dict], spec: Spec, index: HandleIndex, root: Path,
                  problems: list[str]) -> None:
    """Gaps in a series, and handles written in the collection with no row (R.10)."""
    # Gaps are counted per *series key*, not per mnemonic: a doc whose items are questions and
    # decisions mints `VFB.Q1..` and `VFB.D1..` independently, and `VFB.D4` leaves no gap at 4 in Q.
    keys = sorted({row["_ref"].series for row in rows.values()})
    for key in keys:
        numbers = {row["_ref"].number for row in rows.values() if row["_ref"].series == key}
        missing = [n for n in range(1, max(numbers) + 1) if n not in numbers]
        if missing:
            problems.append(
                f"{key}: gap in the series at {', '.join(str(n) for n in missing)}. Append-only "
                f"means a gap is a missing row, not a free number.")

    excused = spec.unminted
    only = spec.collection.root.relative_to(root).as_posix() if spec.collection.root != root else None
    mentions = sweep(root, index, spec.sweep_ext or DEFAULT_SWEEP_EXT, only=only)
    for mention in mentions:
        if spec.prefixes and not _covered(mention, spec.prefixes):
            continue
        if mention.handle in rows or mention.handle in excused:
            continue
        problems.append(
            f"{mention.path}:{mention.line}: '{mention.spelled}' is written here with no row on the "
            f"register. Add the row, or declare it `unminted` with the reason it is named but unspent.")


def _check_pairs(register: dict, where: str, rows: dict[str, dict], index: HandleIndex,
                 problems: list[str]) -> list[dict]:
    kept: list[dict] = []
    for i, pair in enumerate(register.get("pairs") or []):
        at = f"{where}: pairs[{i}] ({pair.get('id', '?')})"
        sides = [pair.get("a") or {}, pair.get("b") or {}]
        ok = True
        subjects: list[str] = []
        for label, side in zip("ab", sides):
            raw = side.get("subject")
            ref = parse_handle(str(raw or ""))
            if not ref:
                problems.append(f"{at}: side {label} names no handle")
                ok = False
                continue
            handle = ref.handle
            subjects.append(handle)
            if handle not in rows:
                problems.append(f"{at}: side {label} names '{raw}', which has no row on the register")
                ok = False
            if not str(side.get("quote") or "").strip():
                problems.append(
                    f"{at}: side {label} has no quote. A contradiction that cannot be quoted is one "
                    f"an agent invented, and inventing them is worse than missing them.")
                ok = False
            elif index.get(handle) is None:
                problems.append(
                    f"{at}: side {label} names '{raw}', which is a mention rather than a bound "
                    f"handle. A mention has no boundary, so there is nothing honest to quote.")
                ok = False
        if not ok:
            continue
        closed = str(pair.get("state") or "").strip().lower().startswith("closed")
        retired = [h for h in subjects if rows[h].get("state") == "retired"]
        if closed and not retired:
            problems.append(f"{at}: marked closed, but neither subject is retired")
            continue
        if not closed and retired:
            problems.append(
                f"{at}: left open, but {', '.join(retired)} is retired. Say what ended it - and both "
                f"quotes stay standing.")
            continue
        kept.append(pair)
    return kept


# --- the projection ---------------------------------------------------------
def _rule_text(row: dict) -> str:
    """Verbatim: the register's own `says`, else the bound item's own words. Never a third copy."""
    says = str(row.get("says") or "").strip()
    if says:
        return says
    return row["_bound"].text


def _tag_retired(text: str, retired: set[str]) -> str:
    """Tag a reference to a retired handle inside a live item's prose.

    Rule 8 says the note is not ours to rewrite, so nothing is removed - a marker is added, and only
    where the surrounding text does not already say so. Without this a live note can assert that a
    retired handle still binds, which is exactly the poisoning this file exists to stop.
    """
    def mark(match: re.Match) -> str:
        ref = parse_handle(match.group(0))
        if ref is None or ref.handle not in retired:
            return match.group(0)
        tail = text[match.end():match.end() + 40].lower()
        if "retired" in tail or RETIRED_TAG in tail:
            return match.group(0)
        return f"{match.group(0)} {RETIRED_TAG}"

    return MENTION_RE.sub(mark, text)


def project(register: dict, rows: dict[str, dict], pairs: list[dict], spec: Spec) -> dict:
    """Build the generated doc. Pure: same inputs, same bytes, no clock."""
    retired = {h for h, r in rows.items() if r.get("state") == "retired"}
    in_force, settled, spent = [], [], {}

    for handle in sorted(rows, key=lambda h: (rows[h]["_ref"].series, rows[h]["_ref"].number)):
        row = rows[handle]
        ref = row["_ref"]
        if row["state"] == "retired":
            spent.setdefault(ref.series, []).append(ref.number)
            continue
        entry = {
            # `subject`, not `handle`, for the same reason the register uses it: this list cites a
            # handle, it does not declare one. Only the item that owns a handle spells it `handle:`.
            "subject": handle,
            "says": _tag_retired(_rule_text(row), retired),
            "lives_in": row["_lives_in"],
        }
        if row.get("note"):
            entry["note"] = _tag_retired(str(row["note"]), retired)
        if row["state"] != "live":
            entry["state"] = row["state"]
        (in_force if row["state"] == "live" else settled).append(entry)

    tensions = []
    for pair in pairs:
        subjects = [parse_handle(str(side["subject"])).handle for side in (pair["a"], pair["b"])]
        if str(pair.get("state") or "").lower().startswith("closed"):
            continue
        if any(h in retired for h in subjects):
            continue  # a tension whose subject is spent is not something a working agent can act on
        tensions.append({
            "id": pair["id"],
            "kind": pair["kind"],
            "state": pair.get("state", ""),
            "a": {"subject": subjects[0], "quote": pair["a"]["quote"]},
            "b": {"subject": subjects[1], "quote": pair["b"]["quote"]},
            "resolves": pair.get("resolves", ""),
        })

    return {
        "kind": "generic",
        "id": spec.out_id,
        "title": f"Live rules - {register.get('title', spec.doc_id)}",
        "summary": "Generated. What still binds, projected from the contradiction register; retired rules reach this list as bare numbers.",
        "visibility": register.get("visibility", "private"),
        "status": "active",
        "updated": register.get("updated"),
        "shape": "live-rules",
        # R.8: the staleness gate ships with the output. Nothing runs the generator for you; this
        # claim is what makes `yss check --run-commands` say so.
        "evidence": [{
            "command": f"python -m yss live-rules {spec.collection.id or ''} --check".replace("  ", " ").strip(),
            "expect": 0,
            "note": "regenerates in memory and fails when this file no longer matches the register",
        }],
        "agent_notes": (
            "GENERATED - never hand-edit; every edit is lost on the next run of "
            "`python -m yss live-rules`. Edit the register and re-run. This is the file a working "
            "agent loads: the register is for auditing history, this is what binds right now."
        ),
        "data": {
            "generated_from": {
                "doc": spec.doc_id,
                "digest": digest(register),
            },
            "in_force": in_force,
            "settled": settled,
            "open_tensions": tensions,
            "retired": [
                {"series": mnemonic, "numbers": sorted(numbers)}
                for mnemonic, numbers in sorted(spent.items())
            ],
        },
    }


def digest(register: dict) -> str:
    """A stable fingerprint of the register's meaningful content (no `_`-prefixed load keys)."""
    stripped = {k: v for k, v in register.items() if not str(k).startswith("_")}
    return hashlib.sha256(dump_yaml(stripped).encode("utf-8")).hexdigest()


# --- entry point ------------------------------------------------------------
def generate(cfg: Config, docs: dict[str, dict], index: HandleIndex, spec: Spec) -> tuple[Path, str]:
    """(path, yaml text) for the generated doc, or raise LiveRulesError with every refusal."""
    doc_id = spec.collection.doc_id(spec.doc_id) if not spec.collection.is_root else spec.doc_id
    register = docs.get(doc_id)
    if register is None:
        raise LiveRulesError([f"no register doc '{doc_id}'"])
    where = register.get("_source", doc_id)

    problems: list[str] = []
    _check_shape(register, where, spec, problems)
    if problems:
        raise LiveRulesError(problems)
    rows = _check_rows(register, where, spec, index, cfg.root, problems)
    _check_series(rows, spec, index, cfg.root, problems)
    pairs = _check_pairs(register, where, rows, index, problems)
    if problems:
        raise LiveRulesError(problems)

    out = project(register, rows, pairs, spec)
    header = (
        "# GENERATED by `python -m yss live-rules` - do not hand-edit.\n"
        f"# Projected from {where}; edit that and re-run. Deterministic: nothing here reads a clock.\n"
    )
    return spec.collection.docs_dir / f"{spec.out_id}.yaml", header + dump_yaml(out)


def write(path: Path, text: str) -> bool:
    """True when the file changed. A no-op run rewrites nothing."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def is_stale(path: Path, text: str) -> bool:
    return not path.exists() or path.read_text(encoding="utf-8") != text
