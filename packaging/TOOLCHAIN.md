# Toolchain contract

The release onefile and the programs it compiles both use **system clang**.
Clang is a host tool. It is not bundled in the onefile.

## Contract

- `clang` must be on `PATH` when `packaging/build.sh` runs, and again when the
  onefile compiles a program.
- `build.sh` sets `CC=clang` and `CXX=clang++` and passes Nuitka `--clang`,
  so the onefile itself is compiled with system clang.
- At user-compile time, pyscriptc invokes `clang` on `PATH` to assemble LLVM
  IR and link the runtime C sources. Those sources ship inside the onefile as
  package data (`pyscriptc/runtime/*.c` and `*.h`) and are extracted when the
  onefile starts.
- Nuitka is not a project dependency. The build runs
  `uv run --with nuitka`, which leaves `pyproject.toml` and `uv.lock` alone.
- The script is host-native. It names the binary from `uname` and does not
  cross-compile.

## OS and arch matrix

| Host | Command | Verified by this change |
| --- | --- | --- |
| Linux x86_64 | `packaging/build.sh` then `packaging/smoke.sh` | yes, on this VM |
| macOS arm64 | the same `packaging/build.sh` on that host | no |
| Linux aarch64 | the same `packaging/build.sh` on that host | no |

The macOS arm64 command is the same script on a macOS arm64 machine. It was
not run here. No Mach-O binary was built.

Phase 9 S0 also names a linux aarch64 unpack. This environment is Linux
x86_64 only, so that host was not built either.

## What this host verified

Verified on 2026-10-14 on this Linux x86_64 VM. macOS arm64 was not built.
No Mach-O binary was produced.

- Host: `Linux x86_64` (`uname -m` is `x86_64`)
- Onefile: `packaging/dist/pyscriptc-linux-x86_64`, ELF 64-bit LSB pie executable, x86-64, dynamically linked
- Nuitka: 4.3, invoked as `uv run --with nuitka` (not pinned, not written to `uv.lock`)
- Interpreter Nuitka compiled against: CPython 3.14.8 (the project requires `>=3.14`)
- Clang: Ubuntu clang version 18.1.3, used both as Nuitka's backend (`--clang`) and as the linker the onefile calls for user programs
- Smoke command: `packaging/smoke.sh`
- Smoke result: success. `examples/add.py` compiled with `--run --call add --args '[1, 2]' --skip-check`. Stdout was `3` plus a newline, matching CPython 3.12 (`python3.12 packaging/smoke/oracle_add.py`).
- The onefile self-extracts on launch. The smoke's first step is `pyscriptc --help`, which only succeeds after that unpack. Runtime C sources are packed as `pyscriptc/runtime/*` and linked with system clang.

`--skip-check` is part of the smoke because the checker launches
`sys.executable -m mypy`. In the onefile, `sys.executable` is the release
binary, and mypy is not bundled.
