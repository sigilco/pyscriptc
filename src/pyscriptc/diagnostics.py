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
        "PS0101",
        "IR type inconsistency",
        (
            "The lowered IR mixes types the emitter cannot pair: operands of one "
            "operator disagree, a compare does not yield bool, a condition is not "
            "bool, or a returned value has another type than the function returns. "
            "Debug-mode verification reports this before the backend runs."
        ),
        "This is a compiler bug: reduce the source and report it with the debug dump.",
    ),
    Diagnostic(
        "PS0102",
        "IR name is not defined",
        (
            "A name the IR reads is not a parameter, local or env slot of its "
            "function, or a call names a function or class the module does not "
            "define. Debug-mode verification reports this before the backend runs."
        ),
        "This is a compiler bug: reduce the source and report it with the debug dump.",
    ),
    Diagnostic(
        "PS0103",
        "IR terminator missing",
        (
            "A statement follows a return or raise that ends its body, or a "
            "function with a value return type has a path that reaches the end "
            "without one. Debug-mode verification reports this before the backend runs."
        ),
        "This is a compiler bug: reduce the source and report it with the debug dump.",
    ),
    Diagnostic(
        "PS0104",
        "emitted LLVM did not verify",
        (
            "The LLVM module verifier rejected the emitted text (a missing "
            "terminator, an undefined value, a malformed instruction). Debug mode "
            "runs the verifier over the .ll it dumps."
        ),
        "This is a compiler bug: attach the --debug-dir dump when reporting it.",
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
        "Keep the manifest to the keys documented in .agents/docs/ffi/ffi-manifest.md.",
    ),
    Diagnostic(
        "PS4006",
        "FFI signature outside the subset",
        "A py signature or type in the manifest is outside the vocabulary the FFI lane marshals.",
        "Use the scalar and container types, or a declared handle, from .agents/docs/ffi/ffi-manifest.md.",
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
        "PS4009",
        "FFI manifest unit missing",
        "A [[source]] entry names a C file, archive or object that does not exist on disk.",
        "Point the manifest at the file that ships with the adapter, or fetch the vendored C first.",
    ),
    Diagnostic(
        "PS4010",
        "use of a consumed FFI handle",
        "A call whose manifest entry lists consumes took ownership of the handle, so the variable is "
        "dead: reading it would use a freed pointer.",
        "Do not use the handle after its consuming call. Open the resource again, or use what the "
        "consuming call returns.",
    ),
    Diagnostic(
        "PS4011",
        "FFI consume needs an owned variable",
        "The call consumes a handle, but the argument is not a handle variable this function owns (a "
        "literal, a call result, or a temporary), so the compiler cannot prove the value is dead "
        "afterwards.",
        "Bind the handle to a local variable first, then pass that variable to the consuming call.",
    ),
    Diagnostic(
        "PS4012",
        "wrong FFI handle type",
        "Two handle types are both opaque pointers at the ABI, so passing one for the other would be "
        "accepted by the link and corrupt memory.",
        "Pass the handle type the manifest signature names; open the resource with the right connect call if needed.",
    ),
    Diagnostic(
        "PS4013",
        "FFI handle has no consuming function",
        "The manifest declares a handle but no function consumes it, so a scope-exit free has no thunk "
        "to call and the handle would leak.",
        "Add a [[fn]] whose consumes names this handle (a close or finalize wrapper), or pass every "
        "handle to the consuming call explicitly.",
    ),
    Diagnostic(
        "PS4014",
        "FFI handle used as a value",
        "A manifest handle names a type, like a class name. The value is an opaque pointer that only "
        "crosses FFI calls.",
        "Annotate a variable with the handle type, or pass it to a manifest function; never use the "
        "handle name itself as a value.",
    ),
    Diagnostic(
        "PS4015",
        "FFI handle attribute access",
        "A handle is an opaque pointer at the ABI: it has no fields, and a read would be a wild "
        "access into foreign memory.",
        "Pass the handle to a manifest function instead of reading its fields; the adapter's thunk is "
        "where the C struct is touched.",
    ),
    Diagnostic(
        "PS4016",
        "FFI handle arithmetic",
        "Arithmetic on a handle is pointer arithmetic on a foreign object. The ABI passes handles as "
        "opaque tokens and never computes with them.",
        "Use the handle only as a token: pass it to manifest calls. Convert a C-side value to an int "
        "through a manifest function if you need a number.",
    ),
    Diagnostic(
        "PS4201",
        "dynamic attribute protocol refused",
        (
            "`__getattr__`, `__setattr__`, `__getattribute__`, `__get__` and `__set__` "
            "intercept attribute access at run time. Attribute access is resolved "
            "statically to declared fields, so a hook that can intercept any name "
            "lookup or write has nothing to run in, and the hook would be silently "
            "dropped or silently bypassed."
        ),
        (
            "Use plain annotated fields and methods. `@property` getters lower; "
            "unknown-name delegation becomes an explicit method call."
        ),
    ),
    Diagnostic(
        "PS4202",
        "attribute deletion protocol refused",
        (
            "`__delattr__` intercepts `del obj.name`, which the lowered subset does "
            "not model: fields are fixed at lowering and never removed, so the hook "
            "would never run."
        ),
        "Use plain annotated fields. Model 'removed' with a sentinel value or a flag field.",
    ),
    Diagnostic(
        "PS4203",
        "finalizer refused",
        (
            "`__del__` is a finalizer: CPython calls it when the object is "
            "garbage-collected, and that timing is explicitly unspecified. The "
            "runtime has no GC to trigger it, so the method would be silently "
            "dropped and the cleanup would never run."
        ),
        "Use `with` and an explicit `close()` for resource lifetime.",
    ),
    Diagnostic(
        "PS4101",
        "no executable build on a mobile target",
        (
            "The target (an iOS device, the iOS simulator or Android) has no standalone "
            "executable: a mobile app links a static library, so a `--emit bin` build has "
            "nothing to run on the device."
        ),
        "Use `--emit lib --target <triple>` to produce libpyscriptc.a plus pyscriptc.h "
        "and module.modulemap, and call pyscriptc_init() from the app.",
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
