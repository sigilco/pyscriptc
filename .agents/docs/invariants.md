# Invariants

## Fail closed

Unknown syntax, an unknown import, and a call outside a module's lowered surface are fences. They are not lowered as a best guess. A fence exits `3` and names a `PS` code. Do not emit LLVM for that file.

## The checker is not the IR

The checker runs as a subprocess (`src/pyscriptc/check.py`); pyrefly is the default (`--checker mypy`/`pyright` deprecated but kept for sanity-diffing and the builtins realignments). Lowering reads annotations from CPython's `ast`. Do not walk the checker's tree. `typing` stays in the checker. It is not a runtime module.

## Stamp

The subset string stays `gates-v1` until a deliberate revision of the language. The IR records `sys.version` of the interpreter that lowered the file. The emitter refuses a different stamp (`PS3001`). Re-lower the file on the interpreter you are emitting with.

## Integers

IR integers are decimal strings. LLVM uses `i64` only when the value fits, traps on overflow (`llvm.*.with.overflow`), and refuses a literal that does not fit (`PS3002`). Floor division matches Python, including negative operands. Division by zero and `INT64_MIN / -1` trap. `bool` is not an `int` here: bool arithmetic is a fence.

## Float literals

Emit raw IEEE bits (`0x{bits:016X}`). Clang 18 rejects Python `float.hex()` inside a call argument.

## Strings

Non-ASCII literals are `PS2005` until the str model lands (epic #472). `str` crosses the C ABI as `%ps.str = { i64, ptr, i64, i32 }`: byte length, data pointer, code-point count, flags (bit 0 ASCII, bit 1 reserved zero). `bytes` crosses as `PsBuf` (16 bytes, `{ i64, ptr }`), never as a returned struct. The same rule applies to every aggregate the runtime returns: pass a pointer or write through an out-parameter, and no runtime call returns a `%ps.str` by value except `ps_exc_str`. Design: `.agents/docs/spec/str-model.md`.

## The island

Link libpython only when the LLVM text contains `ps_island_`. Compile `ps_island.c` only then. The link tail is `-L$LIBDIR -lpython3.14 -Wl,-rpath,$LIBDIR`, with paths from `sysconfig` in the interpreter running pyscriptc. `#define PY_SSIZE_T_CLEAN` comes before `Python.h`. `Py_InitializeEx(0)` runs on the first island call.

Constants baked from that same interpreter (`sys.platform`, `socket.AF_INET`, `ssl.OPENSSL_VERSION`, `ctypes.sizeof`, and the others in `stdmore.CONSTANTS`) are not island calls.

## One file

Relative import is a fence. A module-level constant is an annotated assignment or a bare assignment of a literal, list, or dict. Other module-level statements, other than imports, function definitions, class definitions, a docstring, and `if __name__ == "__main__"`, are fences. The `if __name__` guard is skipped. It is not lowered. `import typing`, `import typing_extensions`, and a type-related `from __future__ import` are erased. They are not values.

## Exceptions

`setjmp` exists only inside `ps_try_call`. Codes: `ValueError` 1, `IndexError` 2, `KeyError` 3, `OverflowError` 4, `ZeroDivisionError` 5, `RuntimeError` 6, `Exception` 7. `re.error`, `struct.error`, and `binascii.Error` surface as `ValueError`. OS errors surface as `RuntimeError`. An unset `os.getenv` raises `KeyError`.

## Benchmarks

Printed speed is `Nx CPython`, the reciprocal of the stored native/CPython ratio. The gate still compares ratios: time growth past `2.0` or RSS growth past `1.25` fails. The top-level geomean in `benchmarks/baseline/reference.json` is the original four kernels. New workloads go in `group_geomeans`. Do not `--record` a partial run over that file.

## Checks

```bash
mise run lint
mise run format-check
mise run test
mise run bench:baseline
```

Each task wraps one raw command: `uv run ruff check`, `uv run ruff format --check`, `uv run pytest` and `uv run python -m benchmarks.baseline.run --check`.

Use the project interpreter (`uv run`, or `.venv/bin/python`). The system `python3` on a developer machine may be older than 3.14.
