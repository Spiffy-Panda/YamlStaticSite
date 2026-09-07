"""Python symbol index, by `ast`. Nothing is imported, so no module-level code runs.

Names understood for one file:

    deep_merge              a top-level function or class
    Config.evidence_for     a member of a top-level class
    DEFAULTS                a top-level assignment
    load_yaml               a name re-exported by an `import ... from`
"""
from __future__ import annotations

import ast
from pathlib import Path

from .base import Range, SymbolError

SUFFIXES = frozenset({".py"})
PACKAGE_STEMS = frozenset({"__init__"})
MEMBER_SEP = "."
ALIAS_SEPS = ("::",)
LABEL = "python"


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


def file_index(path: Path, rel: str) -> dict[str, Range]:
    """Every name a code map may cite in one Python file, mapped to its line range.

    Scoped deliberately: only the module's own top level and one level of class members, so a
    helper named `collect` nested inside another function can never shadow the real export.
    """
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
