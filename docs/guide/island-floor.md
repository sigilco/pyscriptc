---
title: The island floor
description: How to run real scripts with the CPython fallback tier, read the plane map, and bridge native and island execution.
---

pyscriptc compiles the typed subset of Python 3.14. Native compilation to standalone machine code is the ceiling, and the embedded CPython island is the floor. There is no byte-for-byte CPython parity goal, and the compiler adds no dynamic emulation shims for untyped code. When a construct cannot be lowered to native IR, compilation stops at a named `PSxxxx` fence unless the fallback tier is enabled.

## When to use `--fallback=cpython` (alias `--island`)

By default, any unlowered construct, unannotated definition, or unlowered import stops compilation with exit code 3 (or preflight checker code 2). The fallback tier routes fenced function definitions and untyped imports to an embedded in-process CPython 3.14 interpreter runtime instead of refusing the build. Typed functions continue to compile to native code, while untyped or fenced functions run on the island floor.

Enable the fallback tier on the command line using `--fallback=cpython` or its alias `--island`:

```bash
pyscriptc compile script.py --fallback=cpython --emit bin -o script_bin
pyscriptc run --fallback=cpython script.py
pyscriptc run --island script.py
```

You can also enable definition fallback in `pyproject.toml` so invocations do not require CLI flags:

```toml
[tool.pyscriptc.fallback]
definitions = true
```

## Reading the plane map (`pyscriptc assess` and `pyscriptc run --report`)

Before compiling, you can inspect which definitions will lower natively and which will fall to the island floor using `pyscriptc assess`. Consider this mixed script `demo.py`:

```python
import json


def helper(x: int) -> int:
    return x * 2


def parse_data(raw):
    return json.loads(raw)


def main() -> None:
    data = parse_data('{"count": 21}')
    result: int = helper(data["count"])
    print(f"result: {result}")


if __name__ == "__main__":
    main()
```

Running `pyscriptc assess demo.py` outputs the capability summary and the definition plane map:

```text
$ pyscriptc assess demo.py
assess demo.py:

Summary:
  Native:    33.3% (1 of 3 definitions)
  Island:     0.0% (0 of 3 definitions,   0.0% import-level)
  Blocked:   66.7% (2 of 3 definitions)
  Files:    1 (0 lowers fully, 1 partly, 0 refused)
  Imports:  1 (1 native, 0 fenced, 0 island)

Files:
  demo.py: lowers partly (1 native, 2 fenced, 0 island of 3 definitions, 3 fences)
    unsupported construct:
      - line 6: PS1001 parameter raw needs an annotation
        typed idiom: Annotate parameter `raw`. Unannotated parameters need local type inference.
      - line 10: PS2002 call to parse_data is not lowered
        typed idiom: Call a lowered function, or rewrite the call with lowered constructs.
      - line 11: PS2002 data is not a local or parameter
        typed idiom: The name is not a local, a parameter or a lowered global. Declare it or import it.

Plane map:
  module top level: native
  helper: island (demoted by main)
  main: island
  parse_data: island

Imports:
  json: native

Path to the island:
  Add to pyproject.toml:
    [tool.pyscriptc.fallback]
    definitions = true
```

Notice that `helper` is a typed function, but the plane map marks it `helper: island (demoted by main)`. Because `main` fences and falls to the island, its call to `helper` triggers the cascade rule, which demotes `helper` to the island so both execute in the same plane.

When executing with `pyscriptc run`, cached runs remain completely silent on stderr by default. Passing `--report` prints the executed plane map as JSON to stderr after the run:

```bash
$ pyscriptc run --fallback=cpython --report demo.py
result: 42
{
  "target": "demo.py",
  "planeMap": {
    "moduleTopLevel": "native",
    "functions": {
      "helper": "island",
      "parse_data": "island",
      "main": "island"
    },
    "topLevelFences": 0,
    "demotedBy": {
      "helper": "main"
    }
  },
  "fallback": {
    "moduleTopLevel": "native",
    "functions": {
      "helper": "island",
      "parse_data": "island",
      "main": "island"
    },
    "topLevelFences": 0,
    "demotedBy": {
      "helper": "main"
    }
  },
  "requiresLibpython": true,
  "islandImports": []
}
```

