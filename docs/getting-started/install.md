---
title: Install and toolchain
description: Install pyscriptc, install a C toolchain, and check what the compiler found.
---

pyscriptc needs CPython 3.14 or later to run, and a C toolchain to link the programs it compiles.
`pyscriptc doctor` reports exactly what it found; run it first when something does not work.

```bash
uv tool install pyscriptc
pyscriptc doctor
```

## Requirements

- CPython 3.14 or later to run the compiler (PyPI installs only; the prebuilt binary has no Python dependency). The parser is CPython 3.14's `ast`, and the emitted IR is stamped with the interpreter that lowered it.
- A C toolchain to link compiled programs
  - [`zig`](https://ziglang.org/download/) preferred (unlocks cross-compilation), installable with `pip install ziglang`
  - [`clang`](https://clang.llvm.org/get_started.html)

The preflight checker ([pyrefly](https://pyrefly.org/)) ships inside the installed package, so there is no extra setup for it.
`coverage`, `explain`, `--emit ir` and `--emit llvm` need no C toolchain at all.

### Installing clang or zig

| Platform | Command |
| --- | --- |
| macOS | `xcode-select --install` (or `brew install llvm` / `brew install zig`) |
| Debian / Ubuntu | `sudo apt install clang` (or `zig` on newer releases) |
| Fedora | `sudo dnf install clang` (or `zig`) |
| Arch | `sudo pacman -S clang` (or `zig`) |
| Any Linux or macOS | `mise use -g zig` or `brew install zig` |
| Windows | `winget install zig.zig` (or `scoop install zig`) |

### Installing zig with pip

`pip install ziglang` is also a supported way to get zig, and it needs no PATH surgery: the wheel ships the zig binary, and pyscriptc installs a `zig` shim for it (the wheel itself only registers a `python-zig` console script, so `zig` is not on PATH by itself). The shim lives in `~/.cache/pyscriptc/shim`, and `pyscriptc doctor` reports it.

```bash
pip install ziglang          # any zig version the wheel ships
pip install 'ziglang==0.17.0'  # pin the zig version
```

Supported zig versions: >= 0.13 (the floor zig 0.13 wraps clang 18, which clears the LLVM 15 opaque-pointer floor), CI-verified on 0.16.0 and 0.17.0. The full matrix is in [TOOLCHAIN.md](https://github.com/sigilco/pyscriptc/blob/master/packaging/TOOLCHAIN.md). The `ziglang` wheel has no Windows build, so on Windows install zig from [ziglang.org](https://ziglang.org/download/) instead.

Windows is experimental cross-compilation only: `--target x86_64-windows-gnu --driver zig` builds a Windows binary from Linux or macOS, but nothing runs the output in CI and no Windows-native compile is supported yet.

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

On a CPython older than 3.14, pip and uv refuse the install cleanly; `uv tool install --python 3.14 pyscriptc` fetches a suitable interpreter.

## The command

```bash
pyscriptc compile FILE --run [--call FUNC --args '[...]'] [-o /tmp/name] [-- ARGS]
```

- `FILE` is one Python file or a directory. Imports come from the closed standard-library table. A second file of yours is not imported unless you compile the directory.
- A directory with a root `__main__.py` (or a root `main.py`) and no root `__init__.py` is a **program**: that file is the entry point, the siblings are imported by their plain names (`import util`, `from pkg import x`), and every module body runs once, in CPython's import order. A directory with a root `__init__.py` is a **package**: its modules are namespaced under the directory name and its `__main__.py` is the `python -m` script, which the compile skips. `--program` runs that script instead (`python -m pkg` parity). A circular import is refused with the path that closes it.
- `--run` without `--call` runs the file's own script: module-level statements and the body of `if __name__ == "__main__":`, in source order. If the file has neither, it prints a note on stderr naming `--call` and exits 0.
- `--call` runs one function instead. `--args` is a Python list literal (`ast.literal_eval`); elements can be `int`, `float`, `bool`, or `str`. A `list`, a `dict`, or `bytes` is built inside the function. The generated `main` prints the return value: an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `print` of `str`, `int`, `float`, `bool` and `bytes` lowers (see the [support model](../guide/support-model.md)).
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` writes the binary and does not run it. Only `ir` and `llvm` work without a C toolchain.
- `--skip-check` skips the checker. The default preflight is pyrefly, a single MIT-licensed binary that ships with the install (and inside the release binaries). `--checker mypy` and `--checker pyright` are deprecated, warn on use, and need the `oracle-mypy` / `oracle-pyright` extras; see [cross-checking with mypy and pyright](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/spec/external-oracles.md).

`pyscriptc run FILE [-- args]` is the `uv run` shaped shortcut: it compiles once, caches the binary under `~/.cache/pyscriptc/bin` keyed on the source content, the compiler and the flags, then runs it. A second run of an unchanged script skips the compile; arguments after `--` reach `sys.argv`, and stdin, stdout and the exit code pass through. `--no-cache` (or `PYSC_NO_RUN_CACHE=1`) always recompiles.

## When it stops

| Exit | Cause |
| --- | --- |
| 0 | The file lowered, and the binary's own status if you used `--run` |
| 1 | A compiler fault (`SpikeError`) |
| 2 | The type-checker preflight (pyrefly) rejected the file |
| 3 | The subset refused the file. The message names a `PS` code |
| 4 | The integer or the CPython stamp does not fit this backend |

A fence is the whole file. Fix the named construct, or keep that program on CPython.

## What you get

A script that never calls the island links the small runtime and does not link libpython. `examples/hostname.py` calls `socket.gethostname`, so that binary links the CPython that compiled it and initializes it on the call.

## Cross-compiling

`--driver zig --target TRIPLE` links through `zig cc` for another platform, zig bundles each target's sysroot, so no extra install is needed:

```bash
uv run pyscriptc compile examples/invoice.py --emit bin --call charge --args '[10, 2, 50]' \
    --skip-check --driver zig --target aarch64-linux-gnu.2.39 -o /tmp/charge-arm64
```

Tier A targets (CI-verified by `packaging/zig/smoke.sh`): `x86_64-linux-gnu`, `aarch64-linux-gnu`, `riscv64-linux-gnu` (optionally pinned, e.g. `gnu.2.39`), `x86_64-linux-musl`, `aarch64-linux-musl`, `riscv64-linux-musl` (static), and `aarch64-macos` / `x86_64-macos` (compile-verified only, no runner). Static-musl binaries can run foreign arches under `qemu-<arch>-static`.

Experimental cross-compilation targets, both stated as divergences rather than support claims:

- Windows (`x86_64-windows-gnu`, `aarch64-windows-gnu`): builds a PE binary from Linux or macOS, but nothing executes the output anywhere, and the Win32 gaps are divergences 32 and 33 in [divergences](https://pyscriptc.pages.dev/reference/divergences/) (no `lstat`, no winsock, separators and home-resolution differences). Native Windows compiles are not supported yet.
- `wasm32-wasi-musl` (and `wasm32-wasi-p2`): produces a WASI command module for `wasmtime` >= v50, stdout-identical to the native binary on the same program. Preview1 only: no subprocess, no mix, runtime-dead threads, preopen-only filesystem (divergence 31; the `wasi-p2` component wrapper is divergence 33).

Not supported yet: native Windows compiles (the pyscriptc compiler itself runs on Windows as an experimental build, but emitted programs are cross-compilation only), island/fallback programs on any cross target (they embed the host libpython), and the `PYSCRIPTC_MIX_SO` dlopen path on static musl. `--target` requires `--driver zig`; without `--target`, `zig` is just another host driver.

## Debug builds with sanitizers

`--sanitize=address,undefined` compiles the C runtime and the emitted code with the ASan and UBSan runtimes: a memory bug or undefined behaviour in the runtime shims reports itself with a stack trace instead of corrupting a result. It needs a binary (`--emit bin`), cannot be combined with `--target` (the sanitizer runtime must match the host), and `PYSC_SANITIZE=address,undefined` sets it from the environment:

```bash
uv run pyscriptc compile examples/hello.py --emit bin -o /tmp/hello-san --sanitize=address,undefined
/tmp/hello-san
```

CI runs this on every push (`ASan+UBSan smoke` in `.github/workflows/ci.yml`).

## Debugging a compiled program

`--debug-info` makes the emitter attach DWARF metadata to the emitted LLVM: one `DISubprogram` per function and a `DILocation` on the instructions of every statement, so a debugger names the Python file and line of a native frame instead of a generated helper. The flag works with every `--emit` mode, and `PYSC_DEBUG_INFO=1` sets it from the environment:

```bash
uv run pyscriptc compile examples/hello.py --emit bin -o /tmp/hello --debug-info
lldb /tmp/hello
```

Without the flag the emitted text carries no metadata and the binary has no debug sections, so the default build stays unchanged. On macOS the linker moves the DWARF into the companion `hello.dSYM` bundle, which is where `lldb` finds it; on Linux the sections stay in the binary itself. `--debug-info` cannot cross-compile, because the debug info has to match the target toolchain.
