"""Test-case reducer: shrink a Python source to a minimal reproducer.

The reducer is a delta-debugging loop over the AST. It removes one construct at a
time (a module-level statement, an import name, a statement inside a function or a
branch), re-runs the predicate on the candidate, and keeps the removal when the
predicate still holds. A removal that makes the predicate false is rolled back.

A predicate answers one question: does this variant still reproduce the bug?
Three shapes are supported:

* `--predicate CMD` runs a shell command with the variant in `PSC_SOURCE`.
  The command exits 0 when the variant still reproduces.
* `--fence PSxxxx` holds when lowering the variant still reports a fence with
  that code.
* `--crash` holds when lowering the variant raises a non-fence error.

The loop is monotone: the predicate is checked against the starting variant
first, and every candidate that fails it is discarded. The reducer never
mutates the input file.
"""

from __future__ import annotations

import ast
import copy
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyscriptc.errors import IRError, SpikeError
from pyscriptc.lower import lower_source

SOURCE_ENV = "PSC_SOURCE"

#: Default wall-clock budget for one predicate command.
DEFAULT_TIMEOUT = 120.0

#: Statements that own a suite, so an emptied suite needs a `pass` to stay valid.
_SUITE_OWNERS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.If,
    ast.While,
    ast.For,
    ast.AsyncFor,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.ExceptHandler,
)

#: The suite fields of a compound statement.
_SUITE_FIELDS = ("body", "orelse", "finalbody")


@dataclass(frozen=True)
class Verdict:
    """The answer of a predicate: does the variant still reproduce?"""

    holds: bool
    reason: str = ""

    def __str__(self) -> str:
        return "holds" if self.holds else f"fails: {self.reason}"


class Predicate:
    """Base class for a predicate: `verdict(text)` is the whole contract."""

    def verdict(self, text: str) -> Verdict:
        """Whether the variant reproduces, and why."""
        raise NotImplementedError

    def holds(self, text: str) -> bool:
        return self.verdict(text).holds

    def describe(self) -> str:
        return type(self).__name__


@dataclass
class CommandPredicate(Predicate):
    """Run a shell command on a candidate file; exit 0 means it reproduces.

    The command receives the candidate's path in `PSC_SOURCE`. The file is
    a temporary sibling of the input (same suffix), deleted after the run.
    """

    command: str
    directory: Path = field(default_factory=Path.cwd)
    suffix: str = ".py"
    timeout: float = DEFAULT_TIMEOUT

    def verdict(self, text: str) -> Verdict:
        path = Path(tempfile.mkstemp(suffix=self.suffix, dir=self.directory)[1])
        try:
            path.write_text(text, encoding="utf-8")
            env = dict(os.environ)
            env[SOURCE_ENV] = str(path)
            try:
                proc = subprocess.run(
                    self.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return Verdict(False, "timeout")
            except OSError, subprocess.SubprocessError:
                return Verdict(False, "command error")
        finally:
            path.unlink(missing_ok=True)
        if proc.returncode == 0:
            return Verdict(True)
        return Verdict(False, f"exit {proc.returncode}")

    def describe(self) -> str:
        return f"predicate: {self.command}"


@dataclass
class FencePredicate(Predicate):
    """Hold when lowering the variant still reports a fence with this code."""

    code: str

    def verdict(self, text: str) -> Verdict:
        try:
            module = lower_source(text, "candidate.py")
        except SpikeError as exc:
            return Verdict(False, exc.code)
        except SyntaxError:
            return Verdict(False, "syntax error")
        except Exception as exc:  # a lowering bug is a crash, not a fence
            return Verdict(False, type(exc).__name__)
        for fence in module.get("fences", []):
            if fence.get("code") == self.code:
                return Verdict(True)
        return Verdict(False, f"no fence {self.code}")

    def describe(self) -> str:
        return f"fence: {self.code}"


@dataclass
class CrashPredicate(Predicate):
    """Hold when lowering the variant raises a non-fence error."""

    def verdict(self, text: str) -> Verdict:
        try:
            lower_source(text, "candidate.py")
        except SpikeError as exc:
            return Verdict(True, exc.code)
        except SyntaxError:
            return Verdict(True, "SyntaxError")
        except Exception as exc:
            return Verdict(True, type(exc).__name__)
        return Verdict(False, "no crash")

    def describe(self) -> str:
        return "crash: lowering raises a non-fence error"


@dataclass
class Step:
    """One removal attempt in the log."""

    step: int
    kind: str
    detail: str
    kept: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "kind": self.kind,
            "detail": self.detail,
            "kept": self.kept,
            "reason": self.reason,
        }


