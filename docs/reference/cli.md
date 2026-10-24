---
title: CLI
description: Every pyscriptc subcommand and flag.
---

# CLI

```sh
pyscriptc <command> [options]
```

Global flag: `-v` / `--verbose` enables DEBUG audit records on any subcommand (see [debugging and logs](../guide/debugging/)).

## `compile`

Lower a source file or package directory.

```sh
pyscriptc compile FILE [options]
```

| Flag | Effect |
| ---- | ------ |
| `--emit ir\|llvm\|asm\|obj\|bin\|lib` | Stop after JSON IR, textual LLVM, assembly (`.s`), an object (`.o`), a linked binary, or a static library (default `llvm`) |
| `--out-dir DIR` | Write artifacts to `DIR/<source name>` instead of beside the source; use for directory compiles |
| `-o PATH` | Output path; defaults to the source stem |
| `--call FUNC` | Emit a `main` that calls `FUNC` and prints its return |
| `--args '[...]'` | Arguments for `--call`, a Python list literal |
| `--run` | Emit a binary and run it immediately |
| `--program` | Treat a directory as a program (`python -m pkg` parity) even when it has an `__init__.py` |
| `--driver clang\|zig` | C toolchain driver; `zig` enables `--target` (default `clang`, env `PYSC_CC`) |
| `--linker clang\|zig` | Driver for the final link step only (env `PYSC_LINKER`, defaults to `--driver`) |
| `--target TRIPLE` | Cross-compile triple for `zig cc`, e.g. `aarch64-linux-gnu.2.39` (env `PYSC_TARGET`) |
| `--emit lib` | Build a static library instead of a binary; needs a mobile `--target` (`aarch64-apple-ios`, `aarch64-apple-ios-simulator`, `aarch64-linux-android`); see below |
| `--sanitize LIST` | Comma list of sanitizers for the C runtime and emitted code, e.g. `address,undefined` (env `PYSC_SANITIZE`); needs `--emit bin` |
| `--skip-check` | Skip the type-checker preflight |
| `--ffi MANIFEST` | Bind a typed extern manifest (TOML) and link its C sources; repeatable. Also read from the `ffi` key of the config or PEP 723 metadata |
| `--checker pyrefly\|mypy\|pyright` | Preflight checker (default `pyrefly`; mypy and pyright are deprecated) |
| `--bridge mypy` | Feed the checker's inferred-type map into lowering so unannotated code stops fencing; needs mypy installed |
| `--timings` | Print the per-stage wall-time table on its own |
| `--debug[=WHAT]` | Print a stage table plus the per-file capability split; `WHAT` is a comma list of `stages,fences,cc,paths,all` |
| `--debug-dir DIR` | Write `ir.json`, `out.ll`, `cc.sh`, `summary.json` to `DIR` |

Environment defaults: `PYSC_CC`, `PYSC_LINKER` and `PYSC_TARGET` fill `--driver`, `--linker` and `--target` when the flag is absent. An explicit flag always wins.

### Static libraries for mobile (`--emit lib`)

```sh
pyscriptc compile FILE --emit lib --target aarch64-apple-ios-simulator -o LIBDIR
```

`-o` names a directory receiving `libpyscriptc.a`, `pyscriptc.h` and `module.modulemap`. The build compiles through clang with the SDK sysroot (`xcrun --show-sdk-path` on Apple, the NDK at `PYSC_ANDROID_NDK` on Android, API level `PYSC_ANDROID_API`, default 24). `--driver zig` is refused: zig bundles neither bionic nor the Apple SDKs.

The exported surface is exactly `pyscriptc_init` plus `pyscriptc_main`; everything else is hidden. Link the archive, call `pyscriptc_init()` once, then `pyscriptc_main()` per run.

Android consumers must link the archive into a shared object carrying `-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384` (the Google Play 16 KB rule for Android 15+). The archive itself has no program headers, so the flags belong to the shared-object link.

`--emit bin` and `--run` refuse mobile targets with `PS4101` (see `pyscriptc explain PS4101`).

## `run`

```sh
pyscriptc run FILE [-- args]
```

