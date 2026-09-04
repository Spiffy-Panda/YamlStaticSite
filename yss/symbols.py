"""Resolve a code map export to its line range by parsing, never importing.

`yss check` and the `symbols` dynamic source both need to turn a (module path, export name)
pair into a line range. Importing the module would execute its top level, and it still would
not answer for constants or for dotted members such as `Config.evidence_for`, so this walks
the AST instead. Nothing here runs project code.

Names understood for a file path (`yss/config.py`):

    deep_merge              a top-level function or class
    Config.evidence_for     a member of a top-level class
    DEFAULTS                a top-level assignment
    load_yaml               a name re-exported by an `import ... from`

and for a package directory (`yss/providers/`):

    buildinfo.collect       `collect` inside yss/providers/buildinfo.py
    collect                 a name in that package's __init__.py

Only Python is parsed. `supported()` says whether a code map module can carry line ranges at
all; a javascript or jinja module gets a plain file link and no range (adr-024).
"""
from __future__ import annotations

import ast
from pathlib import Path

#: Suffixes this resolver can parse. A code map module with any other suffix is linkable but
#: not seekable, and callers should fall back to a whole-file link.
SUPPORTED_SUFFIXES = frozenset({".py"})

#: (start, end) within one file.
Range = tuple[int, int]
#: (start, end, file) - what `index_for` returns, because a code map module may name a package
#: directory while the export itself lives in one of its files.
Located = tuple[int, int, str]


class SymbolError(Exception):
    """A module could not be parsed. Carries a repo-relative path only, never an absolute one.

    The message reaches dynamic envelopes and the public build scans its own output for the
    checkout path, so an absolute path in here would fail the deploy (adr-004).
    """

    def __init__(self, rel: str, reason: str) -> None:
        super().__init__(f"{rel}: {reason}")
        self.rel = rel
        self.reason = reason


def _start(node: ast.AST) -> int:
    """First line a reader should see: the earliest decorator, else the definition itself."""
    decorators = getattr(node, "decorator_list", None) or []
    return min([node.lineno, *(d.lineno for d in decorators)])


def _range(node: ast.AST) -> Range:
    return (_start(node), getattr(node, "end_lineno", None) or node.lineno)


def _assigned_names(node: ast.AST) -> list[str]:
    """Top-level constant names bound by an assignment (`DEFAULTS = {...}`, `X: int = 1`)."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def file_index(path: Path, rel: str | None = None) -> dict[str, Range]:
    """Every name a code map may cite in one Python file, mapped to its line range.

    Scoped deliberately: only the module's own top level and one level of class members, so a
    helper named `collect` nested inside another function can never shadow the real export.
    """
    rel = rel or path.name
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SymbolError(rel, type(exc).__name__) from exc
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SymbolError(rel, "SyntaxError") from exc

    index: dict[str, Range] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index[node.name] = _range(node)
        elif isinstance(node, ast.ClassDef):
            index[node.name] = _range(node)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    index[f"{node.name}.{member.name}"] = _range(member)
                else:
                    for name in _assigned_names(member):
                        index.setdefault(f"{node.name}.{name}", _range(member))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assigned_names(node):
                index.setdefault(name, _range(node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # A re-export points at the import line: it is where this module makes the name its own.
            for alias in node.names:
                if alias.name == "*":
                    continue
                index.setdefault(alias.asname or alias.name.split(".")[0], _range(node))
    return index


_CACHE: dict[tuple[str, float], dict[str, Located]] = {}


def _mtime(target: Path) -> float:
    """Newest mtime under a path, so a cached index expires when any parsed file changes."""
    try:
        if target.is_dir():
            return max([target.stat().st_mtime, *(c.stat().st_mtime for c in target.glob("*.py"))])
        return target.stat().st_mtime
    except (OSError, ValueError):
        return 0.0


def index_for(root: Path, rel: str) -> dict[str, Located]:
    """Resolve one code map `modules[].path` - a file or a package directory - to its names.

    For a package, a submodule's names are prefixed (`buildinfo.collect`) and the package's own
    `__init__.py` contributes bare names, which is how `yss/providers/` is cited today. Each entry
    names the file it was found in, so a deep link points at that file and not at the directory.
    """
    rel = rel.strip().rstrip("/")
    target = root / rel
    key = (str(target), _mtime(target))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = _index_uncached(target, rel)
    _CACHE[key] = result
    return result


def _index_uncached(target: Path, rel: str) -> dict[str, Located]:
    if target.is_dir():
        index: dict[str, Located] = {}
        for child in sorted(target.glob("*.py")):
            child_rel = f"{rel}/{child.name}"
            names = file_index(child, child_rel)
            prefix = "" if child.stem == "__init__" else f"{child.stem}."
            index.update({f"{prefix}{name}": (span[0], span[1], child_rel) for name, span in names.items()})
        return index
    if target.suffix not in SUPPORTED_SUFFIXES:
        return {}
    return {name: (span[0], span[1], rel) for name, span in file_index(target, rel).items()}


def supported(rel: str) -> bool:
    """Can a module at this path carry line ranges? A directory counts if it holds Python."""
    rel = rel.strip().rstrip("/")
    return rel.endswith("/") or Path(rel).suffix in SUPPORTED_SUFFIXES or not Path(rel).suffix


def lookup(root: Path, rel: str, name: str) -> Located | None:
    """(start, end, file) for one export, or None when the name is not defined where the doc says."""
    try:
        return index_for(root, rel).get(name)
    except SymbolError:
        return None
