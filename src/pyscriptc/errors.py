"""Diagnostics for lowering and LLVM emission."""

from __future__ import annotations

import ast
from typing import Any


class SpikeError(Exception):
    """Base error. `code` is stable; tests and callers match on it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class IRError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__("PS0001", message)


class VersionError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__("PS3001", message)


class FitError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__("PS3002", message)


class CheckFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


def _stamp_span(node: ast.AST, origin: ast.AST) -> None:
    """Copy `origin`'s source position onto `node`.

    Context nodes (`ast.Load`, `ast.Store`, `ast.Del`) are skipped on
    purpose. CPython's parser hands out one shared instance per context
    kind for the whole process, and the parser never sets positions on
    them (their `_attributes` is empty). Stamping one therefore writes to
    a process-global: every later parse of any file sees the stale
    position, and the diagnostics that point at it land in the wrong
    source. Only the nodes that carry a position of their own are
    stamped.

    This lives in `errors` (not `lower._core`) because the restlower
    handlers need it and importing `lower` from `restlower` is a cycle.
    """
    if isinstance(node, ast.expr_context):
        return
    node.lineno = origin.lineno
    node.col_offset = origin.col_offset
    node.end_lineno = origin.end_lineno
    node.end_col_offset = origin.end_col_offset


def span_of(node: Any, filename: str) -> dict[str, Any]:
    line = getattr(node, "lineno", 1) or 1
    col0 = getattr(node, "col_offset", 0) or 0
    end_line = getattr(node, "end_lineno", line) or line
    end_col0 = getattr(node, "end_col_offset", col0) or col0
    return {
        "file": filename,
        "line": line,
        "col": col0 + 1,
        "endLine": end_line,
        "endCol": end_col0 + 1,
    }


def format_diagnostic(diag: dict[str, Any], source: str | None = None) -> str:
    span = diag["span"]
    header = f"{span['file']}:{span['line']}:{span['col']}: {diag['code']}: {diag['message']}"
    if source is None:
        return header
    lines = source.splitlines()
    if not 1 <= span["line"] <= len(lines):
        return header
    text = lines[span["line"] - 1]
    caret_at = max(span["col"] - 1, 0)
    return f"{header}\n  {text}\n  {' ' * caret_at}^"
