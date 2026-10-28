---
title: "Libraries and fallback mode"
description: "How to use third-party and unsupported modules: embed them with --fallback=cpython or call compiled code from Python via bindings."
---

pyscriptc compiles the typed subset of Python 3.14.
Every dependency gets one of two execution modes: it compiles to native code when it fits the subset, or it runs through embedded CPython (fallback mode) when it does not.
An untyped dependency is never impossible to use: it runs in fallback mode.

## Route a module to fallback mode

Fallback mode links your binary against `libpython` and runs the unsupported parts in an in-process CPython 3.14 interpreter, while the typed functions still compile to native code.
Enable it on the command line:

```bash
pyscriptc compile script.py --fallback=cpython -o script_bin
pyscriptc run --fallback=cpython script.py
```

`--island` is an alias for `--fallback=cpython`.
You can also set it in `pyproject.toml` so no flag is needed:

```toml
[tool.pyscriptc.fallback]
definitions = true
```

Consider a script that mixes a typed function with an untyped one:

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

Without the flag the compile stops on the unannotated `raw` parameter (`PS1001`).
With `--fallback=cpython` the unsupported definitions run in the embedded interpreter.
Note that `helper` runs there too: when an unsupported function calls a typed one, the compiler moves the callee into the same interpreter so both run together.

## See what compiles and what does not

`pyscriptc assess` reports which definitions compile and which do not, with the fix for each refusal:

```bash
pyscriptc assess script.py
```

`pyscriptc imports SOURCE` classifies every imported module as `native` or as requiring libpython.
`pyscriptc run --fallback=cpython --report=report.json script.py` writes the same classification as JSON after a run.

## The imports table

The `[tool.pyscriptc.imports]` table in `pyproject.toml` routes each top-level module name to a lane: `compile` (default), `refuse` (always a compile error naming the policy site), or opt-in fallback entries.
Set it when you want the decision recorded in the project instead of on the command line.
The full key reference is [Config](../reference/config.md).

## How values cross

- Scalars (`int`, `float`, `bool`), `str`, `bytes` and lists of scalars cross by value.
A list is copied, so a change made on the embedded side is never visible to native code.
- Dictionaries and class instances cross as opaque handles supporting attribute access, subscript lookups, method calls and `str()` formatting.
- Exceptions raised in the embedded interpreter are catchable from native code: a native `try ... except ValueError:` catches a `ValueError` raised there.
Unknown exception types surface as `RuntimeError` carrying the original class name and message.
- `raise SystemExit(code)` out of the embedded side exits the process with that status.

```python
def risky(x):
    y = [x, "tag"]
    if x < 0:
        raise ValueError("negative value")
    return len(y) + x


def main() -> None:
    try:
        val: int = risky(-5)
        print(f"val: {val}")
    except ValueError as exc:
        print(f"handled ValueError: {exc}")


if __name__ == "__main__":
    main()
```

```bash
pyscriptc run --fallback=cpython script.py
# handled ValueError: negative value
```

## Call compiled code from Python with bindings

The other direction works across module boundaries with `--emit binding`, which produces a shared library plus a `ctypes` loader module:

```bash
pyscriptc compile typed_math.py --emit binding -o typed_math
```

```python
from typed_math_binding import add

print(add(20, 22))
```

Use this when a plain CPython script, or code running in fallback mode, needs to call compiled functions from another module.

## Environment and limits

Fallback mode picks up the host Python environment:

- If `VIRTUAL_ENV` is set, its `site-packages` is on the path.
- `PYTHONPATH` entries are added.
- The directory containing the source file is on `sys.path`, and dependency roots from `pyproject.toml` or `--dependency-root` join both the type-checker search path and the embedded interpreter's path.

Limits to know:

- Fallback mode provides compatibility, not native speed: the embedded parts run under the interpreter, calls crossing the boundary pay a marshalling cost, and native threads serialize at the GIL.
- Closures and generators cannot be passed across the boundary.
- Cross-compiling a libpython-linked binary to a foreign target is refused (divergence 31); fallback mode needs the host libpython and its installed packages.
- Uncaught exceptions from the embedded side print the type and message but not a traceback (divergences 12 and 42).
- Unsupported module-level statements plan the whole module top level for the embedded interpreter (divergence 42).

The complete crossing rules and numbered divergences: [Divergences from CPython](../reference/divergences.md).
Per-module and per-library support status: the [compat site]({{PYSC_COMPAT_URL}}).
