"""Diagnostics for lowering and LLVM emission."""

from __future__ import annotations

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
