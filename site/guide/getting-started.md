---
title: Quickstart
description: Install pyscriptc, compile a Python file, and run the native binary.
---

# Quickstart

<p align="center"><img src="/assets/logo-256.png" width="320" alt="pyscriptc"></p>

pyscriptc lowers a typed Python subset to a native binary. Parsing is CPython's own `ast`; [pyrefly](https://pyrefly.org/) is a preflight checker. Anything the subset does not prove stops at a fence (`PSxxxx`), not a guess.

## Requirements

- CPython 3.14 or later to run the compiler (PyPI installs only; the prebuilt binary has no Python dependency). The parser is CPython 3.14's `ast`, and the emitted IR is stamped with the interpreter that lowered it.
- A C toolchain to link compiled programs
  - [`zig`](https://ziglang.org/download/) preferred (unlocks cross-compilation)
  - [`clang`](https://clang.llvm.org/get_started.html)

The preflight checker ships inside the installed package. No extra setup.

### Installing clang or zig

| Platform           | Command                                                                |
| ------------------ | ---------------------------------------------------------------------- |
| macOS              | `xcode-select --install` (or `brew install llvm` / `brew install zig`) |
| Debian / Ubuntu    | `sudo apt install clang` (or `zig` on newer releases)                  |
| Fedora             | `sudo dnf install clang` (or `zig`)                                    |
| Arch               | `sudo pacman -S clang` (or `zig`)                                      |
| Any Linux or macOS | `mise use -g zig` or `brew install zig`                                |
| Windows            | `winget install zig.zig` (or `scoop install zig`)                      |

## Install

::: tabs

== tab "uv"

```sh
uv tool install pyscriptc
```

== tab "pipx"

```sh
pipx install pyscriptc
```

== tab "pip"

```sh
pip install pyscriptc
```

== tab "binary"

```sh
mise use -g github:sigilco/pyscriptc
```

Prebuilt binaries for Linux, macOS, and an experimental Windows build ship on the
[releases page](https://github.com/sigilco/pyscriptc/releases). The binary still
needs `clang` (or `zig cc`) on PATH to link compiled programs.

:::

## Compile and run

```sh
pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

That prints `285`, the same value CPython returns, from a native binary.

## How a compile works

```sh
pyscriptc compile FILE --run --call FUNC --args '[...]' -o /tmp/name
```

- `FILE` is one Python file or a package directory. Imports resolve from the closed standard-library table.
- `--call` is the function the generated `main` calls. The binary prints its return value.
- `--args` is a Python list literal (`ast.literal_eval`). Elements can be `int`, `float`, `bool`, or `str`.
- The printed return is an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` (or `--run`) links a binary.

## When it stops

| Exit | Cause                                                      |
| ---- | ---------------------------------------------------------- |
| 0    | Compiled, plus the binary's own status if you used `--run` |
| 1    | A compiler fault (`SpikeError`)                            |
| 2    | The type-checker preflight (pyrefly) rejected the file     |
| 3    | One or more fences (`PSxxxx`) — see the printed table      |
| 4    | A version or ABI mismatch                                  |

A fence output names the construct and the code. `pyscriptc explain PS2003` prints what the code means.
