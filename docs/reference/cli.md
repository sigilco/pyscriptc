---
title: CLI
description: Every pyscriptc subcommand and flag.
---

# CLI

Install once (`uv tool install pyscriptc`) and every subcommand is `pyscriptc COMMAND [options]`.
`-v` / `--verbose` turns on DEBUG audit records for any subcommand; the log is what `pyscriptc logs` prints.
Exit codes and the stability contract are in [Stability and contracts](stability.md), the diagnostic codes in [Diagnostics](diagnostics.md).

<!-- gen:flags -->
### `compile`

```sh
pyscriptc compile SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--program` |  | treat the directory as a program (`python -m pkg` parity): `__main__.py` is the entry point even when the directory has an `__init__.py`, and its modules are namespaced under the directory name |
| `--emit EMIT` | `ir\|llvm\|asm\|obj\|bin\|lib\|binding` | output artifact: ir (JSON IR), llvm (textual LLVM), asm, obj, bin, lib or binding; default llvm (default `llvm`) |
| `--out-dir DIR` | `DIR` | write artifacts to DIR/<source name> instead of beside the source (useful for directory compiles) |
| `-o PATH` | `PATH` | output path; defaults to the source stem |
| `--ffi MANIFEST` | `MANIFEST` | bind a typed extern manifest (TOML) and link its C sources; repeatable. Also read from the `ffi` key of pyscriptc.toml / PEP 723 metadata |
| `--fallback TIER` | `cpython` | route a function pyscriptc cannot compile to embedded CPython (fallback mode); the only tier is cpython. Needs libpython and the packages on the host |
| `--island` |  | alias of --fallback=cpython: route every import outside the closed table and every function pyscriptc cannot compile to embedded CPython (fallback mode) |
| `--embed-python MODE` | `host\|bundle\|auto` | libpython embedded CPython links: host (the compiler interpreter), bundle (the pinned runtime pack, relocatable output) or auto (host when this interpreter has a shared libpython, bundle otherwise; the default). Also read from the `embed` key of [tool.pyscriptc.island] |
| `--embed-stdlib TRIM` | `safe\|minimal` | stdlib trim of a bundle: safe (the default; everything except test, idlelib, tkinter, turtledemo, ensurepip, pydoc_data, __phello__) or minimal (an opt-in frozen closure plus allowlist). Also read from the `stdlib` key of [tool.pyscriptc.island] |
| `--call FUNC` | `FUNC` | function the generated main calls |
| `--args ARGS` | `ARGS` | Python literal list of arguments for --call, parsed with ast.literal_eval (default `[]`) |
| `--run` |  | compile a binary and run it; arguments after `--` go to the binary |
| `--driver DRIVER` | `clang\|zig` | C toolchain driver; zig means `zig cc` (preferred, required for --target); env PYSC_CC, defaults to zig if found, else clang |
| `--target TRIPLE` | `TRIPLE` | cross-compilation triple for `zig cc`, e.g. aarch64-linux-gnu.2.39 (env PYSC_TARGET) |
| `--linker LINKER` | `clang\|zig` | driver for the final link step only; env PYSC_LINKER, defaults to --driver |
| `--sanitize LIST` | `LIST` | comma list of sanitizers for the C runtime and the emitted code, e.g. address,undefined (env PYSC_SANITIZE); needs a binary, so --emit bin only |
| `--debug-info` |  | emit DWARF debug metadata (source file and line per function and statement) so lldb/gdb can step in the Python source (env PYSC_DEBUG_INFO) |
| `--print PRINT_WHAT` | `diagnostics` | print JSON instead of compiling: diagnostics is the coverage report; exit 3 on a refusal |
| `--skip-check` |  | skip the type-checker preflight |
| `--checker CHECKER` | `pyrefly\|mypy\|pyright` | type-checker preflight; mypy and pyright are deprecated (default `pyrefly`) |
| `--bridge BRIDGE` | `mypy\|pyrefly` | feed the checker's inferred-type map to the compiler so unannotated code compiles; needs mypy installed, or pyrefly for --bridge pyrefly |
| `--debug[=WHAT]` | `WHAT` | print a compact stage table to stderr; WHAT is a comma list of stages,fences,cc,paths,all (also via PYSC_DEBUG) |
| `--debug-dir DIR` | `DIR` | write debug artifacts (ir.json, out.ll, cc.sh, summary.json) to DIR |
| `--timings` |  | print per-stage wall times (parse, check, lower, emit, cc/link) to stderr |

### `run`

