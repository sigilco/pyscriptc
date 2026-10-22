"""Diagnostic code registry behind `pyscriptc explain`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    title: str
    why: str
    fix: str


_ENTRIES = (
    Diagnostic(
        "PS0001",
        "invalid input or IR",
        "The source or the generated IR failed a structural check before lowering could finish.",
        "Fix the input path or syntax error named in the message.",
    ),
    Diagnostic(
        "PS1001",
        "annotation missing or not lowered",
        (
            "A parameter, return, or variable needs an annotation the subset can read, "
            "or an annotation form has no more specific code."
        ),
        "Annotate the name with int, float, bool, str, bytes, list[T], dict[K, V], tuple[...], or a lowered class.",
    ),
    Diagnostic(
        "PS1003",
        "type mismatch",
        "An operation received a type the lowered subset does not accept for it.",
        "Convert the operand explicitly, for example int(flag) before math calls.",
    ),
    Diagnostic(
        "PS1004",
        "function can fall off the end",
        "A function with a non-None return type has a path that reaches the end without a return.",
        "End every path with a return or a raise.",
    ),
    Diagnostic(
        "PS1101",
        "union annotation",
        "The IR has no tagged union yet, so `A | B` and `Union[A, B]` have no representation.",
        "Narrow the annotation to one arm, or split the function per arm.",
    ),
    Diagnostic(
        "PS1102",
        "Optional annotation",
        "An optional of one type (`T | None`, `Optional[T]`) is lowered. "
        "`A | B | None` and `Union[A, B, None]` need a multi-arm union, which the IR does not have yet.",
        "Narrow to `T | None` with one arm, or split the function per arm.",
    ),
    Diagnostic(
        "PS1103",
        "generic alias annotation",
        (
            "A subscripted annotation such as Mapping[K, V], Sequence[T], Iterator[T], "
            "Callable[...], or type[T] is not a lowered container."
        ),
        "Use list[T], dict[K, V], or tuple[...] in the signature.",
    ),
    Diagnostic(
        "PS1104",
        "dynamic or structural annotation",
        (
            "object, Any, Protocol, TypeVar, Generic, Callable and ABC bases describe "
            "types the subset cannot represent natively."
        ),
        "Annotate with the concrete type the function really takes.",
    ),
    Diagnostic(
        "PS1105",
        "forward reference annotation",
        "A quoted annotation such as 'Node' is not resolved by the lowering.",
        "Define the class before use and write the name unquoted.",
    ),
    Diagnostic(
        "PS1106",
        "annotation names an unlowered type",
        "The annotation is a plain name that is not a builtin scalar, a lowered class, or a known alias.",
        "Import a lowered class, or replace the alias with the concrete type.",
    ),
    Diagnostic(
        "PS1201",
        "decorator",
        "Only a closed set of decorators lowers. Other decorators change behavior the compiler cannot see.",
        "Remove the decorator, or inline what it does.",
    ),
    Diagnostic(
        "PS1202",
        "parameter form",
        "Positional-only, keyword-only, *args and **kwargs parameters are not lowered.",
        "Use plain positional parameters, with defaults where needed.",
    ),
    Diagnostic(
        "PS1203",
        "class header form",
        "Class keywords (metaclass=...) and multiple inheritance are not lowered.",
        "Use a single lowered base class and no class keywords.",
    ),
    Diagnostic(
        "PS1204",
        "class body statement",
        "A class body holds only annotated fields without values and method definitions.",
        "Move assignments into __init__ and declare fields with an annotation only.",
    ),
    Diagnostic(
        "PS1205",
        "except form",
        "`except E as name` and bare `except:` are not lowered.",
        "Name a lowered exception class and drop the `as` binding.",
    ),
    Diagnostic(
        "PS1206",
        "with-as binding",
        "`with ... as name` is lowered only for a closed set of context managers.",
        "Use a lowered context manager without the binding, or open and close explicitly.",
    ),
    Diagnostic(
        "PS2001",
        "stdlib name not lowered",
        "The module imports, but this attribute is outside its lowered surface.",
        "Use a lowered attribute of the module, or hand-write the logic.",
    ),
    Diagnostic(
        "PS2002",
        "construct not lowered",
        "A statement, expression, import, or definition has no lowering.",
        "Rewrite it with the lowered constructs, as the hint for this fence says.",
    ),
    Diagnostic(
        "PS2003",
        "call arguments not lowered",
        "A call has keyword arguments, or the wrong number of arguments, for its lowered form.",
        "Pass positional arguments in the lowered arity.",
    ),
    Diagnostic(
        "PS2004",
        "operator or operand not lowered",
        "The operator is not lowered for these operand types.",
        "Convert the operands to a supported pair of types.",
    ),
    Diagnostic(
        "PS2005",
        "string literal outside Latin-1",
        "Strings are byte sequences in the runtime, so only Latin-1 text lowers.",
        "Use Latin-1 text, or move the literal to bytes.",
    ),
    Diagnostic(
        "PS2101",
        "queued stdlib module",
        "The module is on the roadmap and is not lowered yet.",
        "Avoid the module, or wait for the queue entry to land.",
    ),
    Diagnostic(
        "PS3102",
        "invalid config",
        "A `pyscriptc.toml`, a `[tool.pyscriptc]` table, or PEP 723 script metadata failed schema validation.",
        "Fix the key named in the message; `pyscriptc explain` lists no other config diagnostics.",
    ),
    Diagnostic(
        "PS3001",
        "version mismatch",
        "The running interpreter is not the CPython version the subset targets.",
        "Run under the pinned CPython version.",
    ),
    Diagnostic(
        "PS4001",
        "FFI manifest unreadable",
        "The manifest passed to --ffi could not be read or is not valid TOML.",
        "Fix the file path or the TOML syntax named in the message.",
    ),
    Diagnostic(
        "PS4002",
        "FFI manifest header malformed",
        "The [manifest] section is missing, is not a table, or its module name is not a dotted identifier.",
        "Add [manifest] with a module, ps_abi and format key.",
    ),
    Diagnostic(
        "PS4003",
        "FFI manifest format unsupported",
        "The manifest's format pin is missing or names a schema version this compiler does not read.",
        "Rewrite the manifest against a supported format version.",
    ),
    Diagnostic(
        "PS4004",
        "FFI manifest ABI pin mismatch",
        "The manifest's ps_abi pin is missing or does not match the PS_ABI epoch this compiler stamps.",
        "Regenerate or update the manifest for the compiler's ps_abi epoch.",
    ),
    Diagnostic(
        "PS4005",
        "FFI manifest key malformed",
        "A manifest key is missing, has the wrong type, or is not part of the schema.",
        "Keep the manifest to the keys documented in docs/ffi-manifest.md.",
    ),
    Diagnostic(
        "PS4006",
        "FFI signature outside the subset",
        "A py signature or type in the manifest is outside the vocabulary the FFI lane marshals.",
        "Use the scalar and container types, or a declared handle, from docs/ffi-manifest.md.",
    ),
    Diagnostic(
        "PS4007",
        "FFI manifest duplicate name",
        "A name is declared twice in one manifest, or two manifests claim the same module.",
        "Give each handle, const, function and module a unique name.",
    ),
    Diagnostic(
        "PS4008",
        "FFI manifest dangling reference",
        "An ownership annotation or handle reference names something the manifest does not declare.",
        "Declare the handle, or fix the reference to a declared handle or parameter.",
    ),
    Diagnostic(
        "PS3002",
        "does not fit",
        "The emitted program exceeds a fixed limit of the backend.",
        "Reduce the offending size or count.",
    ),
    Diagnostic(
        "PS3101",
        "dependency resolution failed",
        "`pyscriptc deps` could not read, resolve, download or verify a PyPI dependency "
        "(bad PEP 723 or pyproject metadata, sdist-only package, stale lock under --frozen, hash mismatch).",
        "Fix the metadata or lock as the message says; pyscriptc never builds sdists, so pick a version with a wheel.",
    ),
)

DIAGNOSTICS: dict[str, Diagnostic] = {entry.code: entry for entry in _ENTRIES}


def explain(code: str) -> str | None:
    entry = DIAGNOSTICS.get(code.upper())
    if entry is None:
        return None
    return f"{entry.code}: {entry.title}\n\nWhy: {entry.why}\n\nFix: {entry.fix}"
