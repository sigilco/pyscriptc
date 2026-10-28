"""Per-file analysis shared by `pyscriptc coverage` and `pyscriptc assess`.

A real package tree is not all Python the lowering can read: a file can
hold a syntax error, a null byte, a non-UTF-8 encoding or an encoding
declaration CPython honours but the scanner does not, a name can sit under
a path that is not a module name, and a `.py` path can be a directory or a
special file. Issue #730: one such file used to end the whole run with a
traceback and exit 1. The scanner walks the tree defensively, every file
yields either an analysis or an ``Unanalyzable`` reason, and the report
carries the failures as data so the commands can complete and summarize
them instead of crashing on the first bad file.
"""

from __future__ import annotations

import ast
import codecs
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_ENCODING_COMMENT = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.MULTILINE)
_BOM = codecs.BOM_UTF8


@dataclass(frozen=True)
class Unanalyzable:
    """One file the per-file analysis could not read or parse, and why."""

    path: Path
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"file": str(self.path), "reason": self.reason}


def _declared_encoding(data: bytes) -> str | None:
    """The PEP 263 coding declaration of a source buffer, if any."""
    for line in data.split(b"\n", 2)[:2]:
        match = _ENCODING_COMMENT.match(line)
        if match:
            return match.group(1).decode("ascii", "replace")
    return None


def read_source(path: Path) -> tuple[str | None, str | None]:
    """Read one Python source the way CPython would, or return a reason.

    Returns ``(text, None)`` on success and ``(None, reason)`` when the
    file cannot be turned into source text: unreadable, a directory or a
    special file, a non-UTF-8 encoding without a declaration that decodes
    it, or a declaration naming an unknown codec. A UTF-8 BOM is stripped,
    as CPython does.
    """

    try:
        if path.is_dir():
            return None, "path is a directory, not a Python file"
        if not path.exists():
            return None, "file does not exist (broken symlink)"
        # Issue #730: a FIFO or a device under a `.py` name blocks forever
        # on a read; only a regular file is source.
        if not path.is_file():
            return None, "not a regular file"
        data = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read file: {exc.strerror or exc}"
    if data.startswith(_BOM):
        data = data[len(_BOM) :]
    # A PEP 263 declaration is honoured the way CPython honours it: the
    # codec is validated even when the bytes happen to be valid UTF-8.
    declared = _declared_encoding(data)
    if declared is not None:
        try:
            codecs.lookup(declared)
        except LookupError:
            return None, f"unknown encoding declaration: {declared!r}"
        if declared.lower().replace("_", "-") not in ("utf-8", "utf8"):
            try:
                return data.decode(declared), None
            except UnicodeDecodeError:
                return None, f"cannot decode file with declared encoding {declared!r}"
            except LookupError:  # pragma: no cover - checked above
                return None, f"unknown encoding declaration: {declared!r}"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        if declared is None:
            return None, "invalid or missing encoding declaration (the file is not valid UTF-8)"
        return None, f"cannot decode file with declared encoding {declared!r}"


def parse_source(text: str, filename: str) -> tuple[ast.Module | None, str | None]:
    """Parse one source text, or return a reason: syntax error, null byte, nesting."""

    if "\x00" in text:
        return None, "source code string cannot contain null bytes"
    try:
        return ast.parse(text, filename=filename), None
    except SyntaxError as exc:
        return None, f"syntax error: {exc.msg} (line {exc.lineno or 1})"
    except RecursionError:
        return None, "expression nests too deeply to parse"
    except MemoryError:
        return None, "file too large to parse"
    except ValueError as exc:
        return None, f"cannot parse source: {exc}"


def iter_py_files(target: Path) -> Iterator[Path]:
    """Walk a tree defensively for ``*.py`` paths, without following links.

    `Path.rglob` follows symlinks: a self-referential link used to walk a
    directory into itself, and a dangling link surfaced later as a
    `FileNotFoundError` from the lowering. The walk is iterative, keeps
    its own seen-set of device/inode pairs, and never yields the same file
    twice.
    """

    stack = [target]
    seen: set[tuple[int, int]] = set()
    while stack:
        current = stack.pop()
        try:
            info = os.lstat(current)
        except OSError:
            continue
        key = (info.st_dev, info.st_ino)
        if current.is_symlink():
            # A link is judged by what it points at: a link to a directory
            # is walked once, a link to a module is the module, and a link
            # to nowhere is a named failure.
            try:
                resolved = current.resolve(strict=True)
            except OSError:
                if current.suffix == ".py":
                    yield current
                continue
            if resolved.is_dir():
                stack.append(resolved)
                continue
            if current.suffix == ".py":
                yield current
            continue
        if current.suffix == ".py":
            # A `.py` path is a module candidate even when it is a
            # directory, a FIFO or a device: the reader names it as a
            # failure instead of the walk skipping it.
            yield current
            continue
        if current.is_dir():
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = sorted(os.scandir(current), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name.startswith(".") or name == "__pycache__":
                    continue
                stack.append(Path(entry.path))


def scan(target: Path) -> tuple[list[Path], list[Unanalyzable]]:
    """Every analyzable ``*.py`` file under target, plus those that are not.

    A file yields an analysis when it reads and parses; everything else
    (unreadable, undecodable, a directory named ``*.py``, a syntax error,
    a null byte, over-deep nesting) lands in the failures list with its
    reason, so a report can name it instead of crashing.
    """

    files: list[Path] = []
    failures: list[Unanalyzable] = []
    for path in iter_py_files(target):
        text, reason = read_source(path)
        if text is None:
            failures.append(Unanalyzable(path, reason or "unreadable"))
            continue
        _tree, reason = parse_source(text, str(path))
        if reason is not None:
            failures.append(Unanalyzable(path, reason))
            continue
        files.append(path)
    return files, failures
