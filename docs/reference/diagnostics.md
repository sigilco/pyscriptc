---
title: Diagnostics
description: Every PSxxxx diagnostic code, what it means, and how to change your code.
---

# Diagnostics

When a compile stops, pyscriptc names a code and prints the line that caused it.
Run `pyscriptc explain PS2001` (or any code) to see one entry in the terminal.

Codes are grouped by kind: `PS0xxx` are compiler internals (never your fault), `PS1xxx` are source and type problems, `PS2xxx` are constructs and calls the subset does not take, `PS3xxx` are environment and configuration problems, and `PS4xxx` are extern, target and object-model refusals.
A code is never reused or renumbered; the stability contract is in [Stability and contracts](stability.md).

<!-- gen:codes -->
### Compiler internals

| Code | What it means | How to change your code |
| ---- | ------------- | ---------------------- |
| `PS0001` | The source or the generated IR failed a structural check before the backend could run. | Fix the input path or syntax error named in the message. |
| `PS0101` | The IR mixes types the emitter cannot pair: operands of one operator disagree, a compare does not yield bool, a condition is not bool, or a returned value has another type than the function returns. Debug-mode verification reports this before the backend runs. | This is a compiler bug: reduce the source and report it with the debug dump. |
| `PS0102` | A name the IR reads is not a parameter, local or env slot of its function, or a call names a function or class the module does not define. Debug-mode verification reports this before the backend runs. | This is a compiler bug: reduce the source and report it with the debug dump. |
| `PS0103` | A statement follows a return or raise that ends its body, or a function with a value return type has a path that reaches the end without one. Debug-mode verification reports this before the backend runs. | This is a compiler bug: reduce the source and report it with the debug dump. |
| `PS0104` | The LLVM module verifier rejected the emitted text (a missing terminator, an undefined value, a malformed instruction). Debug mode runs the verifier over the .ll it dumps. | This is a compiler bug: attach the --debug-dir dump when reporting it. |

### Source and types

| Code | What it means | How to change your code |
| ---- | ------------- | ---------------------- |
| `PS1001` | A parameter, return, or variable needs an annotation the subset can read, or an annotation form has no more specific code. | Annotate the name with int, float, bool, str, bytes, list[T], dict[K, V], tuple[...], or a class the compiler takes. |
| `PS1003` | An operation received a type the compiler does not accept for it. | Convert the operand explicitly, for example int(flag) before math calls. |
| `PS1004` | A function with a non-None return type has a path that reaches the end without a return. | End every path with a return or a raise. |
| `PS1101` | The IR has no tagged union yet, so `A | B` and `Union[A, B]` have no representation. | Narrow the annotation to one arm, or split the function per arm. |
| `PS1102` | An optional of one type (`T | None`, `Optional[T]`) compiles. `A | B | None` and `Union[A, B, None]` need a multi-arm union, which the IR does not have yet. | Narrow to `T | None` with one arm, or split the function per arm. |
| `PS1103` | A subscripted annotation such as Mapping[K, V], Sequence[T], Iterator[T], Callable[...], or type[T] is not a container the compiler takes. | Use list[T], dict[K, V], or tuple[...] in the signature. |
| `PS1104` | object, Any, Protocol, TypeVar, Generic, Callable and ABC bases name dynamic shapes the IR has no slot for. Typing names are annotation-only: they steer compilation, they are never runtime values. | Annotate with the concrete type the function really takes. |
| `PS1105` | A quoted annotation such as 'Node' is not resolved at compile time. | Define the class before use and write the name unquoted. |
| `PS1106` | The annotation is a plain name that is not a builtin scalar, a compiled class, or a known alias. | Import the concrete class, or replace the alias with the concrete type. |
| `PS1201` | Only a closed set of decorators compiles. Other decorators change behavior the compiler cannot see. | Remove the decorator, or inline what it does. |
| `PS1202` | Positional-only, keyword-only, *args and **kwargs parameters are not supported. An unannotated **kwargs parameter needs a runtime keyword mapping. | Use plain positional parameters, with defaults where needed. An **kwargs parameter runs through embedded CPython (fallback mode); declare its keys with PEP 692 Unpack[TypedDict]. |
| `PS1203` | Class keywords (metaclass=...) and multiple inheritance are not supported. A metaclass builds the class at run time and native code fixes the layout at compile time. | Use a single base class the compiler takes and no class keywords. Write the class explicitly, or use PEP 695 generics instead of metaclass synthesis. |
| `PS1204` | A class body holds only annotated fields without values and method definitions. | Move assignments into __init__ and declare fields with an annotation only. |
| `PS1205` | `except E as name` and bare `except:` are not supported. | Use a compiled exception class and drop the `as` binding. |
| `PS1206` | `with ... as name` compiles only for a closed set of context managers. | Use a supported context manager without the binding, or open and close explicitly. |

