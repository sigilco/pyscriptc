# Toolchain contract for the release binary

The release binary is the compiler, not the toolchain. It lowers Python to textual LLVM IR and then shells out to the system `clang` to turn that IR plus the C runtime into a native executable. This file is the contract between the binary and the machine it runs on.

## What the user must have

| Requirement | Why | Minimum |
| --- | --- | --- |
| `clang` on `PATH` | `link_binary` in `src/pyscriptc/llvm.py` runs `clang -O2 -x ir prog.ll -x c <runtime>/*.c -lm -o out` | LLVM 15 (see below) |
| A C library with headers and `libm` | the runtime includes `stdio.h`, `math.h`, `dirent.h`, `dlfcn.h` and friends | Linux: glibc 2.34 or newer plus `libc6-dev` (or the distro equivalent) |
| Nothing else | no Python install, no `pip`, no mypy install | none |

Tested clangs: Apple clang 21.0.0 (Xcode, macOS arm64) and Debian trixie `clang` 19 (the dev container). Not tested on anything older.

### Why LLVM 15

The emitted IR uses opaque pointers (`ptr`, 98 call sites in `llvm.py`) and no typed `i8*` form. LLVM 15 made opaque pointers the default and 17 removed the typed ones, so 15 is the floor that parses the text. This is derived from the IR syntax, not from a run on an old clang. Treat 15 as an unverified floor and 19 as the verified one. `llvm.*.with.overflow` intrinsics and `_Thread_local` in the runtime are available well before that.

### Why glibc 2.34

`runtime/ps_mix.c` calls `dlopen`. glibc before 2.34 keeps it in `libdl`, and the link line has no `-ldl`. Alpine and musl are not a documented target.

## How the binary finds clang

It does not search. `link_binary` passes the bare name `clang` to `subprocess.run`, so the OS resolves it through `PATH` of the process that started the binary. There is no `CC` variable, no `--clang` flag and no bundled fallback. Consequences:

- Xcode command line tools on macOS put `clang` at `/usr/bin/clang` (a shim that forwards to the active developer directory). That works.
- Homebrew LLVM or a mise toolchain works if its `bin` comes first on `PATH`.
- A missing `clang` surfaces as a `FileNotFoundError` traceback, not a friendly message. The smoke test checks `command -v clang` first for that reason. Making this a named diagnostic is a compiler change and is out of scope for `packaging/`.

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

## Known issues

- **Platform constants (A0).** `src/pyscriptc/stdmore.py` reads every `errno` name, and some `os` names, at import time. macOS lacks `errno.EADV` and `os.CLONE_FILES`, so an unpatched binary dies on start. The fix is being made in the `pyscriptc-a0-macos` worktree. Until it lands, `entry.py` retries the import and sets each missing name to `-1`. A script that names such a constant on macOS gets `-1`, which is wrong. Delete `_load_cli` from `entry.py` once A0 is merged; it is inert on a tree where the import already works.
- **libpython programs.** A program that emits `ps_island_*` links `-lpython3.14` using `sysconfig` paths of the interpreter running the compiler. In the release binary those paths describe the build machine, not the user's. Island programs are not a supported release target. Programs without `ps_island_` symbols are.
- **macOS deployment target.** Nuitka stamps the binary with the OS it was built on (`macOS 26.0 or higher` on the CI host). Build on the oldest macOS you want to support, or set `MACOSX_DEPLOYMENT_TARGET` when calling `build.sh`.
- **Cross builds.** Nuitka compiles natively. Each of macos-arm64, linux-aarch64 and linux-x86_64 is built on that OS and arch.
