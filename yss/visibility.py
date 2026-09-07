"""Visibility filtering (public/private/on_resolution) and forbidden-string scanning.

Two of the three visibility values are *authored*: `public` and `private` are read literally off
whichever object carries them. The third, `on_resolution`, is *derived* - it is legal only on a
`doc.worksheet` and means "private while this sheet is still asking, public once it is answered"
(gh-37). A worksheet is written private because of its candid free text, and that reason does not
expire when the questions are answered; but the two things worth publishing from a finished sheet -
the prompt it was built to produce and which option was picked - are already structured data. So an
`on_resolution` sheet does not publish the authored document: it publishes a *projection* of it.

The projection rule, stated once here because it is the thing that decides what reaches `dist/`:

    Structured answers publish; prose written to steer or record a human deliberation does not.

Kept: everything a reader needs to see the deliberation as a record - `intro`, `warn`, the whole
`prompt` block, and per question `call`, `lead`, `help`, `applies_to`, `blocks`, `compare`, the
`options[]` as they were offered (including `why`, `pros` and `cons`) and, from the resolution,
`chose[]`, `applied`, `by` and `recorded_in[]`. A selection is an enum, so publishing it costs
nothing and needs no transcription.

Elided: `private_notes` (everywhere, as before), the doc's `agent_notes` (instructions to the agent
that wrote the sheet, never reader-facing), and every question's `resolution.summary` - free prose
an agent wrote about what happened, the one field on a resolved question that nobody vetted for
publication. For a `kind: text` question the answer itself was never persisted at all; what
publishes is the two-state marker `resolution.answered`, rendered as `<filled>` / `<empty>`.

`filter_for_target` applies the projection wherever it meets such a doc, so every caller - the
build's page render, the `data/docs/<id>.json` export, `yss export`, the symbols provider - gets the
projected document from one implementation. The projected copy is stamped `visibility: public`, so
projecting twice is the same as projecting once.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Callable

PRIVATE_KEYS = ("private_notes",)
ON_RESOLUTION = "on_resolution"
#: Doc-level keys dropped from an `on_resolution` worksheet when it publishes.
WORKSHEET_PRIVATE_KEYS = ("agent_notes",)
#: Keys dropped from each `questions[].resolution` when an `on_resolution` worksheet publishes.
RESOLUTION_PRIVATE_KEYS = ("summary",)
TEXT_SUFFIXES = {
    ".html", ".htm", ".json", ".css", ".js", ".txt", ".md", ".xml", ".svg",
    ".yaml", ".yml", ".csv", ".map", ".py", ".toml", ".cfg", ".ini",
}
DEFAULT_SKIP_DIRS = (".git", "__pycache__", "node_modules", ".yss", "dist", ".venv", "venv")

_REMOVED = object()


def question_requires_answer(question: Any) -> bool:
    """Would a reader have to answer this question? `required` defaults to true (worksheet schema)."""
    if not isinstance(question, dict):
        return False
    return question.get("required", True) is not False


def worksheet_resolved(doc: Any) -> bool:
    """Is every question that requires an answer answered?

    The predicate behind `visibility: on_resolution` and behind the nav's waiting count
    (`Renderer._page_state`), so the two can never disagree. A question is answered when it carries
    a `resolution`; a question with `required: false` is not asked of anybody and cannot hold the
    sheet closed. A worksheet with no questions at all is not "resolved" - there is nothing in it to
    have been decided, and flipping an empty sheet public would be a redaction hole, not an archive.
    """
    if not isinstance(doc, dict):
        return False
    questions = doc.get("questions")
    if not isinstance(questions, list) or not questions:
        return False
    return all(
        q.get("resolution") for q in questions if question_requires_answer(q)
    )


def is_derived_worksheet(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("visibility") == ON_RESOLUTION
        and obj.get("kind") == "worksheet"
    )


def is_visible(obj: Any, target: str) -> bool:
    if target != "public":
        return True
    if isinstance(obj, dict):
        visibility = obj.get("visibility", "public")
        if visibility == ON_RESOLUTION:
            # Derived, not authored: only a resolved worksheet publishes, and only a worksheet may
            # claim the keyword at all (the schema says so; anything else is treated as private
            # rather than trusted, so a misplaced keyword fails closed).
            return is_derived_worksheet(obj) and worksheet_resolved(obj)
        return visibility != "private"
    return True


def project_worksheet(doc: dict) -> dict:
    """The public form of a resolved `on_resolution` worksheet - see this module's docstring.

    Shallow-copies only what it rewrites; the caller owns `doc` (`_filter` has already made a copy).
    """
    out = dict(doc)
    out["visibility"] = "public"
    for key in WORKSHEET_PRIVATE_KEYS:
        out.pop(key, None)
    questions = out.get("questions")
    if isinstance(questions, list):
        projected = []
        for question in questions:
            if not isinstance(question, dict):
                projected.append(question)
                continue
            resolution = question.get("resolution")
            if isinstance(resolution, dict):
                question = dict(question)
                kept = {k: v for k, v in resolution.items() if k not in RESOLUTION_PRIVATE_KEYS}
                if (question.get("kind") or "radio") == "text":
                    # The free text was never persisted; the marker is the whole public answer.
                    kept["answered"] = bool(resolution.get("answered"))
                question["resolution"] = kept
            projected.append(question)
        out["questions"] = projected
    return out


def check_worksheets(docs: dict[str, Any]) -> list[str]:
    """Validate what the worksheet schema can only describe in prose (gh-37).

    1. `resolution.chose[]` must name an option `value` of its own question. The prefab falls back
       to printing the raw value, so a typo used to render as a plausible-looking answer that no
       option matched.
    2. On an `on_resolution` sheet a resolved `kind: text` question must carry `resolution.answered`.
       The free text is never published, so without the marker there is nothing publishable to say
       about that question and the sheet has no safe public form.
    3. `visibility: on_resolution` is legal only on a worksheet. The keyword is spelled in the
       envelope so one enum covers every kind, and this is what keeps it from being written on a
       doc with no questions - where it would silently mean "never public" (`is_visible` fails
       closed) rather than what the author meant.
    """
    errors: list[str] = []
    for doc_id, doc in sorted(docs.items()):
        if not isinstance(doc, dict):
            continue
        where = doc.get("_source", doc_id)
        if doc.get("kind") != "worksheet":
            if doc.get("visibility") == ON_RESOLUTION:
                errors.append(
                    f"{where}: at visibility: '{ON_RESOLUTION}' is derived from a worksheet's "
                    f"questions, so it is only legal on `kind: worksheet` (this doc is "
                    f"`kind: {doc.get('kind')}`); use public or private"
                )
            continue
        derived = doc.get("visibility") == ON_RESOLUTION
        for index, question in enumerate(doc.get("questions") or []):
            if not isinstance(question, dict):
                continue
            resolution = question.get("resolution")
            if not isinstance(resolution, dict):
                continue
            qid = question.get("id", index)
            values = {
                o.get("value")
                for o in (question.get("options") or [])
                if isinstance(o, dict)
            }
            for choice in resolution.get("chose") or []:
                if choice not in values:
                    known = ", ".join(sorted(v for v in values if isinstance(v, str))) or "none"
                    errors.append(
                        f"{where}: at questions/{index}/resolution/chose: '{choice}' is not an "
                        f"option `value` of question '{qid}' (options: {known})"
                    )
            if derived and (question.get("kind") or "radio") == "text" and "answered" not in resolution:
                errors.append(
                    f"{where}: at questions/{index}/resolution: question '{qid}' is `kind: text` on "
                    f"a `visibility: {ON_RESOLUTION}` worksheet, so its resolution must carry "
                    f"`answered: true|false` - the text itself is never published, the marker is"
                )
    return errors


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
        if target == "public" and is_derived_worksheet(data):
            # `is_visible` above has already established that it is resolved.
            out = project_worksheet(out)
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


# --- .gitignore matching (pragmatic subset; see build_gitignore_matcher docstring) -----------

class _Rule:
    __slots__ = ("regex", "negate")

    def __init__(self, regex: re.Pattern, negate: bool):
        self.regex = regex
        self.negate = negate


def _segment_to_regex(segment: str) -> str:
    """Translate one path segment (no '/') of a gitignore pattern into a regex fragment.

    Supports '*' (any run of non-'/' chars), '?' (one non-'/' char) and '[seq]'/'[!seq]'
    character classes. Anything else is escaped literally.
    """
    out = []
    i, n = 0, len(segment)
    while i < n:
        c = segment[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = segment.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                cls = segment[i + 1:j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _compile_gitignore_line(line: str) -> _Rule | None:
    """Compile one .gitignore line into a _Rule, or None for blank lines/comments.

    Supported: '#' comments, blank lines, '!' negation, a trailing '/' meaning "directory (and
    everything under it)", a leading '/' anchoring the pattern to the .gitignore's own directory
    (otherwise it matches at any depth), and '*', '?', '[seq]' globs within a path segment. A
    literal '**' segment matches zero or more path segments (so it can cross '/').

    Not supported: escaping of special characters with '\\', trailing-space significance,
    '.git/info/exclude', the user/global excludesfile, or gitattributes. These are documented
    gaps, not bugs - see build_gitignore_matcher.
    """
    line = line.rstrip("\n").rstrip()
    if not line or line.startswith("#"):
        return None
    negate = line.startswith("!")
    if negate:
        line = line[1:]
    if not line:
        return None
    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]
    if not line:
        return None
    anchored = line.startswith("/") or "/" in line
    line = line.lstrip("/")
    segments = line.split("/")
    parts = [".*" if seg == "**" else _segment_to_regex(seg) for seg in segments]
    body = "/".join(parts)
    if anchored:
        pattern = "^" + body + "(/.*)?$"
    else:
        pattern = "(^|.*/)" + body + "(/.*)?$"
    return _Rule(re.compile(pattern), negate)


def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _load_gitignore_rules(git_root: Path) -> dict[Path, list[_Rule]]:
    """Map each directory (at or under git_root) that has its own .gitignore to its compiled rules."""
    rules: dict[Path, list[_Rule]] = {}
    for gi in sorted(git_root.rglob(".gitignore")):
        if ".git" in gi.relative_to(git_root).parts:
            continue
        try:
            lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        compiled = [r for r in (_compile_gitignore_line(line) for line in lines) if r is not None]
        if compiled:
            rules[gi.parent] = compiled
    return rules


def build_gitignore_matcher(root: Path) -> Callable[[Path], bool]:
    """Build a predicate for whether an absolute `path` (somewhere under `root`) is gitignored.

    Walks up from `root` to find the enclosing git repository (a directory containing `.git`),
    then loads every `.gitignore` file at or below that repository root, applying them the way
    git does: files closer to the repository root are consulted first, rules within each file are
    applied in order, and the last matching rule (honouring `!` negation) wins. If no `.git`
    directory is found above `root`, the returned predicate always returns False - a no-op.

    Supported .gitignore syntax: blank lines, `#` comments, `!` negation, directory-only patterns
    (trailing `/`), root-anchored patterns (leading `/`, or any `/` elsewhere in the pattern),
    unanchored patterns (matched at any depth), `*`, `?`, `[seq]`/`[!seq]` globs, and a literal
    `**` segment (treated as "match zero or more path segments", which is close to but not
    identical to git's own `**` semantics for patterns like `a/**/b`).

    Explicitly NOT supported: backslash-escaped special characters, trailing-space escaping,
    `.git/info/exclude`, the user/global `core.excludesFile`, and `.gitattributes` export-ignore
    rules. A repository relying on those will under- or over-ignore relative to real git; treat
    this as a pragmatic best effort for a pre-publish scan, not a `git check-ignore` replacement.
    """
    git_root = _find_git_root(root)
    if git_root is None:
        return lambda path: False
    rule_dirs = _load_gitignore_rules(git_root)
    if not rule_dirs:
        return lambda path: False
    ordered_bases = sorted(rule_dirs.keys(), key=lambda d: len(d.parts))

    def matcher(path: Path) -> bool:
        ignored = False
        for base in ordered_bases:
            try:
                rel = path.relative_to(base)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            for rule in rule_dirs[base]:
                if rule.regex.match(rel_posix):
                    ignored = not rule.negate
        return ignored

    return matcher


def iter_text_files(
    root: Path,
    skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS,
    *,
    respect_gitignore: bool = False,
    extra_ignore_globs: tuple[str, ...] = (),
    stats: dict[str, Any] | None = None,
):
    """Yield text files under root, skipping (in this order):

    1. any path with a part in `skip_dirs` - the always-skip floor, regardless of gitignore;
    2. (if `extra_ignore_globs`) anything matching one of those fnmatch globs against the file's
       posix-style path relative to `root` (see `yss scan --ignore`);
    3. (if `respect_gitignore`) anything the enclosing git repository's `.gitignore` files would
       ignore - see `build_gitignore_matcher` for exactly what subset of gitignore syntax that
       honours, and for the no-op behaviour when `root` is not inside a git repository.

    When `stats` is passed, it is updated in place: `stats["default_dirs"]` collects the names of
    skip_dirs entries actually matched (a set), `stats["ignored_glob"]` and `stats["gitignored"]`
    count files skipped by steps 2 and 3 respectively. Files skipped for not looking like text
    (suffix not in TEXT_SUFFIXES) are not counted - that filter is noise reduction, not a
    visibility decision, and existed before this function had a `stats` parameter.
    """
    matcher = build_gitignore_matcher(root) if respect_gitignore else None
    if stats is not None:
        stats.setdefault("default_dirs", set())
        stats.setdefault("gitignored", 0)
        stats.setdefault("ignored_glob", 0)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        hit_default = next((part for part in rel_parts if part in skip_dirs), None)
        if hit_default is not None:
            if stats is not None:
                stats["default_dirs"].add(hit_default)
            continue
        if extra_ignore_globs:
            rel_posix = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel_posix, glob) for glob in extra_ignore_globs):
                if stats is not None:
                    stats["ignored_glob"] += 1
                continue
        if matcher is not None and matcher(path):
            if stats is not None:
                stats["gitignored"] += 1
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix != "":
            continue
        yield path


def format_skip_summary(stats: dict[str, Any] | None) -> str:
    """Render the stats dict from iter_text_files/scan_tree as one human-readable clause."""
    if not stats:
        return "skipped nothing"
    parts = []
    if stats.get("gitignored"):
        parts.append(f"{stats['gitignored']} files (gitignored)")
    if stats.get("ignored_glob"):
        parts.append(f"{stats['ignored_glob']} files (--ignore)")
    default_dirs = stats.get("default_dirs") or set()
    if default_dirs:
        parts.append(f"{len(default_dirs)} dirs (default)")
    return "skipped " + ", ".join(parts) if parts else "skipped nothing"


def scan_tree(
    root: Path,
    forbidden: list[str],
    flags: list[str],
    skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS,
    *,
    respect_gitignore: bool = False,
    extra_ignore_globs: tuple[str, ...] = (),
    stats: dict[str, Any] | None = None,
):
    """Scan text files under root. Returns (forbidden_hits, flag_hits) as (relpath, line, masked).

    `respect_gitignore` and `extra_ignore_globs` only affect which files are considered - they
    never change what happens to a file that IS scanned. Callers that must see everything (the
    build's own output scan) pass skip_dirs=() and leave respect_gitignore at its default False.
    """
    fhits, whits = [], []
    for path in iter_text_files(
        root,
        skip_dirs,
        respect_gitignore=respect_gitignore,
        extra_ignore_globs=extra_ignore_globs,
        stats=stats,
    ):
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