To capture the plane map to a file without printing to stderr, provide a file path:

```bash
pyscriptc run --fallback=cpython --report=run_report.json demo.py
```

The plane map JSON records:
- `moduleTopLevel`: `native` if module-level statements lower directly, or `island` if top-level code fences.
- `functions`: mapping of every function definition to `native` or `island`.
- `classes`: mapping of every class definition to `native` or `island`.
- `demotedBy`: mapping of functions demoted from native to island by a calling island function, or by naming an island class.
- `requiresLibpython`: boolean indicating whether the binary links the embedded host CPython runtime.
- `islandImports`: list of imports routed through the island.

## The two seams

Execution moves across two distinct boundaries: compiled native code calling island code, and island code calling compiled code.

### Seam 1: Compiled calling the island

When native code calls an island function or an island import, the call routes through the `ps_island_fn_invoke` trampoline. This routing is automatic for untyped imports and untyped definitions under the fallback tier.

A class definition follows the same rule. A class whose own definition the subset refuses (a decorator such as `@attrs.define` or `@dataclasses.dataclass(frozen=True)`, a metaclass keyword, a class-body statement) or whose method bodies refuse is defined by CPython in the island, and every compiled function that names it joins the island too. A native class method that still uses the island class reaches it through the handle path: `Point(3, 4)` calls the class object through `ps_island_fn_invoke`, `Point.kind` reads a class attribute through `ps_island_def_handle` and `ps_island_getattr`, and an annotated local read of an instance attribute (`x: int = point.x`) unboxes it. Attribute writes from compiled code are fenced: mutate the value island-side and read the result back.

