# Map

| Path | Owns |
| --- | --- |
| `src/pyscriptc/cli.py` | argparse, exit codes, `--emit`, `--run`, `--args` |
| `src/pyscriptc/check.py` | mypy subprocess |
| `src/pyscriptc/debug.py` | `--debug` / `PYSC_DEBUG` stage table, `--debug-dir` artifacts |
| `src/pyscriptc/errors.py` | `IRError`, `SpikeError`, `VersionError`, `FitError` |
| `src/pyscriptc/ir.py` | JSON schema, `IR_VERSION`, `SUBSET`, forbidden scriptc kinds |
| `src/pyscriptc/lower.py` | Language, classes, calls, `re` / `json` / `urllib.parse` / `base64` / `hashlib` / `struct` |
| `src/pyscriptc/package.py` | Directory compile. Exports functions, constants, and classes the defining module emits. Skips `__main__.py`. A definition fence precedes "is not exported" |
| `src/pyscriptc/restlower.py` | Remaining queued modules and builtin helpers |
| `src/pyscriptc/stdlib_queue.py` | Import table, `math` signatures, codec signatures |
| `src/pyscriptc/stdmore.py` | Rest-of-queue signatures, baked constants, island call list |
| `src/pyscriptc/builtins.py` | `str` / `bytes` methods, `len`, `int(float)`, `float(int)`, exception codes |
| `src/pyscriptc/llvm.py` | Textual LLVM, `main`, clang link line |
| `src/pyscriptc/runtime/*.c` | Implementations the LLVM text calls |
| `src/pyscriptc/runtime/ps_island.c` | Embedded CPython. Linked only when the text contains `ps_island_` |
| `tests/test_gates.py` | Language gates and the original example corpus |
| `tests/test_examples.py` | The scripts in `examples/` that a user runs |
| `tests/test_adoption.py` | Shapes from real libraries that must stay fenced |
| `benchmarks/baseline/` | Time and RSS gate |
| `benchmarks/adoption/README.md` | Which external suites stay on CPython |
| `benchmarks/native/` | C ABI `.so` versus a `PyInit_*` extension on the island |
| `src/pyscriptc/runtime/ps_mix.c` | `dlopen` of that C ABI. No Python headers |
| `.agents/docs/roadmap.md` | Language order after the stdlib queue |

## Adding a lowered call

1. Put the module in `QUEUE` in `stdlib_queue.py` if it is new. `LOWERED` is `frozenset(QUEUE)`. A name in the queue and missing from the lowered set is `PS2101`. Tests lock the queue order.
2. Add a fixed signature in `stdlib_queue.py` (codec wave) or `stdmore.py` (everything else), or a special case in `lower.py` / `restlower.py` when the type depends on an annotation or a literal.
3. Implement the C function in the matching `runtime/ps_*.c` file. Declare it in `runtime/ps_rt.h`. Return aggregates through pointers.
4. Teach `llvm.py` the symbol if the call is not already a generic runtime call.
5. Add a probe assertion in `examples/wave_probe.py` or `examples/rest_probe.py`, and a case in `tests/test_gates.py` or `tests/test_stdlib_bench.py`. The probe returns `0` when the call matches CPython.
6. If the call is worth timing, add a workload under `benchmarks/runtime/workloads/`, a catalog row, and a suite row. Merge the measured row into `benchmarks/baseline/reference.json` by appending it. Do not replace the four kernel ratios.

## Fence codes

| Code | Meaning |
| --- | --- |
| `PS1001` | A name or annotation is missing |
| `PS1003` | The types of a call or an expression do not match |
| `PS1004` | A function can fall off the end |
| `PS2001` | The name is outside the lowered surface of a module that did import |
| `PS2002` | The syntax or import is outside the subset |
| `PS2003` | The argument count is wrong, including a keyword argument |
| `PS2004` | An operator or a condition is outside the subset |
| `PS2005` | A string literal outside Latin-1 |
| `PS2101` | Queued, not lowered yet. Nothing is in this state while `LOWERED == frozenset(QUEUE)` |
| `PS3001` | IR stamp is a different CPython |
| `PS3002` | A value does not fit what `main` or `i64` can carry |

`main` can pass and print `int`, `float`, and `str`. It can pass a `bool` and cannot print one. It cannot pass a `list`, a `dict`, or `bytes`. Build those inside the function. The scripts in `examples/` follow that rule so `--run --call` works.
