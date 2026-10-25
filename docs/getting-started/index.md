---
title: Quickstart
description: Install pyscriptc, compile a Python file, and run the native binary.
---

# Quickstart

<p align="center"><img src="/assets/logo-256.png" width="320" alt="pyscriptc"></p>

pyscriptc lowers the typed Python 3.14 subset to a native binary. Island fallback is the floor and native compilation is the ceiling; the compiler provides no shims or dynamic runtime emulation for untyped Python. Parsing is CPython's own `ast`; [pyrefly](https://pyrefly.org/) is a preflight checker. Anything the subset does not prove stops at a fence (`PSxxxx`), not a guess.

## Install

```sh
uv tool install pyscriptc
```

`pipx install pyscriptc` and `pip install pyscriptc` work the same way, and prebuilt binaries ship on the [releases page](https://github.com/sigilco/pyscriptc/releases).

Compiling to a binary needs a C toolchain on PATH: `clang`, or `zig` which also unlocks cross-compilation. `coverage`, `explain`, `--emit ir` and `--emit llvm` do not. If the driver is missing, `compile` stops before doing any work and points at `pyscriptc doctor`. The full toolchain setup, including per-platform install commands, is in [Install and toolchain](install.md). Testing an unreleased build is covered in [Trying the beta](beta.md).

## Compile and run

```sh
pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

That prints `285`, the same value CPython returns, from a native binary.

A script with a module-level body or an `if __name__ == "__main__":` guard needs no `--call` at all:

```sh
pyscriptc run hello.py
```

`pyscriptc run` compiles once, caches the binary under `~/.cache/pyscriptc/bin`, and runs it; arguments after `--` reach `sys.argv`.

## How a compile works

```sh
pyscriptc compile FILE --run --call FUNC --args '[...]' -o /tmp/name
```

- `FILE` is one Python file or a package directory. Imports resolve from the closed standard-library table.
- `--call` is the function the generated `main` calls. The binary prints its return value.
- `--args` is a Python list literal (`ast.literal_eval`). Elements can be `int`, `float`, `bool`, or `str`.
- The printed return is an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` (or `--run`) links a binary.

Every subcommand and flag is listed in the [CLI reference](../reference/cli.md).

## When it stops

| Exit | Cause |
| ---- | ----- |
| 0    | Compiled, plus the binary's own status if you used `--run` |
| 1    | A compiler fault (`SpikeError`) |
| 2    | The type-checker preflight (pyrefly) rejected the file |
| 3    | One or more fences (`PSxxxx`), see the printed table |
| 4    | A version or ABI mismatch |

A fence output names the construct and the code. `pyscriptc explain PS2003` prints what the code means. What lowers and what fences is the [support model](../guide/support-model.md).