@dataclass
class Reduction:
    """The outcome of a reduction run."""

    source: str
    original: str
    verdict: Verdict
    predicate: str = ""
    steps: list[Step] = field(default_factory=list)
    detail: str = ""
    candidates: int = 0

    @property
    def changed(self) -> bool:
        return self.source != self.original

    def as_dict(self) -> dict[str, Any]:
        return {
            "predicate": self.predicate,
            "original": self.original,
            "reduced": self.source,
            "changed": self.changed,
            "candidates": self.candidates,
            "verdict": str(self.verdict),
            "detail": self.detail,
            "steps": [step.as_dict() for step in self.steps],
        }


class _Candidate:
    """One removable construct: a description and the tree without it."""

    def __init__(self, kind: str, detail: str, apply: Callable[[], ast.Module | None]) -> None:
        self.kind = kind
        self.detail = detail
        self._apply = apply

    def tree(self) -> ast.Module | None:
        """The candidate tree, or None when the removal is not expressible."""
        return self._apply()


def _render(tree: ast.Module) -> str:
    """The source text for a tree, with a trailing newline."""
    text = ast.unparse(tree)
    return text if text.endswith("\n") else text + "\n"


def _is_docstring(stmt: ast.stmt, index: int) -> bool:
    """The first statement of a body is a docstring when it is a bare string."""
    if index != 0:
        return False
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _resolve(tree: ast.Module, path: tuple[tuple[Any, ...], ...]) -> list[ast.stmt]:
    """The statement list the path points at, as the live list in `tree`."""
    container: list[ast.stmt] = tree.body
    for hop in path:
        stmt = container[hop[1]]
        if hop[0] == "body":
            container = stmt.body
        elif hop[0] == "orelse":
            container = stmt.orelse
        elif hop[0] == "finalbody":
            container = stmt.finalbody
        elif hop[0] == "handler":
            container = stmt.handlers[hop[2]].body
    return container


def _fill_suites(node: ast.AST) -> None:
    """Give every emptied suite a `pass`, so the tree still parses."""
    for child in ast.iter_child_nodes(node):
        _fill_suites(child)
    if not isinstance(node, _SUITE_OWNERS):
        return
    for field_name in _SUITE_FIELDS:
        suite = getattr(node, field_name, None)
        if isinstance(suite, list) and not suite:
            suite.append(ast.Pass())


def _drop_at(tree: ast.Module, path: tuple[tuple[Any, ...], ...], index: int) -> ast.Module | None:
    """A copy of `tree` without the statement at `path`/`index`."""
    clone = copy.deepcopy(tree)
    container = _resolve(clone, path)
    if index >= len(container):
        return None
    del container[index]
    _fill_suites(clone)
    ast.fix_missing_locations(clone)
    return clone


def _bound_names(stmt: ast.stmt) -> set[str]:
    """The names a statement binds."""
    names: set[str] = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(stmt.name)
    elif isinstance(stmt, ast.Import):
        names.update(alias.asname or alias.name.split(".")[0] for alias in stmt.names)
    elif isinstance(stmt, ast.ImportFrom):
        names.update(alias.asname or alias.name for alias in stmt.names if alias.name != "*")
    elif isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            names.update(_target_names(target))
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        names.update(_target_names(stmt.target))
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        names.update(_target_names(stmt.target))
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        for item in stmt.items:
            if item.optional_vars is not None:
                names.update(_target_names(item.optional_vars))
    elif isinstance(stmt, ast.ExceptHandler) and stmt.name:
        names.add(stmt.name)
    elif isinstance(stmt, (ast.Global, ast.Nonlocal)):
        names.update(stmt.names)
    return {name for name in names if isinstance(name, str)}


