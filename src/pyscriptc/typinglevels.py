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


# The generic aliases the lowerer does not take (PS1103), spelled as the
# name a subscript reads.
GENERIC_ALIAS_NAMES: list[str] = [
    "Mapping",
    "MutableMapping",
    "Sequence",
    "MutableSequence",
    "Collection",
    "AbstractSet",
    "MutableSet",
    "Iterator",
    "Iterable",
    "Generator",
    "Callable",
    "Awaitable",
    "Coroutine",
]

# The dynamic annotations (PS1104): a shape the IR has no slot for.
DYNAMIC_NAMES: list[str] = ["Any", "Protocol", "TypeVar", "Generic", "Callable"]


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


def _annotation_names(node: ast.AST) -> list[str]:
    """The names an annotation expression mentions."""
    names: list[str] = []
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            names.append(part.id)
        elif isinstance(part, ast.Attribute):
            names.append(part.attr)
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


def scan_tree(tree: ast.Module, path: Path) -> list[Finding]:
    """The static typing findings of one module.

    The scan is the cheap half of the report: the missing annotations the
    lowerer would fence (the PS1001 family) and the construct forms that
    fence in the annotation itself (PS1103, PS1104, PS1105). It never
    lowers anything, so the report stays cheap on a big tree.
    """

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
            base = annotation.value
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name in GENERIC_ALIAS_NAMES:
                findings.append(
                    Finding(
                        code="PS1103",
                        family="generic-alias",
                        message=f"the generic alias {base_name} is not a container the compiler takes",
                        file=file_name,
                        line=line,
                    )
                )
                continue
        for name in _annotation_names(annotation):
            if name in DYNAMIC_NAMES:
                findings.append(
                    Finding(
                        code="PS1104",
                        family="dynamic-annotation",
                        message=f"the annotation names the dynamic type {name}",
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


def gate(findings: list[Finding], level: str) -> list[Finding]:
    """The findings a level surfaces, each with the level's severity."""

    out: list[Finding] = []
    for finding in findings:
        severity = severity_for(level, finding.family)
        if severity is None:
            continue
        finding.severity = severity
        out.append(finding)
    return out


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
    for finding in findings:
        level = level_of.get(finding.file, policy.mode)
        severity = severity_for(level, finding.family)
        if severity is None:
            continue
        finding.severity = severity
        surfaced.append(finding)
    errors = sum(1 for finding in surfaced if finding.severity == "error")
    warnings = sum(1 for finding in surfaced if finding.severity == "warning")
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
