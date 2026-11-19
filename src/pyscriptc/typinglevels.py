# SPDX-License-Identifier: Apache-2.0
"""The typing report of the quality lane (issue #1315).

Typing is a quality lane, never an admission lane: the report says what
the typing surface of a project looks like, it never changes what
compiles, what lowers or the exit code of a compile.

There is one switch, `[tool.pyscriptc.typing] mode`, with two values:

- `standard` (the default): the typing findings surface in the report;
- `off`: the report carries no typing finding.

The scan itself is a static AST pass over the project's own files. It
reports the two families the lowerer cares about:

- the missing annotations (the PS1001 family): a parameter or a return
  that carries no type;
- the construct forms the lowerer fences in the annotation itself
  (PS1103, PS1104, PS1105).

The names the scan knows are not written here: they are read from the
lowerer's own tables (the dynamic-annotation set, the taken aliases and
the erased modules), so the fence taxonomy has one source and cannot
drift from what the compiler actually does.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The two modes of the typing report.
MODES: list[str] = ["standard", "off"]

# The mode a project gets when it configures nothing.
DEFAULT_MODE = "standard"


class TypingModeError(ValueError):
    """A malformed typing mode."""


def check_mode(mode: str) -> str:
    """The mode name, or an error naming the known modes."""
    if mode not in MODES:
        raise TypingModeError(f"unknown typing mode {mode!r} (known: {'|'.join(MODES)})")
    return mode


@dataclass
class TypingPolicy:
    """The resolved typing policy of a project: the mode and nothing else.

    The policy has no per-path resolution: the mode is the project's
    (the config file's directory is the only thing a project-level table
    could ever vary by, and a per-path table is surface without a
    consumer, issue #1315).
    """

    mode: str = DEFAULT_MODE

    def enabled(self) -> bool:
        """True when the mode surfaces the typing findings."""
        return self.mode != "off"


@dataclass
class Finding:
    """One typing finding a report surfaces."""

    code: str
    family: str
    message: str
    severity: str = "warning"
    file: str = ""
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        """The JSON row of a finding."""
        return {
            "code": self.code,
            "family": self.family,
            "message": self.message,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
        }


def _lowerer_tables() -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    """The lowerer's own fence tables: the dynamic names, the taken aliases,
    the taken bare aliases and the modules whose names spell typing shapes.

    The tables are the lowerer's (issue #1315): the scan reads them so the
    report and the compiler cannot name the same construct two ways.
    """

    from pyscriptc.lower._annotations import (
        _DYNAMIC_ANNOTATIONS,
        _NEVER_ALIASES,
        _TAKEN_ALIASES,
        _TAKEN_BARE_ALIASES,
    )
    from pyscriptc.lower._stdlib_shared import _ERASED_MODULES

    dynamic = frozenset(_DYNAMIC_ANNOTATIONS - _NEVER_ALIASES)
    return dynamic, frozenset(_TAKEN_ALIASES), frozenset(_TAKEN_BARE_ALIASES), frozenset(_ERASED_MODULES)


def _typing_imports(tree: ast.Module) -> set[str]:
    """The names a module imports from a typing-spelling module.

    A name the module imported from `typing` (or `collections.abc`, or
    the shadow annotation module) is a typing shape the lowerer resolves
    through its alias table; a name it did not import is the module's own
    class, which the scan says nothing about.
    """

    erased = _lowerer_tables()[3]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in erased and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _annotation_of(node: ast.AST) -> ast.AST | None:
    """The annotation an AST node carries, or None."""
    if isinstance(node, ast.AnnAssign):
        return node.annotation
    if isinstance(node, ast.arg):
        return node.annotation
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.returns
    return None


def _annotation_names(node: ast.AST) -> list[str]:
    """The names an annotation expression mentions."""
    names: list[str] = []
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            names.append(part.id)
        elif isinstance(part, ast.Attribute):
            names.append(part.attr)
    return names


def _typing_bindings(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """The typing names a module binds: the alias names and the module names.

    The alias map is the local name -> the `typing` name it stands for
    (`from typing import Sequence as S` binds `S` -> `Sequence`); the
    module map is the local name -> the erased module it stands for
    (`import typing as t` binds `t` -> `typing`). Both are the lowerer's
    own bindings, read the same way (issue #1315).
    """

    erased = _lowerer_tables()[3]
    aliases: dict[str, str] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in erased and not node.level:
                for alias in node.names:
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = alias.name
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in erased:
                    modules[alias.asname or root] = alias.name
                elif alias.name in erased:
                    modules[alias.asname or alias.name] = alias.name
    return aliases, modules


def _resolved_alias(node: ast.AST, aliases: dict[str, str], modules: dict[str, str]) -> tuple[str, bool]:
    """The typing name an annotation node reads, and whether it is a typing name.

    A bare name reads the alias it is bound to; an attribute reads the
    member of the erased module it is qualified by (`typing.Any` reads
    `Any`). The flag says the name came from a typing-spelling module
    (a `from typing import X` or an `import typing`), so the scan can
    tell a typing shape from the module's own class.
    """

    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id], True
        return node.id, False
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in modules:
            return node.attr, True
        return node.attr, False
    return "", False


def scan_tree(tree: ast.Module, path: Path) -> list[Finding]:
    """The static typing findings of one module.

    The scan is the cheap half of the report: the missing annotations the
    lowerer would fence (the PS1001 family) and the construct forms that
    fence in the annotation itself (PS1103, PS1104, PS1105). It never
    lowers anything, so the report stays cheap on a big tree.
    """

    dynamic, taken, bare_taken, _erased = _lowerer_tables()
    aliases, modules = _typing_bindings(tree)
    findings: list[Finding] = []
    file_name = path.as_posix()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        plain = args.posonlyargs + args.args
        defaults: list[Any] = [None] * (len(plain) - len(args.defaults))
        defaults.extend(args.defaults)
        for arg, default in zip(plain, defaults, strict=False):
            if arg.annotation is not None or default is not None:
                continue
            if arg.arg in ("self", "cls"):
                continue
            findings.append(
                Finding(
                    code="PS1001",
                    family="param-inference",
                    message=f"parameter {arg.arg} needs an annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
            if arg.annotation is not None or default is not None:
                continue
            findings.append(
                Finding(
                    code="PS1001",
                    family="param-inference",
                    message=f"parameter {arg.arg} needs an annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
        if node.returns is None:
            findings.append(
                Finding(
                    code="PS1001",
                    family="return-inference",
                    message=f"{node.name} needs a return annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
    for node in ast.walk(tree):
        annotation = _annotation_of(node)
        if annotation is None:
            continue
        line = getattr(node, "lineno", 0)
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            findings.append(
                Finding(
                    code="PS1105",
                    family="forward-reference",
                    message=f"the quoted annotation {annotation.value!r} is not resolved at compile time",
                    file=file_name,
                    line=line,
                )
            )
            continue
        if isinstance(annotation, ast.Subscript):
            # A subscripted alias the lowerer takes is fine; a typing alias
            # it does not take fences with PS1103.
            base, from_typing = _resolved_alias(annotation.value, aliases, modules)
            if base and from_typing and base not in taken:
                findings.append(
                    Finding(
                        code="PS1103",
                        family="generic-alias",
                        message=f"the generic alias {base} is not a container the compiler takes",
                        file=file_name,
                        line=line,
                    )
                )
            continue
        if isinstance(annotation, (ast.Name, ast.Attribute)):
            name, from_typing = _resolved_alias(annotation, aliases, modules)
            if name == "object" and not from_typing:
                # The builtin `object` erases to the tagged dynamic value;
                # only the `typing` spelling of it fences.
                continue
            if name in dynamic:
                findings.append(
                    Finding(
                        code="PS1104",
                        family="dynamic-annotation",
                        message=f"the annotation names the dynamic type {name}",
                        file=file_name,
                        line=line,
                    )
                )
            elif from_typing and name not in bare_taken:
                findings.append(
                    Finding(
                        code="PS1103",
                        family="generic-alias",
                        message=f"the alias {name} is not a type the compiler takes",
                        file=file_name,
                        line=line,
                    )
                )
            continue
        for name in _annotation_names(annotation):
            resolved, from_typing = _resolved_alias(ast.Name(id=name, ctx=ast.Load()), aliases, modules)
            if from_typing and resolved in dynamic:
                findings.append(
                    Finding(
                        code="PS1104",
                        family="dynamic-annotation",
                        message=f"the annotation names the dynamic type {resolved}",
                        file=file_name,
                        line=line,
                    )
                )
                break
    return findings


def scan_file(path: Path) -> list[Finding]:
    """The static typing findings of one file, or none when it does not parse."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    return scan_tree(tree, path)


def typing_report(policy: TypingPolicy, files: list[Path], findings: list[Finding]) -> dict[str, Any]:
    """The typing section of a quality report.

    `files` are the files of the tree, `findings` the static findings of
    the whole tree (each carrying its file). The section names the project
    mode and the findings the mode surfaces. The `off` mode surfaces none.
    """

    surfaced: list[Finding] = []
    if policy.enabled():
        surfaced = list(findings)
    return {
        "mode": policy.mode,
        "findings": [finding.as_dict() for finding in surfaced],
        "summary": {
            "files": len(files),
            "findings": len(surfaced),
        },
    }
