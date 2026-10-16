"""Leaf definitions shared by every module in the `lower` package.

`Fence`, `Binding`, and the small pure helpers live here so sibling modules
(`_stdlib.py`, `__init__.py`) can import them without a cycle. Moved verbatim
from `lower.py`; no signature or behavior change.
"""

from __future__ import annotations

import ast
from typing import Any


class Fence(Exception):
    def __init__(self, code: str, message: str, span: dict[str, Any], function: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span
        self.function = function

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "function": self.function,
            "span": self.span,
        }


class Binding:
    def __init__(
        self,
        kind: str,
        module: str,
        attr: str,
        queued: bool,
        typ: dict[str, Any] | None = None,
        rooted: bool = False,
    ) -> None:
        self.kind = kind
        self.module = module
        self.attr = attr
        self.queued = queued
        self.type = typ
        # True when `import urllib.parse` binds `urllib`, not the whole module.
        self.rooted = rooted


_FUTURE_OK = frozenset(
    {
        "annotations",
        "generator_stop",
        "nested_scopes",
        "generators",
        "division",
        "absolute_import",
        "with_statement",
        "print_function",
        "unicode_literals",
    }
)


def _package_base(module_name: str, is_package: bool, level: int) -> list[str] | None:
    if level < 1 or not module_name:
        return None
    bits = module_name.split(".")
    if not is_package:
        bits = bits[:-1]
    drop = level - 1
    if drop > len(bits):
        return None
    return bits[: len(bits) - drop]


def _is_none_expr(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None
