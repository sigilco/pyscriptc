Draft release. Not published.

| Target | Artifact | Runner | Smoke |
| --- | --- | --- | --- |
| macos-arm64 | `pyscriptc-macos-arm64` | macos-14 | full |
| macos-x86_64 | `pyscriptc-macos-x86_64` | macos-15-intel | full |
| linux-x86_64 | `pyscriptc-linux-x86_64` | ubuntu-24.04 | full |
| linux-aarch64 | `pyscriptc-linux-aarch64` | ubuntu-24.04-arm | full |
| windows-x86_64 (experimental) | `pyscriptc-windows-x86_64.exe` | windows-2022 | explain and --help only (see note) |

Checksums for the five binaries are in `sha256sums.txt`.

Linux x86_64 and linux aarch64 are built on ubuntu-24.04. glibc on that image is 2.39. That is the floor for these artifacts. This workflow does not retarget an older runner to chase an older glibc.

`pyscriptc-windows-x86_64.exe` is experimental. The compiler binary may build with Nuitka MinGW64. Windows smoke covers `explain` and `--help` only, not compile and run: `link_binary` compiles every `runtime/*.c` into every emitted program, and `ps_mix.c` needs `dlfcn.h`/`dlopen`, which MinGW-w64 does not provide (programs also use `dirent.h` and `unistd.h`). There is no Windows arm64 artifact.

Each binary is the compiler only. User programs still need `clang` on `PATH` (see `packaging/TOOLCHAIN.md`). Nuitka does not cross-compile: each row is built on its own OS and CPU.
