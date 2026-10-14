# Release packaging

Host-native Nuitka onefile of the pyscriptc console entry. The onefile
self-extracts when it starts. Programs it compiles are linked with system
clang, which is not inside the binary. Details and the OS/arch matrix are in
`TOOLCHAIN.md`.

`examples/hello.py` is not in this tree. The smoke compiles `examples/add.py`.

## Build

From the repository root, on the machine you are shipping for:

```bash
packaging/build.sh
```

The script resolves Nuitka with `uv run --with nuitka` and does not edit
`pyproject.toml` or `uv.lock`. It writes:

- `packaging/dist/pyscriptc-<os>-<arch>` — the onefile to ship
- `packaging/build/` — Nuitka intermediates

Both directories are already covered by the repository `.gitignore` (`build/`
and `dist/`). Do not commit them.

This script builds the host it is running on. It does not cross-compile. A
macOS arm64 binary is produced by running the same script on a macOS arm64
host. That host was not used for the binary checked in this change, and no
Mach-O file is produced here.

## Smoke

Requires the onefile from the build step, `python3.12` on `PATH`, and system
`clang` on `PATH`.

```bash
packaging/smoke.sh
```

The onefile self-extracts on launch. The smoke compiles `examples/add.py`
with `--run --call add --args '[1, 2]' --skip-check` and compares that
stdout to CPython 3.12 (`packaging/smoke/oracle_add.py`).

`--skip-check` is required for the frozen binary. The compiler preflight
runs `sys.executable -m mypy`, and inside the onefile `sys.executable` is the
release binary, which does not bundle mypy.
