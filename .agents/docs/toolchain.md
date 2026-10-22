# Toolchain Strategy & Driver Resolution

This document describes the C toolchain driver resolution order, Zig preference, and cross-platform packaging strategy.

## Driver Preference (Option A)

`pyscriptc` compiles textual LLVM IR and bundles the C runtime into a native binary via an external C compiler driver (`zig cc` or `clang`).

- **Zig (`zig cc`) is the preferred compiler driver across all platforms** (macOS, Linux, Windows).
  - Bundles per-target sysroots and headers for cross-compilation (`--target`).
  - Supports Windows targets without requiring Visual Studio or external MinGW installations.
  - Zig 0.13+ wraps Clang 18+, satisfying the LLVM 15+ opaque pointer requirement.
- **Clang is the Unix host fallback** when Zig is not installed on macOS or Linux.

## Driver Resolution Order

Toolchain driver selection follows this precedence in `src/pyscriptc/cli.py` (`_apply_env_defaults` and `_require_toolchain`):

1. **Explicit CLI flag**: `--driver zig` or `--driver clang` always wins.
2. **Environment variable**: `PYSC_CC` (`zig` or `clang`).
3. **Cross-compilation or Windows target**: If `--target` or `PYSC_TARGET` is set -> prefers `zig` (required for bundled sysroots).
4. **Windows host**: If `sys.platform == "win32"` -> prefers `zig`.
5. **Zig on PATH**: If `shutil.which("zig")` is found -> prefers `zig`.
6. **Clang fallback**: If `shutil.which("clang")` is found on macOS/Linux -> falls back to `clang`.
7. **Missing toolchain**: If neither is found when compiling to a binary (`--run`, `--emit bin`, `--emit obj`, `--emit asm`), fails up-front with an actionable diagnostic pointing to `pyscriptc doctor` and installation options.

Commands that do not compile binary machine code (such as `--emit ir` and `--emit llvm`) require no C toolchain.

## Obtaining Zig & `ziglang` on PyPI

Zig can be installed via standard system package managers or directly from PyPI:

- **PyPI standalone binary (`ziglang`)**:
  ```bash
  pip install ziglang
  # or using uv:
  uv tool install ziglang
  ```
  `ziglang` distributes prebuilt upstream Zig binaries as platform wheels on PyPI, placing a standard `zig` binary onto `PATH` without requiring root or external package managers.
- **Homebrew (macOS / Linux)**:
  ```bash
  brew install zig
  ```
- **Windows Package Manager (winget)**:
  ```powershell
  winget install zig.zig
  ```
- **Linux distributions**:
  Distro package managers (`apt install zig`, `pacman -S zig`, etc.) or official binaries from [ziglang.org](https://ziglang.org/download/).

## Diagnostic Output (`pyscriptc doctor`)

`pyscriptc doctor` inspects the machine and reports:
- The preferred compiler driver (`preferred_driver` in JSON, `driver: <name> (preferred)` in human-readable text).
- Resolved paths and versions of both `clang` and `zig`.
- Target triple and toolchain driver for default compilation.