### Constructs and calls

| Code | What it means | How to change your code |
| ---- | ------------- | ---------------------- |
| `PS2001` | The module imports, but this attribute is outside the surface the compiler takes. An attribute that reads runtime metadata (inspect.signature, __annotations__) is runtime reflection and routes the definition to embedded CPython. | Use an attribute of the module the compiler takes. For reflection, use the typed idiom the message names, or compile with --fallback=cpython. |
| `PS2002` | A statement, expression, import, or definition has no compiled form. Dynamic forms such as **kwargs keyword forwarding or an annotation read at run time are runtime reflection and cannot be compiled natively. | Rewrite it with the typed idiom the message names (PEP 692 Unpack[TypedDict] for **kwargs), or compile with --fallback=cpython. |
| `PS2003` | A call has keyword arguments, or the wrong number of arguments, for the form the compiler takes. | Pass positional arguments in the arity the compiler takes. |
| `PS2004` | The operator is not supported for these operand types. | Convert the operands to a supported pair of types. |
| `PS2005` | Strings are byte sequences in the runtime, so only Latin-1 text compiles. | Use Latin-1 text, or move the literal to bytes. |
| `PS2006` | A user decorator compiles as a direct call of a function value, so the decorator expression must be typed as a function that takes the decorated function and returns one. An expression such as `@click.command()` returns a class instance, an untyped value or an embedded-CPython value, which carries no parameter list to check the wrapped function against. | Use a decorator typed as a function returning a function, or apply the wrapper explicitly in the function body. Under `--fallback=cpython` the decorated definition runs through embedded CPython. |
| `PS2101` | The module is planned but not compiled yet. | Avoid the module, or wait for the queue entry to land. |

### Environment and configuration

| Code | What it means | How to change your code |
| ---- | ------------- | ---------------------- |
| `PS3001` | The running interpreter is not the CPython version the subset targets. | Run under the pinned CPython version. |
| `PS3002` | The emitted program exceeds a fixed limit of the backend. | Reduce the offending size or count. |
| `PS3101` | `pyscriptc deps` could not read, resolve, download or verify a PyPI dependency (bad PEP 723 or pyproject metadata, sdist-only package, stale lock under --frozen, hash mismatch). | Fix the metadata or lock as the message says; pyscriptc never builds sdists, so pick a version with a wheel. |
| `PS3102` | A `pyscriptc.toml`, a `[tool.pyscriptc]` table, or PEP 723 script metadata failed schema validation. | Fix the key named in the message; `pyscriptc explain` lists no other config diagnostics. |

### Externs, targets and the object model