def _target_names(target: ast.expr) -> set[str]:
    """The names a target expression binds."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for element in target.elts for name in _target_names(element)}
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _reads_at(node: ast.AST) -> list[tuple[int, int, str]]:
    """Every name read in `node`, with its source position."""
    out: list[tuple[int, int, str]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            out.append((child.lineno, child.col_offset, child.id))
    return out


def _read_after(tree: ast.Module, stmt: ast.stmt) -> set[str]:
    """The names read after `stmt` ends, anywhere in the module."""
    end_line = getattr(stmt, "end_lineno", None) or stmt.lineno
    end_col = getattr(stmt, "end_col_offset", None) or stmt.col_offset
    out: set[str] = set()
    for line, col, name in _reads_at(tree):
        if line > end_line or (line == end_line and col >= end_col):
            out.add(name)
    return out


def _describe(stmt: ast.stmt) -> str:
    """A short human label for a statement."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"def {stmt.name}"
    if isinstance(stmt, ast.ClassDef):
        return f"class {stmt.name}"
    if isinstance(stmt, ast.Import):
        return f"import {', '.join(alias.name for alias in stmt.names)}"
    if isinstance(stmt, ast.ImportFrom):
        return f"from {stmt.module or '.'} import {', '.join(alias.name for alias in stmt.names)}"
    if isinstance(stmt, ast.Assign):
        targets = ", ".join(ast.unparse(target) for target in stmt.targets)
        return f"assign {targets}"
    if isinstance(stmt, ast.AnnAssign):
        return f"assign {ast.unparse(stmt.target)}"
    if isinstance(stmt, ast.AugAssign):
        return f"augassign {ast.unparse(stmt.target)}"
    if isinstance(stmt, ast.Return):
        return "return"
    if isinstance(stmt, ast.If):
        return f"if {ast.unparse(stmt.test)}"
    if isinstance(stmt, ast.While):
        return f"while {ast.unparse(stmt.test)}"
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return f"for {ast.unparse(stmt.target)}"
    if isinstance(stmt, ast.Try):
        return "try"
    if isinstance(stmt, ast.Expr):
        return f"expr {ast.unparse(stmt.value)[:40]}"
    if isinstance(stmt, ast.Raise):
        return "raise"
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return "with"
    return type(stmt).__name__.lower()


def _path_label(path: tuple[tuple[Any, ...], ...]) -> str:
    """A human label for a statement path."""
    if not path:
        return "module"
    return ".".join(str(hop[1]) for hop in path)


def _collect_paths(tree: ast.Module) -> list[tuple[tuple[tuple[Any, ...], ...], list[ast.stmt]]]:
    """Every statement list in the module, with the path that reaches it."""
    out: list[tuple[tuple[tuple[Any, ...], ...], list[ast.stmt]]] = []

    def walk(path: tuple[tuple[Any, ...], ...], body: list[ast.stmt]) -> None:
        out.append((path, body))
        for index, stmt in enumerate(body):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk((*path, ("body", index)), stmt.body)
            elif isinstance(stmt, ast.If):
                walk((*path, ("body", index)), stmt.body)
                walk((*path, ("orelse", index)), stmt.orelse)
            elif isinstance(stmt, (ast.While, ast.For, ast.AsyncFor)):
                walk((*path, ("body", index)), stmt.body)
                walk((*path, ("orelse", index)), stmt.orelse)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                walk((*path, ("body", index)), stmt.body)
            elif isinstance(stmt, ast.Try):
                walk((*path, ("body", index)), stmt.body)
                for pos, handler in enumerate(stmt.handlers):
                    walk((*path, ("handler", index, pos)), handler.body)
                walk((*path, ("orelse", index)), stmt.orelse)
                walk((*path, ("finalbody", index)), stmt.finalbody)

    walk((), list(tree.body))
    return out


