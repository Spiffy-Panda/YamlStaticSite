"""Symbol index for the code map: which lines each cited export occupies, and what else is
defined near it.

Two sources come from this module, because the two targets have different sources of truth for
the source text itself (adr-024):

  `collect`        the index alone - path, export, [start, end] - plus a per-file table of every
                   name each cited file defines (`defs`) and its language (`langs`). Cheap, both
                   targets, no source text. The public reader pairs it with a commit-pinned fetch
                   from the hosting service.
  `collect_source` the same, plus the lines themselves, private target only, so the private site
                   reads its own working tree and needs no network at all.

`defs` is what turns the reader from a viewer into something you can navigate: an identifier in
the pane is a link when some file in the code map defines it (adr-038). It is names and line
numbers only - never text - so it is as publishable as the export list it extends. A repository
that would rather publish only what the code map names can set `defs: false` on the source.

Both honour `spec['_target']`: a code map module marked `visibility: private` must not appear in
a public index, and a name is all it would take to leak one.
"""
from __future__ import annotations

from pathlib import Path

from ..symbols import SymbolError, file_index, index_for, label_for, supported
from ..visibility import filter_for_target

#: Whole-file text is a convenience for the private reader, not an export format. These caps stop
#: one generated file from turning `dynamic/symbolsource.json` into a multi-megabyte download.
DEFAULT_MAX_FILE_LINES = 4000
DEFAULT_MAX_TOTAL_LINES = 60000


def _bases(cfg, doc: dict) -> list:
    """Where a doc's cited paths resolve from: its own collection first, then the repo root.

    The same order `yss check` uses for path evidence, so the index agrees with the checker about
    what a module path means.
    """
    bases = [cfg.root]
    cid = doc.get("_collection")
    if cid:
        try:
            bases.insert(0, cfg.collection(cid).root)
        except Exception:  # noqa: BLE001 - an unknown collection just falls back to the repo root
            pass
    return bases


def _codemap_modules(cfg, target: str) -> list[tuple[dict, object, str]]:
    """(module, base, repo-relative path) for every code map module the target may see."""
    from ..build import load_all

    loaded = load_all(cfg)
    docs = filter_for_target(loaded.docs, target) if target else loaded.docs
    modules = []
    for doc in docs.values():
        if doc.get("kind") != "codemap":
            continue
        for module in doc.get("modules") or []:
            path = (module.get("path") or "").strip()
            if not path:
                continue
            for base in _bases(cfg, doc):
                if (base / path).exists():
                    break
            else:
                base = cfg.root
            try:
                rel = (base / path).resolve().relative_to(cfg.root.resolve()).as_posix()
            except ValueError:
                rel = path  # outside the checkout: keep the doc's own spelling, never an absolute path
            modules.append((module, base, rel))
    return modules


def _relative(cfg, base, rel: str) -> str:
    """A path the site can link to: repo-relative, and never absolute (adr-004)."""
    try:
        return (base / rel).resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        return rel


def _defs_for(cfg, rel: str) -> dict[str, list[int]]:
    """Every name one file defines, spelled as that file spells it.

    Deliberately the file's own index rather than a slice of a directory index: inside
    `ballistics.rs` the constant is written `GRAVITY`, and `ballistics::GRAVITY` - which is how a
    code map entry naming the directory cites it - appears nowhere in the text a reader clicks on.
    """
    try:
        return {name: [span[0], span[1]] for name, span in file_index(cfg.root / rel, rel).items()}
    except SymbolError:
        return {}