| Code | What it means | How to change your code |
| ---- | ------------- | ---------------------- |
| `PS4001` | The manifest passed to --ffi could not be read or is not valid TOML. | Fix the file path or the TOML syntax named in the message. |
| `PS4002` | The [manifest] section is missing, is not a table, or its module name is not a dotted identifier. | Add [manifest] with a module, ps_abi and format key. |
| `PS4003` | The manifest's format pin is missing or names a schema version this compiler does not read. | Rewrite the manifest against a supported format version. |
| `PS4004` | The manifest's ps_abi pin is missing or does not match the PS_ABI epoch this compiler stamps. | Regenerate or update the manifest for the compiler's ps_abi epoch. |
| `PS4005` | A manifest key is missing, has the wrong type, or is not part of the schema. | Keep the manifest to the keys documented in .agents/docs/ffi/ffi-manifest.md. |
| `PS4006` | A py signature or type in the manifest is outside the vocabulary the FFI lane marshals. | Use the scalar and container types, or a declared handle, from .agents/docs/ffi/ffi-manifest.md. |
| `PS4007` | A name is declared twice in one manifest, or two manifests claim the same module. | Give each handle, const, function and module a unique name. |
| `PS4008` | An ownership annotation or handle reference names something the manifest does not declare. | Declare the handle, or fix the reference to a declared handle or parameter. |
| `PS4009` | A [[source]] entry names a C file, archive or object that does not exist on disk. | Point the manifest at the file that ships with the adapter, or fetch the vendored C first. |
| `PS4010` | A call whose manifest entry lists consumes took ownership of the handle, so the variable is dead: reading it would use a freed pointer. | Do not use the handle after its consuming call. Open the resource again, or use what the consuming call returns. |
| `PS4011` | The call consumes a handle, but the argument is not a handle variable this function owns (a literal, a call result, or a temporary), so the compiler cannot prove the value is dead afterwards. | Bind the handle to a local variable first, then pass that variable to the consuming call. |
| `PS4012` | Two handle types are both opaque pointers at the ABI, so passing one for the other would be accepted by the link and corrupt memory. | Pass the handle type the manifest signature names; open the resource with the right connect call if needed. |
| `PS4013` | The manifest declares a handle but no function consumes it, so a scope-exit free has no thunk to call and the handle would leak. | Add a [[fn]] whose consumes names this handle (a close or finalize wrapper), or pass every handle to the consuming call explicitly. |
| `PS4014` | A manifest handle names a type, like a class name. The value is an opaque pointer that only crosses FFI calls. | Annotate a variable with the handle type, or pass it to a manifest function; never use the handle name itself as a value. |
| `PS4015` | A handle is an opaque pointer at the ABI: it has no fields, and a read would be a wild access into foreign memory. | Pass the handle to a manifest function instead of reading its fields; the adapter's thunk is where the C struct is touched. |
| `PS4016` | Arithmetic on a handle is pointer arithmetic on a foreign object. The ABI passes handles as opaque tokens and never computes with them. | Use the handle only as a token: pass it to manifest calls. Convert a C-side value to an int through a manifest function if you need a number. |
| `PS4101` | The target (an iOS device, the iOS simulator or Android) has no standalone executable: a mobile app links a static library, so a `--emit bin` build has nothing to run on the device. | Use `--emit lib --target <triple>` to produce libpyscriptc.a plus pyscriptc.h and module.modulemap, and call pyscriptc_init() from the app. |
| `PS4201` | `__setattr__`, `__getattribute__`, `__get__` and `__set__` intercept attribute access at run time. Attribute access is resolved statically to declared fields, so a hook that can intercept any name lookup or write has nothing to run in, and the hook would be silently dropped or silently bypassed. | Use plain annotated fields and methods. `@property` getters compile; `__getattr__` compiles for unknown-name reads (issue #434); unknown-name delegation otherwise becomes an explicit method call. |
| `PS4202` | `__delattr__` intercepts `del obj.name`, which the compiled subset does not model: fields are fixed at compile time and never removed, so the hook would never run. | Use plain annotated fields. Model 'removed' with a sentinel value or a flag field. |
| `PS4203` | `__del__` is a finalizer: CPython calls it when the object is garbage-collected, and that timing is explicitly unspecified. The runtime has no GC to trigger it, so the method would be silently dropped and the cleanup would never run. | Use `with` and an explicit `close()` for resource lifetime. |
| `PS4204` | An unknown-name read on a class with `__getattr__` compiles as a direct call of the hook with the attribute name as a compile-time constant. The hook's declared return type is what the read gets, so it must cover the read site's expected type exactly; a hook that returns `None`, `bool` or an optional cannot feed an arbitrary read. | Narrow the hook's return annotation to one value type that every read site expects, or split the class: one `__getattr__` per return shape, or explicit methods instead of the hook. |
<!-- /gen:codes -->

## What a refusal means for your program

A `PSxxxx` code is not an error inside a working program: it means this one construct has no compiled form yet.
You have two ways forward:

- Change your code so the construct fits the subset (the table above names the change for each code). The page to check what the subset takes is [The Python subset](../guide/python-subset.md).
- Compile with `--fallback=cpython` (alias `--island`) so the functions that carry the code run through embedded CPython (fallback mode) instead. This needs libpython and the packages on the host; see [Runtime pack](runtime-pack.md).

Runtime differences that survive a successful compile are not diagnostic codes; they are the numbered list in [Divergences from CPython](divergences.md).
