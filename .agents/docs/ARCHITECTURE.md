# Architecture

pyscriptc compiles one annotated Python file into a native binary. The pipeline is the same shape as [scriptc](https://github.com/vercel-labs/scriptc): a checker, a typed intermediate representation, then LLVM. The IR is Python's, not scriptc's. Parsing uses the CPython that is running the compiler. [mypy](https://mypy-lang.org/) is a preflight. Its tree is thrown away.

The goal is to track CPython instead of inventing a dialect that drifts. Unknown syntax, an unknown import, and an integer that does not fit the backend are fences. A newer CPython can parse a file the subset does not lower yet, and the compiler stops with a named reason instead of guessing. The IR is stamped with `sys.version`. LLVM emission refuses a stamp from a different interpreter.

The program that can ship today is a short script: annotated functions over `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, `set` of `int` or `str`, and a class with a single base, plus the closed standard-library surface in `pyscriptc.stdlib_queue`. End-user scripts that fit live in [`examples/`](examples/README.md). How to run them is in the [documentation site](docs/getting-started/index.md). Contributor notes are in [`.agents/docs/`](.agents/docs/README.md).

## Pipeline

```
source.py
  CPython ast
  mypy preflight          (discarded; annotations on the AST are what lowering reads)
  lower.py                JSON IR, subset gates-v1, stamped with sys.version
  emit/llvm/              textual LLVM
  clang -O2               runtime/*.c linked beside the IR
  binary                  --call prints the function's return value
```

`pyscriptc compile FILE` writes the IR next to the LLVM text. `--emit ir` stops after the JSON file. `--emit llvm` stops after the `.ll` file. `--emit bin` and `--run` ask clang for a binary. `--run` also executes it. `--call` names the function the generated `main` calls. `--args` is a Python list literal parsed with `ast.literal_eval`.

Exit codes: `0` success, `1` a broken IR or a missing source, `2` the checker failed, `3` the file hit a fence, `4` the IR stamp or an integer does not fit this backend, `128+signal` when the binary dies on a signal.

## What the binary contains

Two link lines exist.

A file whose LLVM text has no `ps_island_` symbol links the small C runtime in `src/pyscriptc/runtime/` and `libm`. It does not link libpython. The baseline scripts in that shape sit near a few megabytes of RSS.

A file that calls `subprocess.check_output`, `socket.gethostname`, `threading.active_count`, `threading.current_thread().name`, `inspect.cleandoc`, or `logging.info` / `logging.warning` emits `ps_island_*`. The link line then adds `ps_island.c` and `-lpython3.14` from the interpreter that compiled the file, with an rpath to that library. The first island call runs `Py_InitializeEx(0)`. Constants such as `sys.platform`, `socket.AF_INET`, `ssl.OPENSSL_VERSION`, and `ctypes.sizeof` are baked at compile time and do not pull in libpython.

`sys.exit` is libc `exit`, not an island call.

## IR

`src/pyscriptc/ir.py` checks the JSON before emission. Version is `1`. The subset stamp is `gates-v1`. An `int` is a decimal string so JSON cannot round it. Float literals later become raw IEEE bits because the textual IR has to survive clang. JavaScript kinds from scriptc (`f64`, `nullT`, and the rest of `FORBIDDEN_KINDS`) are rejected.

Types come from annotations on the AST. mypy does not supply them. A name is annotated before it is used. A function that can fall off the end is a fence when the return type is not `None`.

The LLVM backend stores an `int` in `i64` when the value fits. Overflow uses `llvm.*.with.overflow` and traps. A literal that does not fit is `PS3002` at emission time. Floor division is Python floor division. Division by zero and `INT64_MIN / -1` trap.

## Where a fence is born

`src/pyscriptc/lower.py` lowers the language: statements, expressions, classes, calls, and the codec modules (`re`, `json`, `urllib.parse`, `base64`, `hashlib`, `struct`). `src/pyscriptc/restlower.py` lowers the rest of the queued standard library and the builtin helpers (`abs`, `min`, `sorted`, `open`, and the rest). `src/pyscriptc/stdlib_queue.py` is the closed import table. A module outside that table is `PS2002` at the import. `src/pyscriptc/builtins.py` is the method table for `str` and `bytes`.

The lowered standard library is a closed surface inside each module, not the whole module. `functools.reduce` lowers. `functools.partial` does not. The generated list is [`.agents/docs/support/supported.md`](.agents/docs/support/supported.md), and the user-facing summary is the [support model](docs/guide/support-model.md). The file map for changing it is [`.agents/docs/map.md`](.agents/docs/map.md). The language order after this queue is [`.agents/docs/roadmap.md`](.agents/docs/roadmap.md).

## Native libraries

A third-party `.so` from a wheel exports `PyInit_<module>`. That symbol builds a module of Python objects. The small runtime cannot call it. Two shapes work:

- **C ABI.** The library exports ordinary functions (`mix_crc32`, or the same symbols from a Rust `cdylib`). `ps_mix.c` loads that file with `dlopen`. The binary does not link libpython. This is what "link the native dependency" means.
- **Extension module.** `ps_island_mix_crc32` asks embedded CPython to `import` the extension, the same way `subprocess.check_output` already runs on the island. The binary links libpython. This is the only path for cryptography, numpy, pydantic-core, and the other `PyInit_*` wheels.

`import nativeprobe` lowers only through the benchmark's FFI manifest: `pyscriptc compile demo.py --ffi benchmarks/native/nativeprobe.toml` (issue #218). It is the probe behind [`benchmarks/native/`](benchmarks/native/README.md), not a supported module. A compile without the manifest still refuses it with `PS2002`.

## The emitter golden corpus

The LLVM emitter is a single large module, so it is tested per IR node kind rather than only through a whole compile. `src/pyscriptc/emitter.py` is the seam: `emit_ir_text` validates one IR module the way a compile does and renders it to LLVM text in memory, with no file, no subprocess and no toolchain.

`tests/fixtures/backend/llvm/` holds one fixture pair per node kind, plus a fixture per emitter dispatch branch (const type, binary op family, compare family, callee kind, try and raise shape): `<name>.ir.json` is the IR module the emitter sees, `<name>.ll` is the byte-exact text it must produce. `tests/fixtures/backend/manifest.json` records which IR node kind and which dispatch branch each fixture covers, and `tests/test_emitter_goldens.py` fails with the missing kinds or branches listed if the corpus stops covering the emitter's dispatch surface. The determinism test re-runs the generator in a scratch directory and asserts the corpus rebuilds byte-identically, so a golden diff is always a reviewed emitter change, never hash-order noise.

After an intentional emitter change, re-bless the corpus and review the diff:

```bash
uv run pytest tests/test_emitter_goldens.py --update-goldens
```

The fixture layout is backend-neutral: the IR input lives beside the LLVM expectation, so a second backend adds its own expectations beside the same inputs. The specification both emitters share, one IR and one runtime ABI, is the backend contract in [`.agents/docs/design/backend-contract.md`](.agents/docs/design/backend-contract.md).

## What this is for

The distribution story is one command:

```bash
uv run pyscriptc compile examples/access_log.py --run --call admit --args '["{\"method\":\"GET\",\"path\":\"/api/health\",\"status\":\"200\"}"]' -o /tmp/admit
```

The binary prints the return value. There is no `print` in the subset. Scripts whose work is integer cents, a JSON record, a date, a URL, a digest, or a path label fit. Keywords, stored defaults, same-type `and`/`or`, Latin-1 strings, step-1 slices, plain f-strings, `with open(...) as f`, and tuples of two or more fields lower. Tuple equality stays a fence. A directory compile resolves relative imports of functions, module-level constants, and classes the defining module emits, including a subclass of an exported class. `__main__.py` is skipped; the compile says it is the `python -m` script, not an importable module. A class or function that does not lower reports that definition's fence ahead of "is not exported". A module-level `if sys.version_info` compared with a tuple of ints keeps the taken branch. `global` of a module `None` slot, and `as_file(...).__enter__`, lower without libpython. A class may have one base. `@property`, `@staticmethod`, `@classmethod`, a flat `@dataclass`, and an integer `enum` lower. `super()`, multiple inheritance, and any other decorator stay fences. That boundary is written down in [`benchmarks/adoption/README.md`](benchmarks/adoption/README.md).
