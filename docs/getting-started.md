# Getting started

Requirements: CPython 3.14, [uv](https://docs.astral.sh/uv/), and a clang that can assemble textual LLVM IR.

```bash
uv sync
uv run pyscriptc compile examples/invoice.py --run --call charge --args '[10, 2, 50]' -o /tmp/charge
```

That prints `600`. The same function under CPython returns `600`.

## The command

```bash
uv run pyscriptc compile FILE --run --call FUNC --args '[...]' -o /tmp/name
```

- `FILE` is one Python file. Imports come from the closed standard-library table. A second file of yours is not imported.
- `--call` is the function `main` calls. The binary prints that return value. There is no `print` in the subset.
- `--args` is a Python list literal (`ast.literal_eval`). Elements can be `int`, `float`, `bool`, or `str`. A `list`, a `dict`, or `bytes` is built inside the function.
- The printed return is an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` writes the binary and does not run it.
- `--skip-check` skips the checker. The default preflight is pyrefly, a single MIT-licensed binary. `--checker mypy` and `--checker pyright` are deprecated, warn on use, and need the `oracle-mypy` / `oracle-pyright` extras; see [cross-checking with mypy and pyright](external-oracles.md).

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
