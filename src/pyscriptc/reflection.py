"""Runtime reflection: the culprit behind an island-lane fence.

Native code lowers to LLVM with a static type model: there are no runtime
type objects, no signature tables and no annotation dictionaries. A program
that reads one of those at run time is fully typed yet cannot be statically
compiled, so the fence names the mechanism, says why it refuses, points at the
island lane (the opt-in boxed CPython tier), and names the typed idiom that
replaces it when one exists.

The messages keep the PS codes they already had; only the text changes, so
`pyscriptc coverage` can classify the family from the message and the
capability summary can count the definition as island-routed. There is no
fake `__annotations__` table here: a static schema descriptor is a separate
piece of work.
"""

from __future__ import annotations

import re

ISLAND = "island"
ISLAND_SUFFIX = f"routes to the {ISLAND} lane (requires libpython)"

ANNOTATIONS_PREFIX = "runtime reflection ("
KEYWORD_PREFIX = "keyword forwarding (**kwargs)"

# `inspect` members that need a runtime signature table.
_INSPECT_SIGNATURE = frozenset({"signature", "getfullargspec", "getcallargs", "get_annotations", "getargvalues"})
# `inspect` members that read source text or code metadata.
_INSPECT_SOURCE = frozenset(
    {
        "getsource",
        "getsourcelines",
        "getsourcefile",
        "getfile",
        "getmodule",
        "getdoc",
        "getcomments",
        "findsource",
    }
)
# `inspect` members that walk runtime objects and frames.
_INSPECT_MEMBERS = frozenset(
    {
        "getmembers",
        "stack",
        "trace",
        "currentframe",
        "getouterframes",
        "getinnerframes",
        "getclosurevars",
        "getgeneratorstate",
    }
)

# Typing names that are values only because they read annotations at run time.
_TYPING_VALUES = {"get_type_hints": "typing.get_type_hints"}


def _note(mechanism: str, reads: str, absence: str, idiom: str) -> str:
    return f"{ANNOTATIONS_PREFIX}{mechanism}) {reads}, and {absence}; this code {ISLAND_SUFFIX}; typed idiom: {idiom}"


def inspect_note(member: str) -> str | None:
    """The note for an `inspect` member that reads runtime metadata, else None."""

    if member in _INSPECT_SIGNATURE:
        return _note(
            "inspect.signature",
            "reads a callable's parameter list at run time",
            "native code keeps no signature table",
            "declare the parameters explicitly and read the values you pass",
        )
    if member in _INSPECT_SOURCE:
        return _note(
            f"inspect.{member}",
            "reads a function's source text or code metadata at run time",
            "native code keeps no source or line table",
            "inline the logic, or pass the values it would read explicitly",
        )
    if member in _INSPECT_MEMBERS:
        return _note(
            f"inspect.{member}",
            "lists the members or frames of a live object at run time",
            "native code keeps no runtime type or frame objects",
            "list the members explicitly, or use match on the names",
        )
    return None


def typing_note(name: str) -> str | None:
    """The note for a typing name read as a value at run time, else None."""

    mechanism = _TYPING_VALUES.get(name)
    if mechanism is None:
        return None
    return _note(
        mechanism,
        "resolves a function's or class's annotations to types at run time",
        "native code keeps no annotation table and no type objects",
        "pass the concrete types explicitly where they are needed",
    )


def annotation_note(attribute: str) -> str | None:
    """The note for reading a runtime annotation table, else None."""

    if attribute != "__annotations__":
        return None
    return _note(
        "__annotations__",
        "reads a class's or function's annotation table at run time",
        "native code keeps no annotation table (a static schema descriptor is not part of this subset)",
        "pass the field types explicitly where they are needed",
    )


def getattr_note(builtin: str) -> str:
    """A `getattr`/`hasattr` call whose attribute name is not a literal."""

    return _note(
        builtin,
        "resolves an attribute name that is only known at run time",
        "native code resolves every attribute to a declared field",
        "match the name instead of dispatching on it",
    )


def type_synthesis_note() -> str:
    """The three-argument `type(name, bases, dict)` form."""

    return _note(
        "type()",
        "builds a class from strings at run time",
        "native code fixes the class layout at compile time",
        "declare the class explicitly, or use PEP 695 generics for a generic one",
    )


def type_read_note() -> str:
    """`type(value)` on anything but a caught exception."""

    return _note(
        "type()",
        "reads a value's class at run time",
        "native code has no runtime type objects",
        "dispatch on the value or on a declared tag instead",
    )


def metaclass_note() -> str:
    """A class header with `metaclass=`."""

    return _note(
        "metaclass",
        "hands class creation to a metaclass hook at run time",
        "native code fixes the class layout at compile time",
        "write the class explicitly, or use PEP 695 generics",
    )


def kwargs_parameter_note() -> str:
    """A `**kwargs` parameter that needs a runtime keyword mapping."""

    return (
        f"{KEYWORD_PREFIX} collects keyword arguments into a mapping at run time, "
        "and the native lane keeps no keyword mapping for a parameter; "
        f"this code {ISLAND_SUFFIX}; typed idiom: PEP 692 Unpack[TypedDict] declares the accepted keyword keys"
    )


def kwargs_call_note() -> str:
    """A call that forwards `**mapping` to a callee."""

    return (
        f"{KEYWORD_PREFIX} forwards keyword arguments by name at run time, "
        "and the native lane passes arguments positionally with no parameter table; "
        f"this code {ISLAND_SUFFIX}; typed idiom: pass the keyword arguments explicitly"
    )


def routes_to_island(message: str) -> bool:
    """Whether a fence message names a runtime-reflection cause."""

    return ANNOTATIONS_PREFIX in message or KEYWORD_PREFIX in message


_MECHANISM = re.compile(r"^runtime reflection \((.+?)\) ")


def mechanism_of(message: str) -> str | None:
    """The culprit named at the head of a reflection message, if any."""

    match = _MECHANISM.match(message)
    if match is not None:
        return match.group(1)
    if KEYWORD_PREFIX in message:
        return "**kwargs"
    return None