Value marshalling across this seam follows strict rules (see [divergence 41](../reference/divergences.md#numbered-divergences)):
- Scalars (`int`, `float`, `bool`), `str`, `bytes`, and lists of scalars cross by value. Lists are copied across the seam; island-side mutations of a passed list are never visible to the native caller.
- Dictionaries, custom class instances, and complex objects cross as opaque handles (`ps_island_handle`).
- An opaque handle supports attribute access (`getattr`), subscript lookups (`getitem`), nested method and function calls, and `str()` or f-string formatting. Native arithmetic and comparison operators on opaque handles are refused.
- Return values from island functions are unmarshalled into native values when the call site assigns to an annotated local variable of scalar, `str`, `bytes`, or scalar list type.
- Keyword arguments and varargs forwarding (`*args`, `**kwargs`) are supported at island call sites.

Here is an example where a native `main` calls an island function and catches an exception:

```python
def risky_island_call(x):
    y = [x, "tag"]
    if x < 0:
        raise ValueError("negative value")
    return len(y) + x


def main() -> None:
    try:
        val: int = risky_island_call(-5)
        print(f"val: {val}")
    except ValueError as exc:
        print(f"handled ValueError: {exc}")


if __name__ == "__main__":
    main()
```

Running this script handles the error across the seam:

```bash
$ pyscriptc run --fallback=cpython exc_seam.py
handled ValueError: negative value
```

### Seam 2: Island calling compiled (the cascade rule and bindings)

Within a single script or module, an island function cannot directly call a compiled native sibling without bridging. To make real scripts run without manual rewrites, the compiler applies the cascade rule (issue #620): when an island function calls a native sibling function, the fallback planner automatically demotes the callee sibling to run on the island as well. This demotion cascades recursively to all siblings called by the demoted function.

Demotion is refused if the callee cannot run on the island:
- Generic functions using `typing.TypeVar` cannot be demoted.
- Nested or non-module-level functions cannot be demoted.

When demotion is impossible, compilation halts with diagnostic `PS2002` explaining why and pointing to the binding seam:

```text
undemotable.py:10:12: PS2002: island_fn runs on the island and cannot call the compiled function generic_helper: generic compiled function generic_helper cannot be demoted to the island; the island-to-compiled seam is the `--emit binding` surface
```

To call compiled native functions from Python or island code across module boundaries, compile with `--emit binding`:

```bash
pyscriptc compile typed_math.py --emit binding -o typed_math
```

This command generates a shared library (`libtyped_math.dylib`, `.so`, or `.dll`) and a ctypes loader module `typed_math_binding.py`. Python code, including scripts running in standard CPython or on the island floor, can import the binding module to call native functions directly:

```python
from typed_math_binding import add

print(add(20, 22))
```

## Environment (venv, PYTHONPATH, program directory, dependency roots)

The embedded island runtime initializes with an environment aligned to the host Python environment:
- Virtual environment: if `VIRTUAL_ENV` is set, or if the compiler runs inside a virtual environment where `sys.prefix != sys.base_prefix`, the island runtime calls `site.addsitedir` on `<venv>/lib/python3.14/site-packages` (or `<venv>\Lib\site-packages` on Windows).
- `PYTHONPATH`: directory entries in `PYTHONPATH` (delimited by `:` on POSIX or `;` on Windows) are added via `site.addsitedir`.
- Program directory: the directory containing the source file is placed at `sys.path[0]` and registered via `site.addsitedir` (passed via `PYSC_PROGRAM_DIR`).
- Dependency roots: unpacked dependency directories declared in `pyproject.toml` or supplied via `--dependency-root` join both the preflight type-checker search path and the island's `sys.path`.
- Preflight checker suppression: under `--fallback=cpython`, type-checker errors located inside island-routed functions or an island top level are suppressed as notes rather than halting compilation with exit code 2. Type errors inside native definitions still halt the build.

## Exceptions and exit status behavior

The island floor preserves CPython's exit code and exception behavior:
- `raise SystemExit(code)`: when `code` is an integer, the process terminates with that exact status code and stderr remains clean.
- `raise SystemExit("message")`: when called with a string, the message is written to stderr and the process terminates with exit code 1.
- Bare `raise SystemExit()`: exits with status code 0.
- Uncaught exceptions: uncaught exceptions from island code print the exception class name and error message to stderr and terminate with exit code 1. Unlike CPython, traceback lines are not emitted (see [divergence 12](../reference/divergences.md#numbered-divergences) and [divergence 42](../reference/divergences.md#numbered-divergences)).
- Cross-seam exceptions: exceptions raised inside island functions map onto the compiler's closed native exception table (`ValueError`, `KeyError`, `IndexError`, `TypeError`, `OSError`, `RuntimeError`, `SystemExit`). A native `try ... except ValueError:` block catches an island-raised `ValueError`. Unmapped custom exceptions surface as `RuntimeError` carrying the original class name and message.

Here is an example verifying status code propagation:

```python
def run():
    raise SystemExit(42)


if __name__ == "__main__":
    run()
```

```bash
$ pyscriptc run --fallback=cpython exit_demo.py
$ echo "Exit code: $?"
Exit code: 42
```

## What stays slow or refused

The island tier provides compatibility, not native performance or unlimited dynamic semantics.

### What stays slow

- Interpreter execution: island functions execute under the host CPython 3.14 interpreter; they do not receive native machine code optimization.
- GIL contention: every entry into the island acquires the CPython GIL (`ps_island_ensure`). Native threads calling island functions serialize at the GIL.
- Seam crossing overhead: marshalling arguments, unboxing return values, and managing opaque handle wrappers incur per-call overhead. Calling island functions in tight loops will be noticeably slower than native execution.

### What stays refused

- Cross-seam callables: passing closures or generator objects between native code and island code is refused.
- Native writes to island globals: if module top level runs island-side, annotated scalar globals unbox once into native shadow slots at startup. Native code may read these initial values, but native writes to island-owned globals fence at compile time (see [divergence 42](../reference/divergences.md#numbered-divergences)).
- Undemotable sibling calls: direct calls from island functions to generic native functions fence at compile time with `PS2002` unless accessed via `--emit binding`.
- Cross-compilation: the island tier requires the host libpython and host-installed packages. Cross-compiling for WASM (`wasm32-wasi-*`) or other foreign targets with `--fallback=cpython` is refused (see [divergence 31](../reference/divergences.md#numbered-divergences)).
- Subprocess arguments: `subprocess.run` keyword options beyond lowered literals continue to follow [divergence 40](../reference/divergences.md#numbered-divergences).
- Fenced module top level: top-level statements that fence plan the entire module top level for the island (all-or-nothing module top level, documented in [divergence 42](../reference/divergences.md#numbered-divergences)).
