---
title: Getting started
description: Install pyscriptc, check the toolchain, and compile your first Python file to a native binary.
---

# Getting started

pyscriptc compiles typed Python to a native binary. This page gets you from nothing to a running binary in a few minutes.

## Install

Run the install script for your platform. It checks the prerequisites, installs `uv` and, with the flag, `zig` where it can, then installs pyscriptc and runs `pyscriptc doctor`. It never uses `sudo` or an admin shell: for anything it will not install itself it prints the exact command to run as your normal user.

::: tabs
::: tab "macOS / Linux"
```sh
curl -LsSf https://raw.githubusercontent.com/sigilco/pyscriptc/master/docs/install.sh | sh
```
::: /tab

::: tab "Windows"
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/sigilco/pyscriptc/master/docs/install.ps1 | iex"
```
::: /tab
::: /tabs

The script is a convenience: it does the two commands below and nothing more. To install by hand, use `uv tool install pyscriptc`.

From a checkout of the repository, run the script from the file instead of the URL:

```sh
sh docs/install.sh           # macOS and Linux
.\docs\install.ps1           # Windows
```

Pass `--yes` (or `-Yes` on Windows) to let the script install `uv` and `zig` too.

## Prerequisites

You need two things:

- CPython 3.14 or later, to run the compiler. On an older CPython, `uv tool install --python 3.14 pyscriptc` fetches a suitable interpreter.
- A C toolchain on PATH, to link compiled programs. `zig` also enables cross-compilation.

The type checker ships inside the package, so there is nothing extra to install for it.

### C toolchain by platform

| Platform | Install a compiler |
| --- | --- |
| macOS | `xcode-select --install` (or `brew install zig`) |
| Debian / Ubuntu | `sudo apt install clang` (or `sudo apt install zig` on newer releases) |
| Fedora | `sudo dnf install clang` |
| Arch | `sudo pacman -S clang` |
| Any Linux or macOS | `mise use -g zig` |
| Windows | `winget install zig.zig` (or `scoop install zig`) |

You only need one of `clang` or `zig`. Run `pyscriptc doctor` after installing to see what was found.

## Install pyscriptc

Install pyscriptc as a tool:

```sh
uv tool install pyscriptc
```

`pipx install pyscriptc` and `pip install pyscriptc` do the same thing. Prebuilt binaries for Linux, macOS and an experimental Windows build are on the [releases page](https://github.com/sigilco/pyscriptc/releases); the binary still needs `clang` or `zig` on PATH to link programs.

Then check the toolchain:

```sh
pyscriptc doctor
```

`doctor` prints the pyscriptc version, the checker it found, the C compiler or zig it will link with, and the cache locations. If something is missing, fix it with the table above and run `doctor` again.

## First run

Create a small typed script:

```python
# sum_squares.py
def sum_squares(n: int) -> int:
    total: int = 0
    for i in range(1, n + 1):
        total = total + i * i
    return total


if __name__ == "__main__":
    print(sum_squares(10))
```

Compile and run it:

```sh
pyscriptc run sum_squares.py
```

`pyscriptc run` compiles once, caches the binary under `~/.cache/pyscriptc/bin`, and runs it. A second run of an unchanged file skips the compile. Arguments after `--` reach the program as `sys.argv`.

To compile a function and run it directly:

```sh
pyscriptc compile sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

`--call` names the function to run and prints its return value, `--args` passes a Python list of arguments, and `-o` names the output binary. Every subcommand and flag is listed in the [CLI reference](reference/cli.md).

## When a compile stops

If a file cannot be compiled, pyscriptc prints a table naming each construct and a code such as `PS2003`, and exits with a status that says what happened:

| Exit | Cause |
| --- | --- |
| 0 | Compiled (plus the binary's own status with `--run`) |
| 1 | An internal compiler error |
| 2 | The type checker rejected the file (or a command-line syntax error) |
| 3 | The file uses something pyscriptc cannot compile yet |
| 4 | A version or target mismatch |

Run `pyscriptc explain PS2003` to see what a code means and how to change your code. The full list of diagnostic codes is in [Diagnostics](reference/diagnostics.md), and the general reference for unsupported behavior is [Divergences from CPython](reference/divergences.md).