def _index(cfg, target: str, want_defs: bool = True) -> dict:
    """{path: {export: [start, end, file]}} plus what could not be resolved, for visible modules.

    `file` is where the export is actually defined, which differs from the code map's own path
    whenever a module entry names a directory - a link has to point at the file.
    """
    by_path: dict[str, dict[str, list]] = {}
    unresolved: list[dict] = []
    errors: list[dict] = []
    unsupported: list[str] = []
    files: set[str] = set()

    for module, base, path in _codemap_modules(cfg, target):
        exports = [e for e in (module.get("exports") or []) if e.get("name")]
        if not supported(path):
            if exports:
                unsupported.append(path)
            continue
        try:
            available = index_for(base, module["path"].strip())
        except SymbolError as exc:
            # Relative path and exception name only: this string is written into the build output,
            # which the public target scans for the checkout path (adr-004).
            errors.append({"path": path, "error": exc.reason})
            continue
        found: dict[str, list] = {}
        for export in exports:
            name = export["name"]
            span = available.get(name)
            if span:
                found[name] = [span[0], span[1], _relative(cfg, base, span[2])]
            else:
                unresolved.append({"path": path, "export": name})
        if found:
            by_path[path] = found
        # Every file the module covers, cited or not: the reader may seek into a neighbour that
        # the code map itself never names.
        for span in available.values():
            files.add(_relative(cfg, base, span[2]))

    result = {"modules": by_path, "unresolved": unresolved, "errors": errors,
              "unsupported": unsupported,
              "langs": {rel: label_for(Path(rel).suffix) for rel in sorted(files)}}
    if want_defs:
        result["defs"] = {rel: _defs_for(cfg, rel) for rel in sorted(files)}
    return result


def collect(cfg, spec):
    """The index on its own - safe for the public target, which fetches the text elsewhere."""
    return _index(cfg, spec.get("_target") or "", want_defs=bool(spec.get("defs", True)))


def collect_source(cfg, spec):
    """The index plus the source lines, for a target that may read the working tree.

    Declared with `targets: [private]`. Guarded anyway: a misconfiguration that enabled this on a
    redacting target would put source text into the public output, so it refuses rather than obeys.
    """
    target = spec.get("_target") or ""
    if target and cfg.targets.get(target, {}).get("redact"):
        raise RuntimeError(f"source text is not available to redacting target '{target}'")

    result = _index(cfg, target, want_defs=bool(spec.get("defs", True)))
    max_lines = int(spec.get("max_lines", 400))
    # Keyed by the defining file, not by the code map's own path: a module entry may spell a
    # directory with a trailing slash that the resolved path does not carry, and the reader
    # already knows which file each export came from.
    text: dict[str, dict[str, dict]] = {}
    cache: dict[str, list[str]] = {}

    def lines_of(rel: str) -> list[str]:
        if rel not in cache:
            try:
                cache[rel] = (cfg.root / rel).read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                result["errors"].append({"path": rel, "error": type(exc).__name__})
                cache[rel] = []
        return cache[rel]

    for exports in result["modules"].values():
        for name, span in exports.items():
            start, end, file = span[0], span[1], span[2]
            body = lines_of(file)
            if not body:
                continue
            end = min(end, start + max_lines - 1)
            text.setdefault(file, {})[name] = {"start": start, "end": end, "file": file,
                                               "lines": body[start - 1:end]}
    result["text"] = text

    # Whole files, private only. This is what the local reader is for: with the file in hand it can
    # highlight a symbol in real context and jump to a definition three functions up without
    # asking anything for anything (adr-038). Capped, because a generated file has no upper bound.
    if spec.get("files", True):
        per_file = int(spec.get("max_file_lines", DEFAULT_MAX_FILE_LINES))
        budget = int(spec.get("max_total_lines", DEFAULT_MAX_TOTAL_LINES))
        whole: dict[str, dict] = {}
        skipped: list[str] = []
        for rel in result.get("langs", {}):
            body = lines_of(rel)
            if not body:
                continue
            if len(body) > per_file or len(body) > budget:
                skipped.append(rel)
                continue
            budget -= len(body)
            whole[rel] = {"lines": body, "lang": result["langs"].get(rel, "")}
        result["files"] = whole
        if skipped:
            result["files_skipped"] = skipped
    return result