```sh
pyscriptc run SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--program` |  | treat the directory as a program (see compile) |
| `--fallback TIER` | `cpython` | route a function pyscriptc cannot compile to embedded CPython (fallback mode); the only tier is cpython |
| `--island` |  | alias of --fallback=cpython |
| `--embed-python MODE` | `host\|bundle\|auto` | libpython embedded CPython links (see compile); the run cache keys on it |
| `--report[=FILE.json]` | `FILE.json` | print or write the executed plane map after the run (default: silent) |
| `--call FUNC` | `FUNC` | function the generated main calls |
| `--args ARGS` | `ARGS` | Python literal list of arguments for --call (default `[]`) |
| `--driver DRIVER` | `clang\|zig` | C toolchain driver (see compile) |
| `--target TRIPLE` | `TRIPLE` | cross-compilation triple for `zig cc` (see compile) |
| `--linker DRIVER` | `clang\|zig` | link driver (see compile) |
| `--sanitize LIST` | `LIST` | comma list of sanitizers for the C runtime and the emitted code (env PYSC_SANITIZE) |
| `--skip-check` |  | skip the type-checker preflight |
| `--checker CHECKER` | `pyrefly\|mypy\|pyright` | type-checker preflight (default `pyrefly`) |
| `--no-cache` |  | always recompile (env PYSC_NO_RUN_CACHE=1) |
| `--ffi MANIFEST` | `MANIFEST` | bind a typed extern manifest (TOML) and link its C sources; repeatable |

### `verify`

```sh
pyscriptc verify SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--call FUNC` | `FUNC` | function to call on both sides, not the program entry |
| `--args ARGS` | `ARGS` | Python literal args list for --call (default `[]`) |
| `--input FILE` | `FILE` | feed FILE's bytes on stdin to both runs (default: an empty stdin, so input() sees EOF) |
| `--json` |  | print the receipt as JSON instead of text |
| `--timeout S` | `S` | seconds each run may take before it is a divergence (default 60) |
| `--program` |  | treat the directory as a program (see compile) |
| `--driver DRIVER` | `clang\|zig` | C toolchain driver (see compile) |
| `--target TRIPLE` | `TRIPLE` | cross-compilation triple for `zig cc` (see compile) |
| `--linker DRIVER` | `clang\|zig` | link driver (see compile) |
| `--skip-check` |  | skip the type-checker preflight |
| `--checker CHECKER` | `pyrefly\|mypy\|pyright` | type-checker preflight (default `pyrefly`) |
| `--no-cache` |  | always recompile (env PYSC_NO_RUN_CACHE=1) |
| `--ffi MANIFEST` | `MANIFEST` | bind a typed extern manifest (TOML) and link its C sources; repeatable |

### `assess`

```sh
pyscriptc assess SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the assessment report as JSON |
| `--all` |  | print the full report without capping refusals (default top-3 per root cause) |
| `--bench` |  | time CPython against native binary on compilable entry points and print ratio |
| `--verify` |  | run differential verify on compilable entry points and show receipt inline |
| `--call FUNC` | `FUNC` | function to call on entry points, not the program entry |
| `--args ARGS` | `ARGS` | Python literal args list for --call (default `[]`) |
| `--input FILE` | `FILE` | feed FILE's bytes on stdin to verify and bench |
| `--timeout S` | `S` | seconds each run may take (default 60) |
| `--program` |  | treat the directory as a program |
| `--driver DRIVER` | `clang\|zig` | C toolchain driver (see compile) |
| `--target TRIPLE` | `TRIPLE` | cross-compilation triple for `zig cc` (see compile) |
| `--linker DRIVER` | `clang\|zig` | link driver (see compile) |
| `--skip-check` |  | skip the type-checker preflight |
| `--checker CHECKER` | `pyrefly\|mypy\|pyright` | type-checker preflight (default `pyrefly`) |
| `--no-cache` |  | always recompile (env PYSC_NO_RUN_CACHE=1) |
| `--ffi MANIFEST` | `MANIFEST` | bind a typed extern manifest (TOML) and link its C sources; repeatable |

### `coverage`

```sh
pyscriptc coverage SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the report as JSON |
| `--fail-on FAIL_ON` | `blockers\|any` | exit 1 when root refusals (blockers) or any refusal (any) exist; default always exits 0 |

### `config`

```sh
pyscriptc config SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the resolved config as JSON |

### `explain`

```sh
pyscriptc explain CODE [options]
```

### `imports`

```sh
pyscriptc imports SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the report as JSON |
| `--reach FILE` | `FILE` | append the embedded-CPython reach share to a JSON file (the compat ledger reads it) |

### `reduce`

```sh
pyscriptc reduce SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--predicate CMD` | `CMD` | shell command run on each candidate file; it exits 0 when the candidate still reproduces (the candidate path arrives in $PSC_SOURCE) |
| `--fence CODE` | `CODE` | shorthand: compiling still reports a refusal with this code |
| `--crash` |  | shorthand: compiling still raises a non-refusal error |
| `--timeout TIMEOUT` |  | seconds per --predicate command (default 120) |
| `--steps STEPS` |  | cap on removal attempts (default 10000) |
| `--print` |  | print the reduced source instead of writing FILE.min.py |
| `--json` |  | print the removal log as JSON |

