"""Module-level helpers for the stdlib dispatch area.

`_release_erased_import` and `_module_assigned` are shared by the binding
mixin and the static-statement walk in `__init__.py`; they live on a leaf
module so both sides import them without a cycle.
"""

from __future__ import annotations

import ast
from typing import Any

_ERASED_MODULES = frozenset({"typing", "typing_extensions", "abc"})


def _release_erased_import(stmt: ast.Import | ast.ImportFrom, rebound: set[str]) -> None:
    """An erased import binds the typing name again. A previous user assignment no longer hides it."""
    if isinstance(stmt, ast.ImportFrom):
        if stmt.module in _ERASED_MODULES and not stmt.level:
            for alias in stmt.names:
                if alias.name == "TYPE_CHECKING":
                    rebound.discard(alias.asname or alias.name)
        return
    for alias in stmt.names:
        root = alias.name.split(".", 1)[0]
        if root in _ERASED_MODULES:
            rebound.discard(alias.asname or root)


def _module_assigned(stmt: ast.stmt) -> set[str]:
    """Names this module statement binds. Function and class bodies do not bind module names."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        names: set[str] = set()
        for target in stmt.targets:
            names.update(_store_names(target))
        return names
    if isinstance(stmt, ast.AnnAssign):
        return _store_names(stmt.target)
    if isinstance(stmt, ast.AugAssign):
        return _store_names(stmt.target)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _store_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        names = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                names.update(_store_names(item.optional_vars))
        return names
    if isinstance(stmt, ast.If):
        names = set()
        for part in (*stmt.body, *stmt.orelse):
            names.update(_module_assigned(part))
        return names
    return set()


def _synth_module(class_name: str) -> str:
    if class_name in {"Date", "DateTime", "Time", "TimeDelta"}:
        return "datetime"
    if class_name == "Path":
        return "pathlib"
    if class_name in {"TextIO", "BytesIO"}:
        return "io"
    if class_name == "Deque":
        return "collections"
    if class_name == "Hmac":
        return "hmac"
    if class_name == "CsvWriter":
        return "csv"
    return "builtins"


def _mod_call(
    module: str, name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "module", "module": module, "name": name},
        "args": args,
        "type": typ,
        "span": span,
    }


def _store_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names.update(_store_names(elt))
        return names
    return set()
