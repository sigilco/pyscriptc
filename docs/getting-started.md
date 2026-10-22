# Getting started

Requirements: [uv](https://docs.astral.sh/uv/) (or pipx/pip), CPython 3.14 or later (`uv tool install --python 3.14 pyscriptc` fetches it), and, to build a binary, a clang that can assemble textual LLVM IR (or `zig`). See [Installing a C toolchain](#installing-a-c-toolchain) below. `pyscriptc doctor` reports what it found. Emitted programs cannot be compiled on Windows yet; use Linux, macOS or WSL.

```bash
uv tool install pyscriptc
pyscriptc coverage your_script.py
```

`coverage` lists every fence in your file and needs no C toolchain. A fence is the whole file, and the subset is small, so expect most real scripts to fence. Then try an example:

```bash
pyscriptc compile examples/hello.py --run
pyscriptc compile examples/invoice.py --run --call charge --args '[10, 2, 50]'
```

The first prints a greeting. The second prints `600`, the value CPython returns. From a checkout, run `uv sync` and prefix commands with `uv run`.

## The command

```bash
pyscriptc compile FILE --run [--call FUNC --args '[...]'] [-o /tmp/name] [-- ARGS]
```

- `FILE` is one Python file or a directory. Imports come from the closed standard-library table. A second file of yours is not imported unless you compile the directory.
- A directory with a root `__main__.py` (or a root `main.py`) and no root `__init__.py` is a **program**: that file is the entry point, the siblings are imported by their plain names (`import util`, `from pkg import x`), and every module body runs once, in CPython's import order. A directory with a root `__init__.py` is a **package**: its modules are namespaced under the directory name and its `__main__.py` is the `python -m` script, which the compile skips. `--program` runs that script instead (`python -m pkg` parity). A circular import is refused with the path that closes it.
- `--run` without `--call` runs the file's own script: module-level statements and the body of `if __name__ == "__main__":`, in source order. If the file has neither, it prints a note on stderr naming `--call` and exits 0.
- `--call` runs one function instead. `--args` is a Python list literal (`ast.literal_eval`); elements can be `int`, `float`, `bool`, or `str`. A `list`, a `dict`, or `bytes` is built inside the function. The generated `main` prints the return value: an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `print` of `str`, `int`, `float`, `bool` and `bytes` lowers (see [supported.md](supported.md)).
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` writes the binary and does not run it. Only `ir` and `llvm` work without a C toolchain.
- `--skip-check` skips the checker. The default preflight is pyrefly, a single MIT-licensed binary that ships with the install (and inside the release binaries). `--checker mypy` and `--checker pyright` are deprecated, warn on use, and need the `oracle-mypy` / `oracle-pyright` extras; see [cross-checking with mypy and pyright](external-oracles.md).

## When it stops

| Exit | Cause |
| --- | --- |
| 0 | The file lowered, and the binary's own status if you used `--run` |
| 2 | the checker rejected an annotation |
| 3 | The subset refused the file. The message names a `PS` code |
| 4 | The integer or the CPython stamp does not fit this backend |

A fence is the whole file. Fix the named construct, or keep that program on CPython.

## What you get

A script that never calls the island links the small runtime and does not link libpython. `examples/hostname.py` calls `socket.gethostname`, so that binary links the CPython that compiled it and initializes it on the call.

## Cross-compiling

`--driver zig --target TRIPLE` links through `zig cc` for another platform — zig bundles each target's sysroot, so no extra install is needed:

```bash
uv run pyscriptc compile examples/invoice.py --emit bin --call charge --args '[10, 2, 50]' \
    --skip-check --driver zig --target aarch64-linux-gnu.2.39 -o /tmp/charge-arm64
```

Tier A targets (CI-verified by `packaging/zig/smoke.sh`): `x86_64-linux-gnu`, `aarch64-linux-gnu`, `riscv64-linux-gnu` (optionally pinned, e.g. `gnu.2.39`), `x86_64-linux-musl`, `aarch64-linux-musl`, `riscv64-linux-musl` (static), and `aarch64-macos` / `x86_64-macos` (compile-verified only — no runner). Static-musl binaries can run foreign arches under `qemu-<arch>-static`.

Not supported yet: Windows targets (a few POSIX calls in the runtime need shims), island/fallback programs on any cross target (they embed the host libpython), and the `PYSCRIPTC_MIX_SO` dlopen path on static musl. `--target` requires `--driver zig`; without `--target`, `zig` is just another host driver.

## Installing a C toolchain

Compiling a program to a binary needs a clang that can assemble textual LLVM IR (`zig cc` also works, and enables `--driver zig` cross-compilation). `coverage`, `explain`, `--emit ir` and `--emit llvm` do not. If the driver is missing, `compile` stops before doing any work and points at `pyscriptc doctor`, which reports what was found.

| Platform | Command |
| --- | --- |
| macOS | `xcode-select --install` (or `brew install llvm` / `brew install zig`) |
| Debian / Ubuntu | `sudo apt install clang` (or `zig` on newer releases) |
| Fedora | `sudo dnf install clang` (or `zig`) |
| Arch | `sudo pacman -S clang` (or `zig`) |
| Any Linux or macOS | `mise use -g zig` or `brew install zig` |

Windows: the `pyscriptc` binary runs there (experimental) and `coverage` works, but emitted programs cannot be compiled on Windows yet, because the C runtime needs POSIX `dlfcn.h` that MinGW-w64 lacks. Use Linux, macOS or WSL to build programs.

## Other install methods

`pipx install pyscriptc` and `pip install pyscriptc` work the same way as `uv tool install`, on CPython 3.14 or later (older Pythons are refused cleanly). The installed command finds the pyrefly checker that ships with it, so no extra PATH setup is needed. Prebuilt standalone binaries are attached to each [GitHub Release](https://github.com/sigilco/pyscriptc/releases): macOS arm64 and x86_64, Linux x86_64 and aarch64, and Windows x86_64 (experimental). The binaries bundle pyrefly too. On macOS the first launch of a binary can take several seconds; the PyPI install avoids that.

## pyscriptc run

`pyscriptc run FILE [-- args]` is the `uv run` shaped shortcut: it compiles once, caches the binary under `~/.cache/pyscriptc/bin` keyed on the source content, the compiler and the flags, then runs it. A second run of an unchanged script skips the compile; arguments after `--` reach `sys.argv`, and stdin, stdout and the exit code pass through. `--no-cache` (or `PYSC_NO_RUN_CACHE=1`) always recompiles.

```bash
pyscriptc run examples/hello.py
pyscriptc run script.py -- first second
```
