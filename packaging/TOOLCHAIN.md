# Toolchain contract for the release binary

The release binary is the compiler, not the toolchain. It lowers Python to textual LLVM IR and then shells out to a C compiler driver (`zig cc` preferred, `clang` fallback) to turn that IR plus the C runtime into a native executable. This file is the contract between the binary and the machine it runs on.

## What the user must have

| Requirement | Why | Minimum |
| --- | --- | --- |
| `zig` (preferred) or `clang` on `PATH` | `link_binary` in `src/pyscriptc/llvm.py` compiles `.ll` and runtime `*.c` objects and links the executable | Zig >= 0.13 (wraps Clang 18+) or Clang >= 15 |
| A C library with headers and `libm` | the runtime includes `stdio.h`, `math.h`, `dirent.h`, `dlfcn.h` and friends | Linux: glibc 2.34 or newer plus `libc6-dev` (or distro equivalent) |
| Nothing else | no Python install, no `pip`, no mypy install | none |

### Obtaining Zig

Zig is the preferred toolchain driver across all platforms (macOS, Linux, Windows), and is required for cross-compilation (`--target`).
It can be installed via:
- System package managers:
  - macOS: `brew install zig`
  - Windows: `winget install zig.zig`
  - Linux: distro packages (`apt install zig`, `pacman -S zig`, etc.) or official tarballs.
- The PyPI `ziglang` wheel: `pip install ziglang` installs the Zig toolchain but its console script is `python-zig`, not `zig`, so the compiler will not find it on PATH until you add the wheel's `zig` binary (inside `site-packages/ziglang/`) to PATH yourself, or symlink it. The upstream tracker is the place for a `zig` entry point request; treat `pip install ziglang` as a source of the binary, not as a PATH install.

Tested clangs: Apple clang 21.0.0 (Xcode, macOS arm64) and Debian trixie `clang` 19 (the dev container). Not tested on anything older.

### Why LLVM 15

The emitted IR uses opaque pointers (`ptr`, 98 call sites in `llvm.py`) and no typed `i8*` form. LLVM 15 made opaque pointers the default and 17 removed the typed ones, so 15 is the floor that parses the text. This is derived from the IR syntax, not from a run on an old clang. Treat 15 as an unverified floor and 19 as the verified one. `llvm.*.with.overflow` intrinsics and `_Thread_local` in the runtime are available well before that.

### Why glibc 2.34

`runtime/ps_mix.c` calls `dlopen`. glibc before 2.34 keeps it in `libdl`, and the link line has no `-ldl`. Alpine and musl are not a documented target.

## How the binary finds the compiler driver

The driver resolution order is:
1. Explicit `--driver` argument (`zig` or `clang`).
2. `PYSC_CC` environment variable (`zig` or `clang`).
3. If `--target` or `PYSC_TARGET` is set (or cross-compiling) -> `zig` (required for bundled sysroots).
4. If running on Windows (`win32`) -> `zig`.
5. If a sanitizer is requested (`--sanitize` or `PYSC_SANITIZE`) -> `clang`: `zig cc` compiles with `-fsanitize=` but has no linkable sanitizer runtime, so the link fails with `undefined symbol: ___asan_init`.
6. If `zig` is found on `PATH` -> `zig`.
7. Fallback to `clang` on macOS/Linux if found on `PATH`.
8. If neither is found, fails up front with an actionable diagnostic pointing to `pyscriptc doctor` and installation options (`brew install zig`, `winget install zig`, `pip install ziglang` (see the note above), or `clang`).

Notes:
- Xcode command line tools on macOS put `clang` at `/usr/bin/clang`.
- `pip install ziglang` puts a `python-zig` console script on PATH, not `zig`; the real binary sits inside `site-packages/ziglang/` and needs a PATH entry or a symlink.
- `pyscriptc doctor` reports the detected clang and zig paths, versions, and current preferred driver.

## Data files the binary must carry

The compiler reads these at run time, relative to its own package directory (`Path(__file__).parent / "runtime"`). Nuitka onefile unpacks them next to the compiled modules, so `build.sh` includes them as data files at the same relative path:

