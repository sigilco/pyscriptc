# SPDX-License-Identifier: Apache-2.0
"""The inert `pyscriptc._annotations` shadow module (issue #1306).

The compiler's annotation surface names two decorators, `@island` and
`@specialize`. They live here so a program that uses them runs unmodified
on plain CPython and on PyPy: this module imports nothing from pyscriptc,
binds two plain functions and does nothing else, so the decorated
definition is the same object the interpreter would see without the
decorators.

Under the compiler both names are annotation-only, exactly like the
`typing` names: the import is erased and the decorated definitions lower
as the plain functions they are. This is the base the `@island` demotion
notes (#1307) and the `@specialize` specialization report (#1309) build
on; the pattern is Cython's pure-Python shadow module.

The decorators keep the decorated type, so the type checker reads a
decorated definition as the definition it wraps.
"""

from typing import TypeVar

__all__ = ["island", "specialize"]

_F = TypeVar("_F")


def island(fn: _F) -> _F:
    """Mark a definition the compiler routes to the island on purpose.

    Inert: returns the function unchanged. Under the compiler the marking
    is advisory only (the demotion notes are #1307's slice).
    """
    return fn


def specialize(fn: _F) -> _F:
    """Mark a definition the compiler monomorphizes on purpose.

    Inert: returns the function unchanged. Under the compiler the marking
    asserts the monomorphization the compiler already performs (the
    specialization report is #1309's slice).
    """
    return fn