def _candidates(tree: ast.Module) -> list[_Candidate]:
    """Every removable construct in the module, module level first."""
    out: list[_Candidate] = []
    for path, body in _collect_paths(tree):
        label = _path_label(path)
        for index, stmt in enumerate(body):
            if _is_docstring(stmt, index):
                continue
            bound = _bound_names(stmt)
            if bound & _read_after(tree, stmt):
                continue  # still referenced: removing it would change the program
            detail = f"{label}: {_describe(stmt)}" if label != "module" else _describe(stmt)
            out.append(
                _Candidate(
                    "statement",
                    detail,
                    lambda p=path, i=index, t=tree: _drop_at(t, p, i),
                )
            )
            if isinstance(stmt, ast.ImportFrom) and len(stmt.names) > 1:
                for pos, alias in enumerate(stmt.names):
                    out.append(
                        _Candidate(
                            "import-name",
                            f"{label}: {alias.asname or alias.name} from {stmt.module or '.'}",
                            lambda p=path, i=index, s=pos, t=tree: _drop_import_name(t, p, i, s),
                        )
                    )
    return out


def _drop_import_name(
    tree: ast.Module,
    path: tuple[tuple[Any, ...], ...],
    index: int,
    position: int,
) -> ast.Module | None:
    """A copy of `tree` without one name of a `from ... import a, b`."""
    clone = copy.deepcopy(tree)
    container = _resolve(clone, path)
    if index >= len(container):
        return None
    stmt = container[index]
    if not isinstance(stmt, ast.ImportFrom) or position >= len(stmt.names):
        return None
    names = [alias for pos, alias in enumerate(stmt.names) if pos != position]
    replacement = ast.ImportFrom(module=stmt.module, names=names, level=stmt.level)
    ast.copy_location(replacement, stmt)
    container[index] = replacement
    _fill_suites(clone)
    ast.fix_missing_locations(clone)
    return clone


def reduce_source(source: str, predicate: Predicate, max_steps: int = 10_000) -> Reduction:
    """Reduce `source` to a variant that still satisfies `predicate`.

    The loop is monotone: every candidate is tried once, and a candidate that
    fails the predicate is discarded. Each pass walks the whole module, so a
    removal at module level cannot hide a removal inside a function body.
    """
    original = source
    verdict = predicate.verdict(source)
    if not verdict.holds:
        return Reduction(
            source=source,
            original=original,
            predicate=predicate.describe(),
            verdict=verdict,
            detail="the input does not satisfy the predicate; nothing to reduce",
            candidates=0,
        )

    current = source
    steps: list[Step] = []
    number = 0
    progress = True
    while progress and number < max_steps:
        progress = False
        for candidate in _candidates(ast.parse(current)):
            if number >= max_steps:
                break
            tree = candidate.tree()
            if tree is None:
                continue
            text = _render(tree)
            if text == current:
                continue
            try:
                ast.parse(text)
            except SyntaxError:
                continue
            number += 1
            outcome = predicate.verdict(text)
            steps.append(Step(number, candidate.kind, candidate.detail, outcome.holds, outcome.reason))
            if outcome.holds:
                current = text
                progress = True
                break
    final = predicate.verdict(current)
    return Reduction(
        source=current,
        original=original,
        predicate=predicate.describe(),
        steps=steps,
        verdict=final,
        detail=f"{sum(1 for step in steps if step.kept)} of {len(steps)} removals kept",
        candidates=number,
    )


class _BesideInput:
    """Sentinel: write the reduced source beside the input file."""


DEFAULT_OUTPUT = _BesideInput()


def reduce_file(
    path: str | Path,
    predicate: Predicate,
    output: str | Path | None | _BesideInput = DEFAULT_OUTPUT,
    max_steps: int = 10_000,
) -> Reduction:
    """Reduce the file at `path` and write the result to `output`.

    The input file is never modified. The default writes `<name>.min.py`
    beside the input; `output=None` writes nothing.
    """
    source_path = Path(path)
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IRError(f"cannot read {source_path}: {exc}") from exc
    result = reduce_source(source, predicate, max_steps=max_steps)
    if isinstance(output, _BesideInput):
        target: Path | None = source_path.with_name(f"{source_path.stem}.min.py")
    elif output is None:
        target = None
    else:
        target = Path(output)
    if target is not None:
        target.write_text(result.source, encoding="utf-8")
    return result