| Source | Target inside the binary | Used by |
| --- | --- | --- |
| `src/pyscriptc/runtime/*.c` (18 files) | `pyscriptc/runtime/` | globbed and compiled into every program |
| `src/pyscriptc/runtime/*.h` (`ps_rt.h`) | `pyscriptc/runtime/` | included by the C files |
| mypy package data (`mypy/typeshed/**`, `mypy/xml/**`) | `mypy/` | the mypy preflight needs the bundled stubs |

If a new file type appears in `runtime/`, extend both `--include-data-files` lines in `build.sh`. `pyproject.toml` has the same list under `[tool.setuptools.package-data]` and the two must agree.

## mypy preflight inside a onefile binary

`src/pyscriptc/check.py` runs `sys.executable -m mypy ...` as a subprocess. In a onefile build `sys.executable` points at a path that does not exist, and the binary has no `-m` switch. `packaging/entry.py` handles both without touching `src/`: it points `sys.executable` at the binary itself, and answers `binary -m mypy ARGS` by calling `mypy.main.main` in process. Every `compile` therefore unpacks the binary twice (once for the compiler, once for the mypy child). `--skip-check` avoids the second unpack.

## zig cc alternative

`zig cc` can replace `clang` for emitted user programs on macOS and Linux: it is a clang front end, and zig 0.13 or newer wraps clang 18+, which clears the LLVM 15 opaque-pointer floor. Floor: zig >= 0.13 (clang 18); zig >= 0.15 wraps clang 20. Verified on zig 0.16.0 with `-target x86_64-linux-gnu.2.39`, which also pins the exact glibc the output binary needs (`zig targets` lists valid versions). Two usage notes: pass `.ll` files by extension, `zig cc` does not accept clang's `-x ir` flag; and glibc pins need the matching loader at run time, same as any cross-built binary. `packaging/zig/smoke.sh` proves the non-mix and mix paths on every CI run.

Not supported under zig cc: Windows-native emitted programs on a Windows host (no Windows runner builds or executes emitted programs anywhere; Windows is experimental cross-compilation from Linux/macOS only, and semantics on Windows are unverified). Static musl targets for mix programs (static musl cannot `dlopen`, so `PYSCRIPTC_MIX_SO` is dead there). A mix `.so` built against a different libc than the emitted binary. Also not in scope: compiling the compiler itself with zig (Nuitka-on-zig is experimental).

Release-note blurb, matching release.yml's draft-body style: "Emitted programs may also be compiled with `zig cc` >= 0.13 instead of `clang`, on macOS and Linux. `zig cc -O2 prog.ll <runtime>/*.c -target x86_64-linux-gnu.2.39 -lm -o out` builds a binary pinned to glibc 2.39. Windows targets (`x86_64-windows-gnu`, `aarch64-windows-gnu`) are experimental cross-compilation: the compiler builds them but no CI job runs the output, and real Windows support is a later milestone. Static-musl mix programs and cross-libc mix .so files are not supported. The released compiler binary is still built with the native per-runner toolchains."

## Known issues

- **Platform constants (A0).** `src/pyscriptc/stdmore.py` reads every `errno` name, and some `os` names, at import time. macOS lacks `errno.EADV` and `os.CLONE_FILES`, so an unpatched binary dies on start. The fix is being made in the `pyscriptc-a0-macos` worktree. Until it lands, `entry.py` retries the import and sets each missing name to `-1`. A script that names such a constant on macOS gets `-1`, which is wrong. Delete `_load_cli` from `entry.py` once A0 is merged; it is inert on a tree where the import already works.
- **libpython programs.** A program that emits `ps_island_*` links `-lpython3.14` using `sysconfig` paths of the interpreter running the compiler. In the release binary those paths describe the build machine, not the user's. Island programs are not a supported release target. Programs without `ps_island_` symbols are.
- **macOS deployment target.** Nuitka stamps the binary with the OS it was built on (`macOS 26.0 or higher` on the CI host). Build on the oldest macOS you want to support, or set `MACOSX_DEPLOYMENT_TARGET` when calling `build.sh`.
- **Cross builds.** Nuitka compiles natively. Each of macos-arm64, linux-aarch64 and linux-x86_64 is built on that OS and arch.