Compile (cached by content hash under `~/.cache/pyscriptc/bin`) and run in one step. Arguments after `--` go to the program as `sys.argv`. Flags mirror `compile` (`--call`, `--args`, `--program`, `--driver`, `--target`, `--linker`, `--sanitize`, `--skip-check`, `--checker`), plus `--no-cache` to always recompile (env `PYSC_NO_RUN_CACHE=1`) and `--ffi MANIFEST` (repeatable) for extern manifests.

## `verify`

```sh
pyscriptc verify FILE [--call FN] [--args '[...]'] [--input FILE] [--json] [-- args]
```

Compile `FILE`, run it under the pinned CPython 3.14 oracle and as the linked binary with the same argv and stdin, and report the first observable difference. The receipt covers stdout bytes, stderr bytes and the exit code, plus the printed return value when `--call` is used.

| Flag | Effect |
| ---- | ------ |
| `--call FUNC` | Call `FUNC` instead of the program entry; the return value is printed by both sides and compared as stdout |
| `--args '[...]'` | Arguments for `--call`, a Python list literal |
| `--input FILE` | Feed `FILE`'s bytes on stdin to both runs; without it stdin is empty, so `input()` sees EOF |
| `--json` | Print the receipt as JSON (`schemaVersion` 1) instead of text |
| `--timeout S` | Seconds each run may take before it counts as a divergence (default 60) |
| `--no-cache` | Always recompile instead of reusing the `run` binary cache (env `PYSC_NO_RUN_CACHE=1`) |
| `--skip-check`, `--checker`, `--driver`, `--linker`, `--target`, `--ffi` | As in `compile` |

Exit codes: 0 when every check matches, 1 on a divergence, 3 when the compiler fences the program, 4 when no CPython 3.14 oracle is available.

The oracle is resolved from `PYSC_ORACLE` (an explicit path, refused if it is not 3.14), `PYSC_ORACLE_DIR`, `python3.14` on PATH, `uv python find 3.14`, then the interpreter running pyscriptc. It is probed before use: verify never compares a binary against the wrong interpreter.

Verify is a differential smoke test, not a proof. It covers the paths the argv and stdin exercised in that run, and the corpus and the must-fence corpus remain the broad coverage. There is no byte-for-byte parity goal: the island is the floor and native is the ceiling (see the typed-subset positioning decision), so a divergence is evidence to inspect, not automatically a compiler bug.

`--call` compares the call, not the module body: the compiled main runs the global initializers and then the function, so the oracle side hides the output of the module's top-level statements. The function's own output and its printed return value are compared.

## `assess`

```sh
pyscriptc assess PATH [--json] [--bench] [--verify] [--input FILE] [--call FN] [--args '[...]'] [--timeout S]
```

Evaluate any Python script, library or app directory without crashing, whatever the fence count. Reports lowering status per file, fences grouped by root cause (reflection culprit, untyped import, unsupported construct) with the nearest typed idiom, per-import ladder status, overall native vs island vs blocked shares, and concrete `[tool.pyscriptc.imports]` policy lines for the island floor.

| Flag | Effect |
| ---- | ------ |
| `--json` | Print the report as JSON (`schemaVersion` 1) instead of text |
| `--bench` | For compilable entry points, time CPython against the native binary on user inputs and print the measured ratio with sample count (default 5 samples; without this flag, no speed numbers are printed) |
| `--verify` | Run the differential verification on compilable entry points and show the receipt inline |
| `--input FILE` | Feed `FILE` bytes on stdin to both runs |
| `--call FUNC` | Function to call on entry points instead of the program entry |
| `--args '[...]'` | Arguments for `--call`, a Python list literal |
| `--timeout S` | Seconds each run may take (default 60) |
| `--program` | Treat directory as a program |

### Example 1: Single script

```sh
pyscriptc assess script.py --bench --verify
```

On a single script, assess reports whether it lowers fully, partly, or is refused. If the script compiles natively and has a runnable entry point, `--bench` prints the measured speedup against CPython 3.14 and `--verify` displays the differential receipt inline. Untyped dependencies such as `requests` report the exact `requests = "island"` policy line.