### `build`

```sh
pyscriptc build DIRECTORY [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--script SCRIPT` |  | build only this console script; default every script |
| `--out-dir DIR` | `DIR` | write the binaries to DIR instead of <project>/build |
| `--driver DRIVER` | `clang\|zig` | C toolchain driver (see compile) |
| `--target TRIPLE` | `TRIPLE` | cross-compilation triple for `zig cc` (see compile) |
| `--linker DRIVER` | `clang\|zig` | link driver (see compile) |
| `--skip-check` |  | skip the type-checker preflight |
| `--checker CHECKER` | `pyrefly\|mypy\|pyright` | type-checker preflight (default `pyrefly`) |

### `logs`

```sh
pyscriptc logs [options]
```

### `telemetry`

```sh
pyscriptc telemetry ACTION [options]
```

### `doctor`

```sh
pyscriptc doctor [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the report as JSON |

### `deps`

```sh
pyscriptc deps SOURCE [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--json` |  | print the report as JSON |
| `--frozen` |  | use the existing lock, never resolve |
| `--upgrade` |  | re-resolve to the newest allowed versions |
| `--offline` |  | no network: uv offline, cached wheels only |
| `--no-download` |  | classify from wheel tags only |
| `--cache-dir DIR` | `DIR` | cache root (env PYSC_CACHE_DIR, default ~/.cache/pyscriptc) |

### `embed`

```sh
pyscriptc embed fetch [options]
```

#### `embed fetch`

```sh
pyscriptc embed fetch [options]
```
| Flag | Value | Effect |
| ---- | ----- | ------ |
| `--platform PLATFORM` | `PLATFORM` | pack platform (default: this machine) |
| `--force` |  | re-download even when the pack is cached |
<!-- /gen:flags -->

## Notes on `compile`

`--emit bin` and `--run` refuse mobile targets with `PS4101` (see `pyscriptc explain PS4101`).
When compilation stops on unsupported constructs, pyscriptc prints the root cause and counts the cascade (`and N more`) rather than printing every consequence.
Use `pyscriptc coverage FILE` to see every individual code.

`PYSC_CC`, `PYSC_LINKER` and `PYSC_TARGET` fill `--driver`, `--linker` and `--target` when the flag is absent; an explicit flag always wins.

## Notes on `verify`

Exit codes: 0 when every check matches, 1 on a divergence, 3 when the compiler refuses the program, 4 when no CPython 3.14 oracle is available.

The oracle is resolved from `PYSC_ORACLE` (an explicit path, refused if it is not 3.14), `PYSC_ORACLE_DIR`, `python3.14` on PATH, `uv python find 3.14`, then the interpreter running pyscriptc.
It is probed before use: verify never compares a binary against the wrong interpreter.

Verify is a differential smoke test, not a proof.
It covers the paths the argv and stdin exercised in that run.
Code either compiles to native code or runs through embedded CPython (fallback mode), so a divergence is evidence to inspect, not automatically a compiler bug.

`--call` compares the call, not the module body: the compiled main runs the global initializers and then the function, so the oracle side hides the output of the module's top-level statements.

## Notes on `assess`

On a single script, assess reports whether it compiles fully, partly, or is refused, and `--bench` prints the measured speedup against CPython when an entry point exists.
On a library directory it inspects every module without needing an entry point.
On an application directory (one with `__main__.py` or `main.py`) it evaluates the whole package tree and prints the `[tool.pyscriptc.imports]` lines that route the unsupported imports to embedded CPython.
See [Config](config.md) for what those lines go into.

## Notes on `coverage`

Needs no C toolchain.
The text view carries a capability line per definition: `native` (compiles), `fenced` (a rewrite clears it) or `island` (routes to embedded CPython).
The JSON has the same split under `capabilities.byFile` and `capabilities.totals`.

## Notes on `reduce`

The result is written to `FILE.min.py` beside the input; the input is never modified.
Exit code is 0 when the final variant still satisfies the predicate.

## Notes on `doctor`

`doctor` reports the type checker it found (argv, version, how it was resolved), clang and zig paths and versions, the cache and log directories, the default target, and the install shape (`wheel`, `tool`, `binary`, `source`).
Paste this when filing an issue.

## Notes on `embed`

`embed fetch` downloads the runtime pack recorded in the runtime lock, verifies its sha256, and unpacks it under `~/.cache/pyscriptc/runtime/`.
A `--embed-python=bundle` compile without a cached pack names this command before downloading.
`--force` re-downloads a cached pack; `--platform` fetches for another machine.
The pack itself, its pinned versions and the licenses that travel with it are on [Runtime pack](runtime-pack.md).
