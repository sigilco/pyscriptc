---
title: CLI
description: Every pyscriptc subcommand and flag.
---

# CLI

```sh
pyscriptc <command> [options]
```

Global flag: `-v` / `--verbose` enables DEBUG audit records on any subcommand (see [debugging and logs](/guide/debugging/)).

## `compile`

Lower a source file or package directory.

```sh
pyscriptc compile FILE [options]
```

| Flag | Effect |
| ---- | ------ |
| `--emit ir\|llvm\|bin` | Stop after JSON IR, textual LLVM, or a linked binary (default `llvm`) |
| `-o PATH` | Output path; defaults to the source stem |
| `--call FUNC` | Emit a `main` that calls `FUNC` and prints its return |
| `--args '[...]'` | Arguments for `--call`, a Python list literal |
| `--run` | Emit a binary and run it immediately |
| `--driver clang\|zig` | C toolchain driver; `zig` enables `--target` (default `clang`, env `PYSC_CC`) |
| `--linker clang\|zig` | Driver for the final link step only (env `PYSC_LINKER`, defaults to `--driver`) |
| `--target TRIPLE` | Cross-compile triple for `zig cc`, e.g. `aarch64-linux-gnu.2.39` (env `PYSC_TARGET`) |
| `--skip-check` | Skip the mypy preflight |
| `--checker mypy\|pyright` | Preflight checker (default `mypy`); `pyright` resolves via `PYRIGHT_BIN` then PATH |
| `--debug[=WHAT]` | Print a stage table; `WHAT` is a comma list of `stages,fences,cc,paths,all` |
| `--debug-dir DIR` | Write `ir.json`, `out.ll`, `cc.sh`, `summary.json` to `DIR` |

Environment defaults: `PYSC_CC`, `PYSC_LINKER` and `PYSC_TARGET` fill `--driver`, `--linker` and `--target` when the flag is absent. An explicit flag always wins.

## `coverage`

```sh
pyscriptc coverage SOURCE [--json]
```

Report every fence in a file or a package tree — per-module counts and codes, useful for scoping a port.

## `explain`

```sh
pyscriptc explain PSxxxx
```

Print the description of a diagnostic code.

## `logs`

```sh
pyscriptc logs
```

Print the audit-log path and its last ~50 lines. This is the output to paste into an issue.

## `doctor`

```sh
pyscriptc doctor [--json]
```

Report what the compiler would use: the type checker (argv, version, and how it was resolved: `env`, `interpreter`, `user-scheme`, `path`, or `-m-fallback`), clang and zig paths and versions, the cache and log directories, the default target, and the install shape (`wheel`, `tool`, `binary`, `source`). Paste this when filing an issue.

## `telemetry`

```sh
pyscriptc telemetry status|enable|disable
```

Manage anonymous usage metrics. See [telemetry and privacy](/guide/telemetry/).
