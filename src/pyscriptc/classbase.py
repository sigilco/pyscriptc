# SPDX-License-Identifier: Apache-2.0
"""The class-base census: what kind of base a refused class base is.

The design note `.agents/docs/design/library-blocks-and-class-bases.md` ranks
the class-base work by kind (library base, `Protocol`, `Generic`, `ABC`,
enum, `NamedTuple`, `TypedDict`, exception, stdlib) and orders the ranks by
the number of fences each kind accounts for. The ranking needs the number to
be a measurement, so the lowerer resolves every refused class base and this
module names the kind it resolved to.

The naming is pure: it reads the resolved descriptor of a base (the module it
came from, the member it names, how it was spelled) and answers a kind. The
resolution itself lives in the lowerer, which is the only place that knows
what an import binds to.

The kinds are the ones the census table uses, so the report reads the way the
design note does.
"""

from __future__ import annotations

import sys

# The base kinds, in the order the report lists them: the ranking's row
# order, so a reader compares the rows the way the note does.
KIND_LIBRARY = "library"
KIND_PROTOCOL = "protocol"
KIND_GENERIC = "generic"
KIND_ABC = "abc"
KIND_ENUM = "enum"
KIND_NAMEDTUPLE = "namedtuple"
KIND_TYPEDDICT = "typeddict"
KIND_EXCEPTION = "exception"
KIND_STDLIB = "stdlib"
KIND_OTHER = "other"

BASE_KINDS: tuple[str, ...] = (
    KIND_LIBRARY,
    KIND_PROTOCOL,
    KIND_GENERIC,
    KIND_ABC,
    KIND_ENUM,
    KIND_NAMEDTUPLE,
    KIND_TYPEDDICT,
    KIND_EXCEPTION,
    KIND_STDLIB,
    KIND_OTHER,
)

# The annotation-only members that name a base kind on their own. A base
# spelled through one of them is that kind, whatever the module spells it
# (`typing.Protocol`, `typing_extensions.Protocol`, `collections.abc`).
_TYPING_BASES: dict[str, str] = {
    "Protocol": KIND_PROTOCOL,
    "Generic": KIND_GENERIC,
    "ABC": KIND_ABC,
    "ABCMeta": KIND_ABC,
    "NamedTuple": KIND_NAMEDTUPLE,
    "TypedDict": KIND_TYPEDDICT,
}

# The enum members that name an enum base.
_ENUM_MEMBERS = frozenset({"Enum", "IntEnum", "StrEnum"})

# The builtin types a class can name as a base (`class C(list)`). They are
# standard-library types the lowering does not lower as a class, so they
# census with the stdlib bases.
_BUILTIN_BASES = frozenset({"list", "dict", "set", "tuple", "frozenset", "str", "bytes", "bytearray"})


def typing_base_kind(member: str) -> str | None:
    """The kind a typing member names as a base, or None.

    `member` is the bare member name (`Protocol`, `Generic[T]`'s `Generic`,
    `NamedTuple`), not a dotted path: the annotation-only modules spell the
    same members under several module names.
    """

    return _TYPING_BASES.get(member)


def enum_base_kind(member: str) -> str | None:
    """The kind an enum member names as a base, or None."""

    return KIND_ENUM if member in _ENUM_MEMBERS else None


def builtin_base_kind(member: str) -> str | None:
    """The kind a builtin type names as a base, or None."""

    return KIND_STDLIB if member in _BUILTIN_BASES else None


def is_stdlib_module(module: str) -> bool:
    """True when the module is a standard-library module.

    The closed import table wins over the interpreter's module names, so a
    lowered module the interpreter does not ship (the dotenv config pack)
    still counts as standard library.
    """

    if not module:
        return False
    root = module.split(".", 1)[0]
    if root in sys.stdlib_module_names:
        return True
    try:
        from pyscriptc.imports import module_status
    except Exception:  # pragma: no cover - the import table is always there
        return False
    if module_status(module) is None:
        return False
    return True


def kind_of(
    *,
    member: str = "",
    module: str = "",
    is_exception: bool = False,
    is_enum: bool = False,
) -> str:
    """The kind one resolved class base is.

    The resolution answers the questions the naming asks: the member the base
    names (`BaseModel`, `Protocol`, `Thread`), the module it came from
    (`sqlalchemy.orm`, `typing`, `threading`), whether the base is a builtin
    exception, and whether the base is a class the lowering knows is an enum.
    A base that resolves to nothing (a name the lowering never saw) has no
    module and no member, and the kind is then `other`.
    """

    if is_exception:
        return KIND_EXCEPTION
    if is_enum:
        return KIND_ENUM
    if member:
        kind = typing_base_kind(member)
        if kind is not None:
            return kind
        kind = enum_base_kind(member)
        if kind is not None:
            return KIND_ENUM
        kind = builtin_base_kind(member)
        if kind is not None:
            return kind
    if module:
        if is_stdlib_module(module):
            return KIND_STDLIB
        return KIND_LIBRARY
    return KIND_OTHER
