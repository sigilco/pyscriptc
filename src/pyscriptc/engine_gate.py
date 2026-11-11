"""The engine language gate (issue #668).

An island engine is an interpreter with its own language level. PyPy 3.11
implements Python 3.11: it has no PEP 649 (deferred annotations), no PEP 695
(type parameter lists and the `type` statement), no PEP 750 (t-strings) and
no PEP 758 (`except A, B:` without parentheses). A definition that uses one
of those constructs cannot run on that engine, so the compile refuses it
instead of emitting a program that fails at run time with a syntax error
the user never sees.

The gate is the engine's **language level**, taken from the capability
manifest (`scripts/engine_manifest.py probe`, run inside the engine): the
`language_level` field (`3.11` for PyPy). The compiler parses the program's
sources with `ast.parse(..., feature_version=...)` at the engine's level,
which rejects exactly the syntax that level does not have. CPython 3.14
syntax the engine lacks therefore fails at compile time with a coded
diagnostic.

Why `feature_version` and not a hand-written visitor: the CPython parser
knows the exact version each syntax was introduced in, and the gate must
match the engine's level to the letter. A visitor would have to track every
construct by hand and would silently miss the next one the engine gains or
loses. The parser's own version gate is the same rule the engine's own
parser applies.

The gate is **additive**: without an engine selection (or with the built-in
`cpython`, whose level is the compiler's own) the sources are parsed as
they always were, byte for byte.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The diagnostic code of the gate. `PS4303` sits with the other
#: runtime-pack codes (`PS4301` the trimmed-stdlib refusal, `PS4302` the
#: native-dependency refusal): all three are refusals that name a runtime
#: the compiler cannot satisfy.
GATE_CODE = "PS4303"

#: The CPython language level the compiler itself targets. It is the
#: ceiling of every gate: an engine may declare a lower level (PyPy 3.11),
#: never a higher one (a 3.15 engine would accept syntax the compiler
#: cannot lower).
COMPILER_LEVEL = (3, 14)

#: The language level of the built-in embedded CPython engine: the
#: compiler's own. The built-in engine is never gated.
BUILTIN_LEVEL = COMPILER_LEVEL


class GateError(Exception):
    """A user-facing failure of the engine language gate (no traceback)."""

    def __init__(self, message: str, path: Path | None = None, line: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.line = line


@dataclass(frozen=True)
class LanguageLevel:
    """A parsed language level (`3.11`, `3.14`)."""

    major: int
    minor: int

    @property
    def feature_version(self) -> tuple[int, int]:
        """The `feature_version` argument of `ast.parse`."""
        return (self.major, self.minor)

    @property
    def text(self) -> str:
        """The `major.minor` spelling."""
        return f"{self.major}.{self.minor}"

    def __str__(self) -> str:
        return self.text


def parse_level(text: str) -> LanguageLevel | None:
    """Parse a `major.minor` language level, or None when it is not one."""
    parts = text.strip().split(".")
    if len(parts) != 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError:
        return None
    if major < 3 or minor < 0:
        return None
    return LanguageLevel(major, minor)


def check_source(source: str, level: LanguageLevel, *, path: Path | None = None, filename: str = "") -> None:
    """Refuse a source the engine's language level cannot parse.

    The refusal names the construct and the level, so the user knows what
    to change and which engine would accept it. `SyntaxError` from the
    version-gated parse is the signal: the message already names the
    construct ("t-strings are only supported in Python 3.14 and greater").
    """
    try:
        ast.parse(source, filename=filename, feature_version=level.feature_version)
    except SyntaxError as exc:
        line = exc.lineno or 0
        detail = exc.msg or "unsupported syntax"
        raise GateError(
            f"{GATE_CODE}: the {level.text} engine does not support this syntax: {detail}; "
            f"the program uses a construct the engine's language level ({level.text}) does not have",
            path=path,
            line=line,
        ) from exc


def engine_level(descriptor: Any, manifest: Any) -> LanguageLevel | None:
    """The language level of a selected engine, or None when it is unknown.

    The level comes from the engine's capability manifest (the engine's own
    claim, probed inside the engine). A descriptor without a manifest, or a
    manifest without a usable `language_level`, leaves the gate open: the
    compiler does not guess an engine's language level, and the CPython
    default path stays exactly as it was.
    """
    if manifest is None:
        return None
    level_text = getattr(manifest, "language_level", None)
    if not isinstance(level_text, str):
        return None
    level = parse_level(level_text)
    if level is None:
        return None
    if level.major != COMPILER_LEVEL[0] or level.minor > COMPILER_LEVEL[1]:
        # A level the compiler cannot lower is not a gate: the compiler
        # parses 3.14 syntax, so an engine claiming 3.15 would accept
        # source the compiler cannot lower. The gate only ever lowers the
        # ceiling.
        return None
    return level
