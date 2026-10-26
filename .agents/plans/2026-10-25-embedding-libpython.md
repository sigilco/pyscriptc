# Embedding libpython, pluggable island engines, capability reports, island performance

Status: assessment plus an experimental proof of concept (Refs #428). This file replaces the earlier draft on `work/embedpy`, which was thin, partly wrong and contained injected promotional text; every claim below was either run on this machine (macOS 26.6 arm64, CPython 3.14.9, zig 0.16.0, 2026-10-25) or is marked "from docs" or "not run".

Related: #157 (island as per-import policy, closed), #553 (requests pilot, closed), #606 (island ignored the user environment, closed), #612 (per-definition island fallback, closed), #428 (epic: island is the floor, native is the ceiling), `.agents/plans/2026-10-25-typed-subset-positioning.md`, `.agents/plans/2026-10-21-pypi-support-design.md`, `.agents/docs/benchmarks/cross-compile-assessment.md`, `docs/getting-started/install.md`, `docs/reference/config.md`.

## Verdict

Not now as a product feature; yes to keeping the seam and the manifest idea alive as cheap, default-off scaffolding.

- Embedding works. A relocatable bundle (island binary, libpython, trimmed stdlib zip) ran from an empty environment with the build tree hidden, and a statically linked build produced a single 6.4 MB executable plus a 250 KB stdlib zip. Details in section 2.
- It is a distribution feature for people shipping one binary that contains islands. Nothing in milestones 4 to 6 asks for it, the island is the floor and not the product (positioning plan), and every platform beyond macOS and glibc Linux needs its own libpython story and CI.
- The honest read on ROI: the expensive part is not the link line (a dozen lines, done), it is per-platform CI, security patch cadence for a bundled interpreter, and the stdlib/site-packages bundling policy. None of that is justified before a user asks. You may be fumbling if this gets pulled into a release before someone needs a relocatable island binary.
- Pluggable engines are a design option, not a plan. Only CPython can be the island engine without rewriting `ps_island.c`; PyPy is the one realistic second engine (section 3).
- Performance: build flags matter (PGO+LTO is 16 percent faster than a plain build on a small benchmark), the experimental JIT and free-threading were slower here, and "specializing CPython for the island subset" has no measured upside worth a project (section 5).

Smallest first step (when someone asks): ship section 2's seam as a documented, default-off option and put `scripts/embed_poc.py` behind a flag after a Linux run. Until then the POC stays experimental on this branch. Kill criterion: no request for a relocatable island binary by the v0.9 milestone, or the boxed ledger drop flag stays raised, then delete the seam (it lives in `boxed.py` plus 12 marked lines in `_driver.py`).

Policy note for the reviewer: `uv run python benchmarks/compat2/boxed_cost.py --window-commits 10` reports `drop_flag: true` on this branch before any of my commits (windows: code 4.3 percent, 3.7 percent, 1.4 percent; churn 25.7 percent, 30.3 percent, 0 percent; bug share 0 percent, 33.3 percent, 0 percent). `AGENTS.md` says the flag is false and that raising it means stop adding boxed features and ask. The POC adds about 40 boxed lines because the owner asked for it; the flag state needs a decision regardless of this PR.

## 1. How the output finds libpython today (verified)

- `src/pyscriptc/emit/llvm/_driver.py` `link_binary`: when the LLVM text contains `ps_island_`, it takes the include dir, `LIBDIR` and `VERSION` from `sysconfig` of the interpreter running pyscriptc and appends `-L{LIBDIR} -lpython{VERSION} -Wl,-rpath,{LIBDIR}` and `-I {include}`. When absent, `ps_island.c` is dropped from the link. With `--target` it raises "island fallback embeds the host CPython; cross-compiling it needs a cross-built libpython (explain-only)". The wasm targets refuse `ps_island_` outright (same function).
- Dynamic link, no dlopen. Built with `--island` against a uv-managed python-build-standalone (PBS) 3.14.9, `otool -L` shows the dylib by absolute path (`.../cpython-3.14.9-macos-aarch64-none/lib/libpython3.14.dylib`) and `otool -l` shows an `LC_RPATH` to the same `lib` directory. The binary is therefore tied to the compiler's interpreter install, and moving or deleting it breaks the binary (dyld "Library not loaded").
- Init: `src/pyscriptc/runtime/ps_island.c` calls `Py_InitializeEx(0)` once through `pthread_once` on the first island call, imports `site`, line-buffers stdout (no `Py_Finalize` ever runs), then extends `sys.path` from `VIRTUAL_ENV` (`lib/pythonX.Y/site-packages`), `PYTHONPATH` (via `site.addsitedir`) and `PYSC_PROGRAM_DIR`, falling back to the directory of `argv[0]`. `pyscriptc run` sets `PYSC_PROGRAM_DIR` and `VIRTUAL_ENV` in `_run_environment` (`src/pyscriptc/cli.py`).
- Run-time knobs that already work: `PYTHONHOME` and the other variables CPython's own path configuration reads, `PYTHONPATH`, `VIRTUAL_ENV`, `PYSC_PROGRAM_DIR`. Build-time knobs: none. There is no flag or config key to choose the libpython.
- Failure modes measured: libpython removed from a bundle gives `dyld: Library not loaded: @rpath/libpython3.14.dylib`; stdlib missing gives CPython's "Could not find platform independent libraries" and a path-configuration dump, then a fatal error. A different minor version was not run; the link is version-specific (`-lpython3.14`) and the headers are compiled in, so a binary built against 3.14 cannot adopt another minor without a rebuild (reasoned from the link line, not run).
- Docs today: `docs/getting-started/install.md` says island and fallback programs on any cross target are not supported because they embed the host libpython; `docs/reference/config.md` lists the `island` lane as "the binary requires libpython".

## 2. POC: island binary with a bundled or static CPython (experimental)

Code: `scripts/embed_poc.py`, the env seam `PYSC_EXPERIMENTAL_EMBED_PREFIX` / `PYSC_EXPERIMENTAL_EMBED_LINK=static` read by `embed_link_flags` in `src/pyscriptc/boxed.py` and called from `_driver.py` inside `boxed:begin`/`boxed:end` markers, tests in `tests/test_embed_poc.py`. With the variables unset nothing changes (a test pins that the default link line still has `-lpython3.14` and no relative rpath).

Program: an untyped `fractions` import routed to the island (`[imports] fractions = "island"`), printing `11/12`.

| Variant | Executable | Shipped alongside | Runs from empty env, build tree hidden |
|---|---|---|---|
| Default (host link) | 0.63 MB | nothing, needs the compiler's interpreter | no |
| Shared bundle, PBS 3.14.9 pgo+lto `install/` | 0.61 MB | `libpython3.14.dylib` 18.6 MB, `python314.zip` 0.25 MB (30 modules) | yes |
| Static, source build `--disable-shared --enable-optimizations`, `math` made builtin | 6.4 MB (no PGO build) | `python314.zip` 0.25 MB | yes, one binary plus one zip |
| Static, same with PGO, `math` shared | 7.7 MB | zip plus `math.cpython-314-darwin.so` 0.1 MB | yes |

What stdlib bundling needed:

- The import closure by `modulefinder` is 322 modules (3.2 MB zip), because lazy imports in `os`/`site` pull in most of the stdlib. Running the binary once with `PYTHONPROFILEIMPORTTIME=1` and keeping what was imported gives 30 modules, 0.25 MB. That trim is unsafe for programs whose imports depend on input; a production version needs the static closure plus an explicit allowlist.
- The zip must be stored (uncompressed) when `zlib` is a shared extension, as in a default source build: zipimport otherwise fails with "can't decompress data; zlib not available" at `encodings`. PBS builds `zlib` into the library, but storing is also faster to open.
- `lib/pythonX.Y/lib-dynload/` must exist (even empty) or getpath prints "Could not find platform dependent libraries".
- In a default `--disable-shared` source build, extension modules such as `math` are shared objects; the fractions program failed with `No module named 'math'` until `math` was linked in via `Modules/Setup.stdlib` (`*static*`) or its `.so` was shipped. Single-file output therefore needs a curated builtin module list.
- Not done: site-packages for PyPI dependencies (the real island use case, `requests` etc.), Linux, Windows, signing/notarization.

Findings that cost time:

- The PBS `pgo+lto-full` archive's `libpython3.14.a` (76 MB) is LLVM bitcode (producer LLVM 23.1.3). Apple ld (LLVM 21) fails with "Unknown attribute kind (105)" and zig 0.16 fails with "unknown cpu architecture: 0". Static linking PBS on macOS needs a matching LLVM or a non-LTO archive; the practical route here was building CPython from source. `libpython3.14.a` is absent from `install_only` archives, only the `full` ones ship it. PBS metadata (`PYTHON.json`) says `libpython_link_mode: shared` for that archive and lists the system libs to link.
- `zig cc` turns every `-L` directory into an absolute `LC_RPATH`. My first bundle "worked" only because the build tree still existed; hiding it exposed that. The seam now passes the libpython file by path, leaving only `@executable_path/../lib`.
- The first version of the script dropped the extension-module check onto the host interpreter's `sys.builtin_module_names`, which differs from the target's; matching against the target's `lib-dynload` by name fixed it.

Startup (quiet machine, median of 40 process launches; later reruns were disturbed by load average 10 to 12 from other sessions and are not quoted):

| Process | Median |
|---|---|
| Native program, no island | 3.8 ms |
| Island, host link (uv PBS) | 28.1 ms |
| Island, shared bundle (PBS) | 32.4 ms |
| Island, static source build with PGO | 27.7 ms |
| `python3.14 -S -c pass` (PBS) / static source build | 18.9 ms / 16.3 ms |
| `python3.14 fractions.py`, PBS / static | 29.7 ms / 28.4 ms |

An island costs about 24 ms over a native binary, of which about 16 ms is interpreter initialization, about 3 ms `site`, and the rest the imports. Static versus shared startup is within noise.

### Per-target distribution (what we can tell the user to bring)

| Target | Shared libpython | Static libpython | Source of truth |
|---|---|---|---|
| macOS arm64/x86_64 | yes (PBS, python.org, Homebrew) | source build works (measured); PBS archive is LTO bitcode | measured, PBS asset list |
| Linux glibc | yes (PBS `pgo+lto`, distro) | PBS `full` archives exist; not run | PBS asset list |
| Linux musl | PBS musl builds exist | PBS publishes `lto+static` and `noopt+static` musl variants; not run | PBS asset list (names only) |
| Windows x86_64/arm64 | python.org "embeddable package" (`python3.dll`, `python3NN.dll`, stdlib `.pyc` zip, `._pth` file, no pip, no site by default); PBS has msvc builds | not assessed | docs.python.org Windows page; our compiler does not emit native Windows programs yet (install.md) |
| iOS | CPython tier 3, dynamic `libpython3.x.dylib` in a Framework only, static `.a` unsupported, no subprocess | no | PEP 730 and PEP 11 |
| Android | CPython tier 3 (arm64-v8a, x86_64), shared `libpython3.x.so` loaded with dlopen, static unsupported | no | PEP 738 and PEP 11 |
| wasm32-wasi | CPython tier 2 since 3.13 (no shared libpython) | the interpreter is built as a single wasm module | PEP 11; our wasm targets refuse the island |
| wasm32-emscripten | tier 3 | n/a | PEP 11 |

PBS publishes no iOS or Android builds (checked in the 20261021 release asset list), so mobile means python.org/BeeWare artifacts and a cross-compile story that does not exist yet (`cross-compile-assessment.md`). The single-binary requirement therefore holds only for desktop/server static builds; mobile frameworks are dynamic by rule.

Licensing: the PBS archive ships a `licenses/` directory (CPython, expat, libffi, mpdecimal, ncurses, OpenSSL 3, SQLite, tcl, zlib, zstd, others). Redistributing a bundled interpreter means shipping those texts with the binary; the PSF license itself is permissive. This is an inventory, not legal advice.

### Options

| Option | What | Cost | Verdict |
|---|---|---|---|
| A document-only | users bring libpython; expose a path flag/config and document env vars | small; the seam above is most of it | do when asked |
| B `--embed-python` | compiler links a PBS or user libpython and writes the bundle | per-platform CI, stdlib/site-packages policy, patch cadence | not now |
| C separate package (`pyscriptc-embed`) | per-platform runtimes outside core | release engineering for N wheels, security updates | only if B has users |
| D no embedding | islands stay desktop/server with a system CPython | nothing | current state |

Extension modules and ABI: a bundle must carry extensions built for the same libpython; abi3 (limited API) wheels survive minor upgrades, version-specific wheels do not; free-threaded builds use a different ABI (tag `t`, `Py_GIL_DISABLED`, PEP 703) and need rebuilt extensions. A bundle should therefore pin the interpreter and resolve wheels for its tag at build time (ties into the PyPI design in `2026-10-21-pypi-support-design.md`).

## 3. Pluggable island engine (design, partly measured)

Contract today: `ps_island.c` is a CPython C-API client (1,367 lines, 70 distinct `Py*` names) compiled against the engine's `Python.h`. Arguments and results cross as scalar, str, bytes and list values only, so the island ABI is value marshalling plus a handful of handle operations, not object sharing.

Measured against the engines installed by `uv python install pypy3.11 graalpy`:

| Engine | Embeddable as a library? | Fit with `ps_island.c` |
|---|---|---|
| CPython | yes, `libpython` | native fit |
| PyPy 3.11.17 (PyPy 8.0.1) | yes: `libpypy3.11-c.dylib` (33 MB) exports `rpython_startup_code`, `pypy_setup_home`, `pypy_init_threads`, `pypy_execute_source`; a 20-line C program started it and ran `fractions` in 56 ms wall including process start. Docs call this interface legacy and recommend cffi embedding | the library also exports 806 cpyext functions under a `PyPy` prefix (`PyPyDict_New`, `PyPyGILState_Ensure`, ...); `Python.h` from PyPy renames them. Compiling `ps_island.c` against PyPy headers fails at exactly two sites: `Py_InitializeEx` (no `PyPy_Initialize*` exists, startup is the `rpython_startup_code` + `pypy_setup_home` pair) and `PyException_GetArgs`. Not linked or run |
| GraalPy 3.13.14 (25.x) | no C embedding API found: the 220 MB `libpythonvm.dylib` is a Truffle/native-image library with isolate entry stubs; embedding is Java Polyglot (`GraalPyResources.createContext()`) or a standalone native executable (from docs) | `ps_island.c` compiles against GraalPy headers with zero errors, but `libpython-native.dylib` (1,170 `Py*` exports) is the extension-side API for code running inside the VM, so there is nothing to init from our process. C extension support is experimental and not ABI compatible with CPython wheels (docs) |
| MicroPython | yes in principle: the `ports/embed` port generates a set of `.c`/`.h` files to compile into a project (from its README; not run here) | no CPython C API; the island ABI would be reimplemented on `mp_*`. Language is a CPython subset, stdlib is a small set of modules, no PyPI packages |

Cost in the island ABI, in order: PyPy needs an init shim and a one-function change plus a link against `libpypy-c` (plausible, days, with the cpyext performance caveat); GraalPy would need a Java host process (not a C link), so it is a subprocess/RPC engine, not an embedded one; MicroPython needs a second implementation of `ps_island.c`. Any engine that cannot be linked can still serve as a subprocess engine over a JSON/length-prefixed pipe, which also gives crash isolation and removes the GIL and init-once constraints, at the cost of per-call latency (not measured) and no handles.

Proposed config seam (not implemented; the POC only has the env var):

```toml
[tool.pyscriptc.island]
engine = "cpython"          # default; or a path to an engine descriptor
link = "host"               # host | bundle | static
```

Minimal engine descriptor (TOML, owned by whoever supplies the engine):

```toml
name = "cpython"
version = "3.14.9"
abi = "capi"                # capi | cpyext | rpc
include = "include/python3.14"
libpath = "lib/libpython3.14.dylib"
init = "Py_InitializeEx"    # entry used by the runtime shim: Py_InitializeEx | pypy_setup_home | rpc
stdlib_manifest = "capabilities.json"
features = ["gil"]          # or ["free-threaded"], ["jit"], ...
```

`abi = "capi"` selects `ps_island.c` as is; `cpyext` selects it with a shim translation unit; `rpc` selects a subprocess runner. Anything else is refused with a message naming the engine.

## 4. Capability reporting (prototype, measured on three real engines)

Problem: today `pyscriptc imports` and `assess` classify imports against the compiler's own tables and the host `sys.stdlib_module_names` (`src/pyscriptc/imports.py`, `assess.py`); nothing says "this module does not exist on the engine you chose".

Prototype: `scripts/engine_manifest.py`, two subcommands.

- `probe` runs inside the engine and writes a JSON manifest: engine name, version, language level, platform, free-threaded flag; which names of a reference stdlib list (the compiler target's 3.14 `sys.stdlib_module_names`, 297 names) resolve (`sys.builtin_module_names` plus `importlib.util.find_spec`, no imports executed); and a set of language feature probes (`compile()` of one snippet per feature, `exec` for PEP 649).
- `check MANIFEST PROGRAM` runs on the host and prints one line per import the engine lacks, exit code 1.

Results (reference list = CPython 3.14 names):

| Engine | Language level | Stdlib names present of 297 | Missing language features |
|---|---|---|---|
| CPython 3.14.9 (PBS) | 3.14 | 289 (missing only Windows modules and `_gdbm`) | none |
| PyPy 3.11.17 | 3.11 | 254 | PEP 649 deferred annotations, PEP 695 type parameters, PEP 750 t-strings, PEP 758 bracketless except |
| GraalPy 3.13.14 | 3.13 | 262 | PEP 649, PEP 750, PEP 758 |

Public modules missing on both PyPy and GraalPy include `annotationlib` and `compression` (new in 3.14), which is what a user needs reported; the many private `_xxx` names that show as missing are accelerator modules with pure-Python fallbacks and should be filtered by the consumer. The demo `import annotationlib; import compression.zstd` against the PyPy manifest prints `p.py:2: annotationlib is not available on pypy 3.11.17 (stdlib module missing)`.

Design to adopt (not built):

- Manifest schema v1 as in the prototype, plus `pep_support` derived from the feature probes and `stdlib.private_missing` split from `stdlib.missing`.
- Sources, in order: the engine's own `probe` output (authoritative, and third parties can run it); PBS `PYTHON.json` (`python_stdlib_test_packages`, extension list, `python_extension_module_loading`) for CPython builds; `sysconfig` for build flags. We ship the CPython manifest generated at release time; other engines supply their own file via the descriptor's `stdlib_manifest`.
- Consumers: `pyscriptc imports` and `assess` add an `engine` column when an island import is not available (`island: unsupported on pypy 3.11.17`), the compile step refuses with a coded diagnostic, language-feature probes gate syntax in island-bound definitions.
- Limits found: `find_spec` proves a module exists, not that it works (a C extension can exist and fail to load); third-party packages need a separate per-engine wheel/ABI check; a manifest generated on one OS describes that OS only.
- One portability trap: running `ruff format` with a 3.14 target rewrote `except (A, B):` to the PEP 758 bracketless form in the probe script, which is a syntax error on PyPy 3.11 and GraalPy 3.13. Probe code must stay in the lowest dialect and avoid `except A, B`.

## 5. Performance of island CPython builds

Benchmark: `fib(27)`, an n-body loop (60,000 steps) and a dict/str word count, best of 5 in-process runs, three repetitions each, same machine, runs quoted from a window where the load average was about 5. All are 3.14.9 unless stated.

| Build | Time (s) | Versus plain |
|---|---|---|
| Source build, no PGO, no LTO (`--disable-shared`) | 0.523 | baseline |
| Source build, `--enable-optimizations` (PGO) | 0.468 | 10.5 percent faster |
| PBS pgo+lto (tail-call interpreter, JIT compiled in but off) | 0.437 | 16.4 percent faster |
| PBS pgo+lto with `PYTHON_JIT=1` | 0.49 | 12 percent slower than JIT off |
| PBS free-threaded pgo+lto | 0.51 (0.59 to 0.74 in disturbed runs) | 17 percent slower than the same flags with the GIL |
| PBS free-threaded with `PYTHON_GIL=1` | 0.51 to 0.77 (noisy) | no gain from re-enabling the GIL |

Reading:

- PGO, LTO and the tail-call interpreter are worth about 16 percent for free if the island ships a PBS-style build; this is the only lever with a clear payoff, and the PBS binary already has it. The adaptive specializing interpreter is on by default in 3.11+ and needs no choice.
- The copy-and-patch JIT was slower on this workload (PBS builds it as `yes-off`, so the user opts in with `PYTHON_JIT=1`); not recommended without a workload that shows a win.
- Free-threading is slower single-threaded here (PEP 703 quotes 5 to 8 percent overhead; this run saw more, with noise). An island that is called from native threads gets nothing from it because calls are serialized by `PyGILState_Ensure` today, and it changes the extension ABI. Not recommended.
- Specializing for the island's language subset: the interpreter is already specialized at run time, the hot cost is the interpreter loop, not stdlib size. What a frozen, trimmed stdlib buys is size (31 MB of stdlib to 0.25 MB for this program) and a little startup: `import fractions` costs 8.7 ms of which unmarshalling the `.pyc` files is 1.6 ms, so freezing saves at most about 2 ms of a 28 ms process, while module bodies (enum, re) dominate. CPython 3.14 has no startup snapshot that I found or tried. Expected gain from subset specialization: under 10 percent of startup, roughly zero on steady-state throughput. Kill criterion for any spike: a custom frozen stdlib that does not bring the island process below 20 ms (from 28) is dropped.
- Not measured: the benchmark ran as plain scripts under each interpreter, not through the island marshalling path; per-call island overhead is a separate measurement.

## 6. Corrections to the earlier draft

Removed because they were wrong, unverifiable or injected: a Windows DLL claim that was provider-injected advertising, the "8 to 15 MB" static size (measured 6.4 to 7.7 MB executable, 19.6 MB for the shared bundle), "divergence 31" for wasm (not found; the actual source is `_driver.py` and `install.md`), the recommendation to add `--libpython-path`/`libpython_path` as the first step (the seam above is smaller and testable), and the unqualified statements about iOS "Apple Python" and Android "termux". Several rows it listed (Linux musl static, Windows static) remain not run.

## 7. Reproduce

- PBS archive: `gh release download 20261021 -R astral-sh/python-build-standalone -p 'cpython-3.14.9+20261021-aarch64-apple-darwin-pgo+lto-full.tar.zst'`, extract, then `PYSC_EMBED_POC_PREFIX=<dir>/python/install uv run pytest tests/test_embed_poc.py -k bundle`.
- Shared bundle: `uv run python scripts/embed_poc.py --prefix <dir>/python/install --trim-by-run prog.py out/`.
- Static: build CPython 3.14.9 with `--disable-shared --enable-optimizations`, then `PYSC_EXPERIMENTAL_EMBED_LINK=static uv run python scripts/embed_poc.py --prefix <install> --trim-by-run prog.py out/`.
- Manifests: `python scripts/engine_manifest.py refs > ref.json`, then `ENGINE_PYTHON scripts/engine_manifest.py probe --reference ref.json > manifest.json`, then `python scripts/engine_manifest.py check manifest.json prog.py`.
