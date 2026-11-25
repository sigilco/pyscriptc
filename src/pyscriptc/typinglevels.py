# SPDX-License-Identifier: Apache-2.0
"""The typing strictness levels of the quality lane (issue #1315).

Typing is a quality lane, never an admission lane: a level changes which
typing findings a report surfaces, never what compiles, what lowers or the
exit code of a compile. The four levels are `off`, `basic`, `standard` and
`strict`, the shape Pyright converged on and Sorbet's per-file sigils
inspired.

Two mechanisms pick the level of a file:

- the project config, `[tool.pyscriptc.typing]`: `mode` sets the project
  default and the per-level glob lists (`strict = ["src/core/**"]`) set the
  level of the paths they match;
- the per-file pragma `# pyscriptc: typed=strict`, which may only raise the
  level of its file over the config, never lower it (the Pyright ratchet
  rule, issue 4321). A strict config therefore holds: a file that wants
  less has to be taken out of the strict globs, not downgraded in place.

What a level surfaces:

| level | typing findings | severity |
| ----- | --------------- | -------- |
| `off` | none | none |
| `basic` | missing annotations (the PS1001 family) | warning |
| `standard` | missing annotations, plus the coverage numbers | warning |
| `strict` | missing annotations and the construct-form family (PS1103, PS1104, PS1105, PS1106) | error |

The levels map onto the PEPs the annotation surface already covers: PEP 484
(function annotations) and PEP 526 (variable annotations) are the base
contract the lowerer consumes and the levels report on; PEP 561 (stub
resolution) is the third-party type source the checker preflight proves; the
PEP 585, 604, 647 and 695 construct forms are the construct-form family a
strict level reports more loudly. A level is a reporting emphasis, never new
syntax support.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyscriptc.builtins import EXCEPTIONS

# The four levels, from the loosest to the strictest.
LEVELS: list[str] = ["off", "basic", "standard", "strict"]

# The level a project gets when it configures nothing.
DEFAULT_LEVEL = "standard"

# The finding families the missing-annotation level reports. They are the
# PS1001 family: a parameter, a local or a return that carries no type.
MISSING_FAMILIES: list[str] = ["param-inference", "local-inference", "return-inference"]

# The construct-form family a strict level adds: the annotation shapes the
# lowerer does not take (generic aliases, dynamic annotations, quoted
# forward references and names the compiler does not know).
CONSTRUCT_FAMILIES: list[str] = ["generic-alias", "dynamic-annotation", "forward-reference", "unlowered-type-name"]

# The pragma that raises the level of one file.
PRAGMA_RE = re.compile(r"^#\s*pyscriptc:\s*typed\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")

# The builtin scalar annotations the lowerer takes as a whole name.
_SCALARS: frozenset[str] = frozenset({"int", "float", "bool", "str", "bytes"})

# The bare container annotations the lowerer takes: a bare `list`,
# `dict` or `set` names a shape whose element slots the body pins
# (issue #483). A bare `tuple` is not among them: a tuple's shape is
# its arity and the bare name carries none, so it fences.
_BARE_CONTAINERS: frozenset[str] = frozenset({"list", "dict", "set"})


def _glob_matches(pattern: str, text: str) -> bool:
    """True when the glob pattern covers the text.

    The pattern is a shell-style glob: `*` matches inside one path
    segment, `**` matches across segments and `?` matches one character
    that is not a separator. The matcher is written out rather than
    delegated to `fnmatch`, whose path semantics differ from the one the
    config documents.
    """

    return _glob_here(pattern, text)


def _glob_here(pattern: str, text: str) -> bool:
    """The glob match, walking the pattern against the text."""
    if pattern == "":
        return text == ""
    head = pattern[0]
    rest = pattern[1:]
    if head == "*":
        if rest.startswith("*"):
            # `**` crosses the path separators.
            trimmed = rest.lstrip("*")
            if trimmed == "":
                return True
            index = 0
            while index <= len(text):
                if _glob_here(trimmed, text[index:]):
                    return True
                index += 1
            return False
        # A single `*` stays inside one path segment.
        index = 0
        while index <= len(text):
            if index > 0 and text[index - 1] == "/":
                break
            if _glob_here(rest, text[index:]):
                return True
            index += 1
        return False
    if head == "?":
        if text == "" or text[0] == "/":
            return False
        return _glob_here(rest, text[1:])
    if text == "" or text[0] != head:
        return False
    return _glob_here(rest, text[1:])


class TypingLevelError(ValueError):
    """A malformed level name, pragma or config table."""


def rank(level: str) -> int:
    """The order of a level: `off` is 0, `strict` is 3."""
    if level not in LEVELS:
        raise TypingLevelError(f"unknown typing level {level!r} (known: {'|'.join(LEVELS)})")
    return LEVELS.index(level)


def check_level(level: str) -> str:
    """The level name, or an error naming the known levels."""
    if level not in LEVELS:
        raise TypingLevelError(f"unknown typing level {level!r} (known: {'|'.join(LEVELS)})")
    return level


def severity_for(level: str, family: str) -> str | None:
    """The severity a level reports a family with, or None when it suppresses it.

    `off` suppresses every typing family. `basic` and `standard` report the
    missing-annotation family as a warning. `strict` reports the missing
    family and the construct-form family as an error.
    """

    check_level(level)
    if level == "off":
        return None
    if family in MISSING_FAMILIES:
        return "error" if level == "strict" else "warning"
    if family in CONSTRUCT_FAMILIES:
        return "error" if level == "strict" else None
    return None


def reports_coverage(level: str) -> bool:
    """True when the level surfaces the coverage numbers of the report.

    The coverage numbers are the standard level's addition over basic: they
    say how much of the unannotated surface the pins would lift.
    """

    return level in ("standard", "strict")


@dataclass
class TypingPolicy:
    """The resolved typing policy of a project: a default level and globs.

    `base` is the directory the glob patterns are relative to (the config
    file's directory, or the working directory when there is no config).
    """

    mode: str = DEFAULT_LEVEL
    globs: dict[str, list[str]] = field(default_factory=dict)
    base: Path | None = None

    @classmethod
    def from_table(cls, table: Any, base: Path | None = None) -> TypingPolicy:
        """The policy a `[tool.pyscriptc.typing]` table names.

        The table holds the project default (`mode`) and one glob list per
        level. A level name the compiler does not know, or a glob list that
        is not a list of strings, is a `TypingLevelError`; the config
        layer turns that into its own PS3102 error.
        """

        data: dict[str, Any] = table if isinstance(table, dict) else {}
        mode: str = data.get("mode", DEFAULT_LEVEL)
        check_level(mode)
        globs: dict[str, list[str]] = {}
        for level in LEVELS:
            if level not in data:
                continue
            value: Any = data[level]
            if isinstance(value, str):
                globs[level] = [value]
            elif isinstance(value, list):
                items: list[str] = []
                for item in value:
                    if not isinstance(item, str):
                        raise TypingLevelError(f"{level} must be a glob or a list of globs")
                    items.append(item)
                globs[level] = items
            else:
                raise TypingLevelError(f"{level} must be a glob or a list of globs")
        return cls(mode=mode, globs=globs, base=base)

    def config_level(self, path: Path) -> str:
        """The level the config gives a path, before the pragma.

        The most specific matching pattern wins: the longest pattern that
        matches the path. Between equally long patterns the strictest level
        wins, so an overlapping list never lowers a level by accident.
        Without a match the project mode holds.
        """

        best_level = self.mode
        best_key: tuple[int, int, int] = (-1, -1, -1)
        rel = self._relative(path)
        for level in LEVELS:
            for index, pattern in enumerate(self.globs.get(level, [])):
                if not self._matches(pattern, rel, path):
                    continue
                key = (len(pattern), rank(level), index)
                if key > best_key:
                    best_key = key
                    best_level = level
        return best_level

    def level_for(self, path: Path, source: str) -> tuple[str, str]:
        """The level of a file and where it came from.

        The pragma may only raise the level over the config, so the level
        is the strictest of the two. The second element names the origin:
        `pragma`, `glob` or `mode`.
        """

        config_level = self.config_level(path)
        pragma_level = parse_pragma(source)
        if pragma_level is None:
            origin = "glob" if config_level != self.mode else "mode"
            return config_level, origin
        if rank(pragma_level) > rank(config_level):
            return pragma_level, "pragma"
        return config_level, "config"

    def _relative(self, path: Path) -> str:
        """The path relative to the config's directory, as a posix string."""
        if self.base is None:
            return path.as_posix()
        try:
            return path.resolve().relative_to(self.base.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def _matches(self, pattern: str, rel: str, path: Path) -> bool:
        """True when a glob pattern covers the path.

        A pattern matches the path itself, the path under it (a directory
        pattern covers everything below it) and the absolute path, so an
        absolute pattern works too.
        """

        normalized = pattern.rstrip("/")
        if _glob_matches(normalized, rel):
            return True
        if rel == normalized or rel.startswith(normalized + "/"):
            return True
        return _glob_matches(normalized, path.as_posix())


def parse_pragma(source: str) -> str | None:
    """The level the file's pragma asks for, or None when it has none.

    The pragma is a comment, so it is inert for CPython and for every other
    tool. Only a comment that reads exactly `# pyscriptc: typed=<level>`
    counts; a level that is not one of the four is an error, so a typo is
    never silently ignored.
    """

    found: str | None = None
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        match = PRAGMA_RE.match(stripped)
        if match is None:
            continue
        level = check_level(match.group(1))
        if found is None or rank(level) > rank(found):
            found = level
    return found


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


def _lowerer_tables() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The lowerer's own fence tables: the bare names, the subscripted aliases and the dynamic names.

    The tables are the lowerer's (issue #1315): the scan reads them so the
    report and the compiler cannot name the same construct two ways. The
    three sets separate the three shapes the lowerer treats apart, because
    one name may be taken in one shape and fenced in another: `TextIO` is
    taken bare but `TextIO[int]` fences, and `Sequence` is taken
    subscripted but `Sequence` bare fences. The bare set is what
    `_typing_annotation` lowers as a whole name (the never aliases and the
    IO handles); the subscripted set is what it lowers under a slice; the
    dynamic set is what fences `PS1104`.
    """

    from pyscriptc.lower._annotations import (
        _DYNAMIC_ANNOTATIONS,
        _IO_HANDLES,
        _NEVER_ALIASES,
        _UNWRAP_FORMS,
    )
    from pyscriptc.lower._core import _DICT_FORMS, _LIST_FORMS, _SET_FORMS

    # The never aliases are taken bare (`-> NoReturn` lowers to `none`) and
    # never a dynamic annotation, so they come out of that set.
    never = _NEVER_ALIASES
    # The subscripted aliases `_typing_annotation` lowers: the list, dict and
    # set forms, the unwrap forms, `Callable`, `Tuple`, `Optional`, `Union`,
    # the iterators, `IO` and the container handles (`Deque`, `Counter`,
    # `Queue` and friends, which lower through their stdlib import spelling).
    subscripted = (
        _LIST_FORMS
        | _DICT_FORMS
        | _SET_FORMS
        | frozenset(_UNWRAP_FORMS)
        | {
            "Callable",
            "Tuple",
            "Iterable",
            "Sequence",
            "Iterator",
            "Generator",
            "Optional",
            "Union",
            "IO",
        }
    )
    # The bare names the lowerer takes: the never aliases and the IO handles
    # (`TextIO`, `BinaryIO` are a parameter's IO slice by itself).
    taken_bare = frozenset(never) | frozenset(_IO_HANDLES)
    # The dynamic names PS1104 fences. The builtin `object` erases to the
    # tagged dynamic value and is taken, but the `typing` spelling of it
    # fences, so `object` stays in the set and the scan tells the two
    # apart by where the name came from.
    dynamic = frozenset(_DYNAMIC_ANNOTATIONS - never)
    return taken_bare, subscripted, dynamic


def _typing_bindings(tree: ast.Module) -> dict[str, str]:
    """The typing names a module binds: the alias map and the module names.

    The alias entry is the local name -> the `typing` name it stands for
    (`from typing import Sequence as S` binds `S` -> `Sequence`, `import
    typing as t` binds `t` -> the erased module member `t.X` names). Both
    are read the same way the lowerer reads them (`_typing_alias`): a
    `from` import of an erased module and a qualified member of an erased
    module alias.
    """

    from pyscriptc.lower._stdlib_shared import _ERASED_MODULES

    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _ERASED_MODULES and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = alias.name
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _ERASED_MODULES or alias.name in _ERASED_MODULES:
                    bindings[alias.asname or root] = alias.name
    return bindings


def _resolved_name(node: ast.AST, bindings: dict[str, str]) -> tuple[str, bool]:
    """The typing name an annotation node reads, and whether it is one.

    A bare name reads the alias it is bound to; an attribute reads the
    member of the erased module it is qualified by (`typing.Any` reads
    `Any`). The flag says the name came from a typing-spelling module, so
    the scan can tell a typing shape from the module's own class.
    """

    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id], True
        return node.id, False
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in bindings:
            return node.attr, True
        return node.attr, False
    return "", False


def _is_qualified_typing(node: ast.AST, bindings: dict[str, str]) -> bool:
    """True when the annotation is the qualified spelling of a typing name.

    The lowerer reads `typing.Sequence` and `typing.Sequence[int]` through
    two different paths: the subscripted one resolves the alias and
    lowers, the bare one goes through the class table and finds nothing,
    so it fences with PS1106. The scan tells the two apart, so the
    report names the same code the compiler does.
    """

    if not isinstance(node, ast.Attribute):
        return False
    return isinstance(node.value, ast.Name) and node.value.id in bindings


def _stdlib_handle_bindings(tree: ast.Module) -> dict[str, str]:
    """The stdlib names a module binds that lower as a type handle.

    The entry is the local name -> the lowerer's alias for it
    (`from collections import deque` binds `deque` -> `Deque`,
    `from queue import Queue` binds `Queue` -> `Queue`). Only the
    spellings the lowerer resolves through its own import table are
    here: the typing spellings (`from typing import Deque`) are not,
    because the lowerer resolves those through the erased typing module
    and fences them with PS1103.

    The table is the lowerer's own `_annotation_alias` branches, read
    the same way: a `from` import of the module the alias belongs to.
    """

    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        module = node.module
        for alias in node.names:
            local = alias.asname or alias.name
            resolved = _HANDLE_ALIASES.get((module, alias.name))
            if resolved is not None:
                bindings[local] = resolved
    return bindings


# The stdlib (module, member) spellings the lowerer resolves to a type
# handle. The value is the lowerer's alias for the handle.
_HANDLE_ALIASES: dict[tuple[str, str], str] = {
    ("collections", "deque"): "Deque",
    ("collections", "OrderedDict"): "OrderedDict",
    ("collections", "Counter"): "Counter",
    ("collections", "defaultdict"): "defaultdict",
    ("queue", "Queue"): "Queue",
    ("queue", "LifoQueue"): "Queue",
    ("queue", "SimpleQueue"): "Queue",
    ("asyncio", "Queue"): "AsyncQueue",
    ("asyncio", "Task"): "Task",
    ("asyncio", "Lock"): "AsyncLock",
    ("asyncio", "Event"): "AsyncEvent",
    ("asyncio", "Semaphore"): "AsyncSemaphore",
    ("asyncio", "StreamReader"): "StreamReader",
    ("asyncio", "StreamWriter"): "StreamWriter",
    ("asyncio", "Server"): "Server",
    ("asyncio.subprocess", "Process"): "AsyncProcess",
    ("subprocess", "Popen"): "Popen",
}


def _module_names(tree: ast.Module) -> set[str]:
    """The names a module binds at its top level.

    The set is what a plain-name annotation may read: a class, a
    function, an assignment or an import the module makes. A name the
    module never binds is the PS1106 case, and a name it binds to
    something that is not a type (a function) fences the same way, so
    the scan keeps the bindings apart.
    """

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _class_names(tree: ast.Module) -> set[str]:
    """The class names a module defines."""
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _function_names(tree: ast.Module) -> set[str]:
    """The function names a module defines."""
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _from_imports(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """The `from` imports of a module: local name -> (module, member).

    The lowerer resolves an imported plain-name annotation through its
    own alias table; the scan reads the same table, so an import the
    alias table answers (`from pathlib import Path`) is taken and one it
    does not (`from xpkg.a import C`, a class outside the closed world)
    fences with PS1106 the way the compiler does.
    """

    imports: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        for alias in node.names:
            if alias.name != "*":
                imports[alias.asname or alias.name] = (node.module or "", alias.name)
    return imports


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


def scan_tree(tree: ast.Module, path: Path) -> list[Finding]:
    """The static typing findings of one module.

    The scan is the cheap half of the report: the missing annotations the
    lowerer would fence (the PS1001 family) and the construct forms that
    fence in the annotation itself (PS1103, PS1104, PS1105, PS1106). It
    never lowers anything, so the report stays cheap on a big tree. The
    names it reads are the lowerer's own tables, so the report cannot
    name a construct two ways.

    The scan reads the structural half of the lowerer's inference: the
    shapes (which display, which mutation, which name) decide a finding,
    the value types the lowerer proves under its peek do not. A local
    the lowerer proves through a call result the alias table answers
    (`len(...)` over a proven argument) stays a finding here: the report
    is a floor on what a project sees, never an exact replay, and a
    finding the compiler later proves away costs the reader nothing.
    """

    taken_bare, subscripted, dynamic = _lowerer_tables()
    bindings = _typing_bindings(tree)
    handles = _stdlib_handle_bindings(tree)
    module_names = _module_names(tree)
    class_names = _class_names(tree)
    function_names = _function_names(tree)
    from_imports = _from_imports(tree)
    returns = _function_returns(tree)
    findings: list[Finding] = []
    file_name = path.as_posix()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            findings.extend(_module_local_findings(node, file_name))
            continue
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
        if args.vararg is not None and args.vararg.annotation is None:
            findings.append(
                Finding(
                    code="PS1001",
                    family="param-inference",
                    message=f"parameter {args.vararg.arg} needs an annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
        if args.kwarg is not None and args.kwarg.annotation is None:
            findings.append(
                Finding(
                    code="PS1202",
                    family="param-inference",
                    message=f"parameter {args.kwarg.arg} needs an annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
        if node.returns is None and not _body_proves_return(node, returns):
            findings.append(
                Finding(
                    code="PS1001",
                    family="return-inference",
                    message=f"{node.name} needs a return annotation",
                    file=file_name,
                    line=node.lineno,
                )
            )
        findings.extend(_local_inference_findings(node, file_name))
    for node in ast.walk(tree):
        annotation = _annotation_of(node)
        if annotation is None:
            continue
        line = getattr(node, "lineno", 0)
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            # A quoted forward reference resolves the way the lowerer's own
            # pass does: the text names an annotation, and one the module
            # never defines (or does not parse) keeps the PS1105 fence.
            _finding_or_none = _forward_ref_finding(annotation.value, class_names, from_imports)
            if _finding_or_none is not None:
                code, message = _finding_or_none
                findings.append(
                    Finding(code=code, family="forward-reference", message=message, file=file_name, line=line)
                )
            continue
        if isinstance(annotation, ast.Subscript):
            # A subscripted alias the lowerer takes is fine; a typing alias
            # it does not take fences with PS1103. The stdlib handles lower
            # only through their own import spelling.
            base, from_typing = _resolved_name(annotation.value, bindings)
            if base and from_typing and base not in subscripted:
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
            findings.extend(
                _name_annotation_findings(
                    annotation,
                    bindings,
                    handles,
                    module_names,
                    class_names,
                    function_names,
                    from_imports,
                    taken_bare,
                    dynamic,
                    file_name,
                    line,
                )
            )
            continue
        for name in _annotation_names(annotation):
            resolved, from_typing = _resolved_name(ast.Name(id=name, ctx=ast.Load()), bindings)
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


def _function_returns(tree: ast.Module) -> dict[str, str]:
    """The return type every function the module defines declares.

    The entry is the function name -> the kind its return annotation
    names. The report reads it to prove a call: a call to a function
    the module defines returns what that function declares.
    """

    returns: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.returns is None:
            continue
        kind = _annotation_value_kind(node.returns)
        if kind is not None:
            returns[node.name] = kind
    return returns


def _body_proves_return(fn: ast.FunctionDef | ast.AsyncFunctionDef, returns: dict[str, str]) -> bool:
    """True when the function's own body proves the type it returns.

    The lowerer peeks the body (issue #500): a body whose returns all
    agree on one provable type gives the function that type, so a
    missing return annotation is not a fence. The peek's rule is the
    one the report reads: a sync function (an async one is refused),
    no yield, and either no return at all (the body proves `None`) or
    returns that all carry a value the subset can lower and that agree
    on one type.

    The report reads the structural half: a bare `return`, a `None`
    return or a body whose returns disagree leaves the annotation
    missing, and so does an async function or a generator.
    """

    if isinstance(fn, ast.AsyncFunctionDef):
        return False
    yields = [node for node in ast.walk(fn) if isinstance(node, (ast.Yield, ast.YieldFrom))]
    if yields:
        # The generator's own peek (issue #500) gives the unannotated
        # generator the one type every yield agrees on; a bare `return`
        # among them keeps it, a valued return or a `yield from` does
        # not. The scan reads the structural half.
        if any(node.value is not None for node in ast.walk(fn) if isinstance(node, ast.Return)):
            return False
        if any(isinstance(node, ast.YieldFrom) for node in yields):
            return False
        return True
    returned = [node for node in ast.walk(fn) if isinstance(node, ast.Return)]
    if not returned:
        # Every path falls off the end: the body proves `None`.
        return True
    if any(node.value is None for node in returned):
        # A bare `return` proves nothing beyond `None`, and mixing it
        # with a valued return means the paths disagree.
        return all(node.value is None for node in returned)
    kinds: set[str] = set()
    for node in returned:
        kind = _returned_value_kind(node.value, fn, returns)
        if kind is None:
            return False
        kinds.add(kind)
    return len(kinds) == 1


def _returned_value_kind(
    value: ast.expr, fn: ast.FunctionDef | ast.AsyncFunctionDef, returns: dict[str, str]
) -> str | None:
    """The kind of a returned value the peek can lower, or None.

    The kind is the value's own shape: a scalar constant, a string, a
    tuple or a list display, a name the body bound to a proven value, a
    parameter the signature annotated, or a call to a function the
    module defines with a known return. A value the peek cannot lower
    leaves the return unproven, and the annotation stays missing.
    """

    if isinstance(value, ast.Constant):
        if value.value is None:
            return None
        return type(value.value).__name__
    if isinstance(value, ast.JoinedStr):
        return "str"
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return type(value).__name__
    if isinstance(value, ast.Dict):
        return "dict"
    if isinstance(value, ast.BinOp):
        return _returned_value_kind(value.left, fn, returns)
    if isinstance(value, ast.UnaryOp):
        return _returned_value_kind(value.operand, fn, returns)
    if isinstance(value, ast.Name):
        return _name_value_kind(value.id, fn, returns)
    if isinstance(value, ast.Call):
        # A call the module defines with a known return is the body's own
        # fact; a builtin's result is the alias table's, which the report
        # does not read, so it stays unproven here.
        return _call_value_kind(value, returns)
    return None


def _name_value_kind(name: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, returns: dict[str, str]) -> str | None:
    """The kind of a name the body bound to a proven value, or None.

    A parameter the signature annotated is the function's own fact; a
    local the body bound to a literal or to a call the peek proves is
    the body's own fact. Anything else proves nothing.
    """

    for arg in list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs):
        if arg.arg == name and arg.annotation is not None:
            return _annotation_value_kind(arg.annotation)
    for stmt in fn.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        kind = _returned_value_kind(stmt.value, fn, returns)
        if kind is not None:
            return kind
    return None


def _annotation_value_kind(annotation: ast.expr) -> str | None:
    """The kind an annotated parameter carries, or None.

    The kind is the annotation's own shape: a scalar name, a container
    subscript or a class name. An annotation the peek cannot read (a
    typing alias, a union) proves nothing.
    """

    if isinstance(annotation, ast.Name):
        if annotation.id in _SCALARS:
            return annotation.id
        if annotation.id in _BARE_CONTAINERS:
            return annotation.id
        return annotation.id
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        if isinstance(base, ast.Name):
            return base.id
        return None
    return None


def _call_value_kind(call: ast.Call, returns: dict[str, str]) -> str | None:
    """The kind a called function returns, or None.

    The peek proves a call to a function the module defines whose own
    return annotation says what it returns. A call the module does not
    define proves nothing.
    """

    if not isinstance(call.func, ast.Name):
        return None
    return returns.get(call.func.id)


def _module_local_findings(tree: ast.Module, file_name: str) -> list[Finding]:
    """The findings the module-level locals of a module carry.

    A module-level name is a global slot, and a global slot needs a
    type: an empty display at the top level says nothing, and the
    lowerer fences it with PS1001. The top level never accumulates a
    type from the body's mutations the way a function body does, so the
    finding stands whatever the module does with the name later.
    """

    findings: list[Finding] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not _is_empty_display(stmt.value):
            continue
        findings.append(
            Finding(
                code="PS1001",
                family="local-inference",
                message=f"{target.id} needs a concrete type",
                file=file_name,
                line=stmt.lineno,
            )
        )
    return findings


def _local_inference_findings(fn: ast.FunctionDef | ast.AsyncFunctionDef, file_name: str) -> list[Finding]:
    """The findings the unpinned locals of one function carry.

    The lowerer's accumulated-type scan reads a bare `[]` or `{}` as a
    shape the body pins: the uses of the name (`name.append(v)`,
    `name[k] = v`) say what goes in it. A display the body never pins
    keeps the PS1001 fence, and that is what the report names.

    The scan is the structural half of the lowerer's own scan: the same
    shapes, read the same way, but only the display's own name is read,
    so the report stays cheap. The value types are compared too, because
    the lowerer refuses a mixed element type and the report has to
    refuse it the same way.
    """

    findings: list[Finding] = []
    body = list(fn.body)
    for stmt in body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = stmt.value
        if not _is_empty_display(value):
            continue
        if not _is_set_call(value) and _name_is_pinned(target.id, body, stmt):
            continue
        findings.append(
            Finding(
                code="PS1001",
                family="local-inference",
                message=f"{target.id} needs a concrete type",
                file=file_name,
                line=stmt.lineno,
            )
        )
    return findings


def _is_empty_display(value: ast.expr) -> bool:
    """True when the value is an empty display the body has to pin.

    A bare `[]` or `{}` says nothing on its own; the same `set()`
    call says nothing either, and a set never accumulates a type from
    its `add` calls, so it always keeps the fence. A display with
    elements has its own type and never reaches the accumulated-type
    scan.
    """

    if isinstance(value, ast.List):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, ast.Call):
        return isinstance(value.func, ast.Name) and value.func.id == "set" and not value.args
    return False


def _is_set_call(value: ast.expr) -> bool:
    """True when the value is the `set()` call.

    A set is the one display the accumulated-type scan does not read:
    its element type never pins from the `add` calls, so an
    unannotated `set()` keeps the fence whatever the body does with it.
    """

    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "set"


def _name_is_pinned(name: str, body: list[ast.stmt], display: ast.stmt) -> bool:
    """True when the body pins the display's element type.

    The pin is a mutation of the display's own name inside the same
    body: an `append` on a list, an item write on a dict, an `add` on a
    set. The lowerer reads the same shapes and compares the value
    types; a mixed type is a refusal there, so it is a refusal here.
    """

    kinds: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if node is display:
                continue
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"append", "add"} and isinstance(node.func.value, ast.Name):
                    if node.func.value.id != name or not node.args:
                        continue
                    kind = _value_kind(node.args[0])
                    if kind is None:
                        return False
                    kinds.add(kind)
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                item = node.targets[0]
                if isinstance(item, ast.Subscript) and isinstance(item.value, ast.Name):
                    if item.value.id != name:
                        continue
                    kind = _value_kind(node.value)
                    if kind is None:
                        return False
                    kinds.add(kind)
    if not kinds:
        return False
    return len(kinds) == 1


def _value_kind(value: ast.expr) -> str | None:
    """The kind of a value the peek can lower, or None."""

    if isinstance(value, ast.Constant):
        if value.value is None:
            return None
        return type(value.value).__name__
    if isinstance(value, ast.JoinedStr):
        return "str"
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return type(value).__name__
    if isinstance(value, ast.Dict):
        return "dict"
    if isinstance(value, ast.Name):
        return value.id
    return None


def _forward_ref_finding(
    text: str, class_names: set[str], from_imports: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    """The finding a quoted forward reference carries, or None when it resolves.

    The lowerer parses the text and lowers it through the ordinary
    annotation path; a name the module never defines keeps the PS1105
    fence. The scan reads the same rule cheaply: a text that parses as a
    plain name the module defines (a class it collects, a builtin scalar,
    an exception) resolves; anything else fences.
    """

    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError:
        return "PS1105", f"the forward reference {text!r} does not parse as an annotation"
    body = parsed.body
    if isinstance(body, ast.Constant) and isinstance(body.value, str):
        return "PS1105", f"the forward reference {text!r} quotes a string, not a type"
    if not isinstance(body, ast.Name):
        return None
    name = body.id
    if name in _SCALARS or name in ("object", "Any"):
        return None
    if name in EXCEPTIONS or name == "BaseException":
        return None
    if name in class_names:
        return None
    imported = from_imports.get(name)
    if imported is not None and _import_is_lowered_type(imported[0], imported[1]):
        return None
    return "PS1105", f"the forward reference {text!r} never resolves: no definition by module end"


def _name_annotation_findings(
    annotation: ast.AST,
    bindings: dict[str, str],
    handles: dict[str, str],
    module_names: set[str],
    class_names: set[str],
    function_names: set[str],
    from_imports: dict[str, tuple[str, str]],
    taken_bare: frozenset[str],
    dynamic: frozenset[str],
    file_name: str,
    line: int,
) -> list[Finding]:
    """The findings a plain-name or attribute annotation carries.

    The rule is the lowerer's own dispatch, read in its order: the
    typing bindings first (`Any` and `object` resolve in it, the dynamic
    names fence PS1104, the bare aliases that are not the never aliases
    or the IO handles fence PS1103), then the stdlib handles, then the
    names the module defines, then the fence.
    """

    name, from_typing = _resolved_name(annotation, bindings)
    if from_typing:
        if name in dynamic:
            return [
                Finding(
                    code="PS1104",
                    family="dynamic-annotation",
                    message=f"the annotation names the dynamic type {name}",
                    file=file_name,
                    line=line,
                )
            ]
        if _is_qualified_typing(annotation, bindings):
            # The qualified spelling of a typing name goes through the
            # lowerer's class table, which answers only the never aliases
            # and the IO handles; the rest fence with PS1106.
            if name in taken_bare:
                return []
            return [
                Finding(
                    code="PS1106",
                    family="unlowered-type-name",
                    message=f"the annotation {name} names a type that is not lowered",
                    file=file_name,
                    line=line,
                )
            ]
        if name not in taken_bare:
            return [
                Finding(
                    code="PS1103",
                    family="generic-alias",
                    message=f"the alias {name} is not a type the compiler takes bare",
                    file=file_name,
                    line=line,
                )
            ]
        return []
    if name in handles:
        return []
    if name in _SCALARS or name in ("object", "Any"):
        return []
    if name in _BARE_CONTAINERS:
        return []
    if name in EXCEPTIONS or name == "BaseException":
        return []
    if name in class_names:
        return []
    if name in function_names:
        # A function is not a type: the lowerer refuses it with PS1106.
        return [
            Finding(
                code="PS1106",
                family="unlowered-type-name",
                message=f"the annotation {name} names a type that is not lowered",
                file=file_name,
                line=line,
            )
        ]
    imported = from_imports.get(name)
    if imported is not None:
        if _import_is_lowered_type(imported[0], imported[1]):
            return []
        return [
            Finding(
                code="PS1106",
                family="unlowered-type-name",
                message=f"the annotation {name} names a type that is not lowered",
                file=file_name,
                line=line,
            )
        ]
    if name in module_names:
        # A name the module binds to a value (an assignment) lowers as the
        # value's type; the lowerer takes it, so the scan stays quiet.
        return []
    return [
        Finding(
            code="PS1106",
            family="unlowered-type-name",
            message=f"the annotation {name} names a type that is not lowered",
            file=file_name,
            line=line,
        )
    ]


def _import_is_lowered_type(module: str, member: str) -> bool:
    """True when the lowerer lowers the imported member as a type.

    The answer is the lowerer's own alias table (`alias_name`), the same
    one `_annotation_alias` reads: an import the table answers is a type
    the compiler takes (`from pathlib import Path`, `from collections
    import deque`), and one it does not is a name outside the closed
    world, which the compiler fences with PS1106.
    """

    from pyscriptc.restlower import alias_name

    return alias_name(module, member) is not None


def scan_file(path: Path) -> list[Finding]:
    """The static typing findings of one file, or none when it does not parse."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    return scan_tree(tree, path)


def scan_files(paths: list[Path]) -> list[Finding]:
    """The static typing findings of a tree of files, in path order."""

    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_file(path))
    return findings


def typing_report(policy: TypingPolicy, files: list[Path], findings: list[Finding]) -> dict[str, Any]:
    """The typing section of a quality report.

    `files` are the files of the tree, `findings` the static findings of
    the whole tree (each carrying its file). The section names the project
    mode, the level every file ended up at, the findings each level
    surfaces and the counts by severity.
    """

    levels: dict[str, str] = {}
    origins: dict[str, str] = {}
    level_of: dict[str, str] = {}
    for path in files:
        source = ""
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            source = ""
        level, origin = policy.level_for(path, source)
        key = path.as_posix()
        levels[key] = level
        origins[key] = origin
        level_of[key] = level
    surfaced: list[Finding] = []
    errors: int = 0
    warnings: int = 0
    for finding in findings:
        level = level_of.get(finding.file, policy.mode)
        severity = severity_for(level, finding.family)
        if severity is None:
            continue
        finding.severity = severity
        if severity == "error":
            errors += 1
        else:
            warnings += 1
        surfaced.append(finding)
    return {
        "mode": policy.mode,
        "levels": levels,
        "origins": origins,
        "findings": [finding.as_dict() for finding in surfaced],
        "summary": {
            "files": len(files),
            "errors": errors,
            "warnings": warnings,
            "coverage": reports_coverage(policy.mode),
        },
    }