### Example 2: Pure-Python library directory

```sh
pyscriptc assess mylib/
```

On a library directory, assess inspects every module and definition without requiring an entry point. Modules that do not lower report their root-cause fences and mark what stays blocked (such as dynamic attributes or union types) alongside the nearest typed idiom.

### Example 3: Application directory

```sh
pyscriptc assess myapp/ --bench
```

On an application directory containing `__main__.py` or `main.py`, assess evaluates the whole package tree. Compilable entry points are benchmarked against CPython, while unlowered imports and reflection culprits report the island routing configuration needed to run on the hybrid floor.

## `coverage`

```sh
pyscriptc coverage SOURCE [--json] [--fail-on blockers|any]
```

Report every fence in a file or a package tree, per-module counts and codes, useful for scoping a port. Needs no C toolchain. The text view carries a capability line: each definition is `native` (no fence), `fenced` (a fence that a rewrite clears) or `island` (the fence traces to runtime reflection and the definition routes to the island lane). The JSON has the same split under `capabilities.byFile` and `capabilities.totals`, with the fence codes and the reflection mechanisms per file. `--fail-on` exits 1 when root fences (`blockers`) or any fence (`any`) exist; the default always exits 0 unless the resolved config sets `fail-on`.

## `config`

```sh
pyscriptc config SOURCE [--json]
```

Show the resolved config for a source: the `[tool.pyscriptc]` or `pyscriptc.toml` table merged with the script's PEP 723 block, plus the resolved import lanes. See [Config](config.md).

## `explain`

```sh
pyscriptc explain PSxxxx
```

Print the description of a diagnostic code.

## `imports`

```sh
pyscriptc imports SOURCE [--json]
```

Classify every import of a file or package tree into a compat lane (standard library, native-backed, curated-replacement, pure Python). The default view is the capability ladder: `native`, `native: fences (PS20xx)` or `island (requires libpython)`. A reflection fence (`inspect.signature`, `get_type_hints`, `__annotations__`, metaclass synthesis, dynamic `getattr`) puts the import on the island rung, and the JSON `imports[].capability` records the routing, codes, mechanisms, files and the `libpython` requirement. Nothing reaches the network.

## `deps`

```sh
pyscriptc deps SOURCE [--json] [--frozen] [--upgrade] [--offline] [--no-download]
```

Resolve PyPI dependencies from a PEP 723 `# /// script` block or `pyproject.toml`, and cache pure-Python wheels. `SOURCE` is a `.py` script, a `pyproject.toml`, or a directory.

## `logs`

```sh
pyscriptc logs
```

Print the audit-log path and its last lines. This is the output to paste into an issue.

## `reduce`

Delta-debug a source down to a minimal reproducer of a fence or a crash.

```sh
pyscriptc reduce FILE (--predicate CMD | --fence PSxxxx | --crash) [--print] [--json]
```

| Flag | Effect |
| ---- | ------ |
| `--predicate CMD` | Shell command run on each candidate; exit 0 keeps the removal. The candidate path arrives in `$PSC_SOURCE` |
| `--fence PSxxxx` | Shorthand: lowering the candidate still reports a fence with that code |
| `--crash` | Shorthand: lowering the candidate still raises a non-fence error |
| `--timeout S` | Seconds per `--predicate` command (default 120) |
| `--steps N` | Cap on removal attempts (default 10000) |
| `--print` | Print the reduced source instead of writing `FILE.min.py` |
| `--json` | Print the removal log as JSON and write nothing |

The result is written to `FILE.min.py` beside the input; the input is never modified. Exit code is 0 when the final variant still satisfies the predicate.

## `doctor`

```sh
pyscriptc doctor [--json]
```

Report what the compiler would use: the type checker (argv, version, and how it was resolved), clang and zig paths and versions, the cache and log directories, the default target, and the install shape (`wheel`, `tool`, `binary`, `source`). Paste this when filing an issue.

## `telemetry`

```sh
pyscriptc telemetry status|enable|disable|reset-id
```

Manage anonymous usage metrics. See [telemetry](../project/telemetry.md).
