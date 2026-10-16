# Cross-compilation assessment

Can pyscriptc ship cross-compiled binaries? Verdict: **yes for POSIX targets
with near-zero compiler work; Windows needs a small runtime port; the island
fallback stays explain-only per target.**

Evidence below was measured on zig 0.16.0 (the version CI already pins for
`zig-smoke`) against the real emitted `.ll` fixtures and all 17 non-island
runtime C files.

## Why the hard part is already done

- The emitter writes target-agnostic LLVM IR: no `target triple`, no
  `target datalayout`, opaque `ptr`, `i64` scalars. The backend fills in the
  triple — verified: zig overrides the module triple with only a
  `-Woverride-module` warning on every target tried.
- `link_binary` is a single 25-line seam in `llvm.py`: `clang -O2 -x ir prog.ll
  -x c runtime/*.c -lm -o out`. Every cross decision lives in that one call.
- `zig cc` is a drop-in clang driver that bundles per-target sysroots
  (glibc at any version, musl, mingw-w64, macOS libc) and LLD — no external
  sysroot or cross-linker install is needed. CI already proves it end-to-end:
  `packaging/zig/smoke.sh` builds `hello.ll` + the full runtime for
  `x86_64-linux-gnu.2.39` and runs it (prints 42), plus a `dlopen`-loaded
  libmix built by zig cc itself.
- Zig accepts `.ll` input by extension (it rejects clang's `-x ir`), which is
  already the working recipe in CI.

## Measured target matrix (zig 0.16.0, this repo's fixtures)

| Target | Result | Evidence |
| --- | --- | --- |
| `x86_64-linux-gnu.2.39` | builds + runs | CI `zig-smoke` job |
| `aarch64-linux-gnu.2.39` | builds (ELF aarch64) | local spike |
| `riscv64-linux-gnu.2.39` | builds (ELF riscv64) | local spike |
| `x86_64-linux-musl` | static binary, runs, prints 42 | local spike |
| `aarch64-macos` | builds (Mach-O arm64) via 2-stage | local spike, see zig bug below |
| `x86_64-windows-gnu` | 4/17 runtime files fail | local spike, POSIX gaps below |
| `aarch64-windows-gnu` | same 4 files | local spike |

Two findings from the spike that shape the design:

1. **Zig 0.16 `.ll`-on-link-line bug (macOS only).** `zig cc hello.ll rt/*.c
   -target aarch64-macos` exits 0 but writes an empty output file (and
   segfaulted in one earlier invocation). Compiling `.ll -> .o` first, then
   linking objects, works and produces a valid Mach-O arm64 executable. Linux
   targets are unaffected, but the two-stage flow is the safer recipe
   everywhere: `driver -c prog.ll -o prog.o` then `driver prog.o rt/*.c -o out`.
2. **The Windows gap is 4 files, not a redesign.** Per-file compile scan of
   `runtime/` for `x86_64-windows-gnu`:
   - `ps_mix.c` — `dlfcn.h`/`dlopen` → `LoadLibrary`/`GetProcAddress` shim,
     or mark the mix path dead on Windows (same class of limitation as
     static-musl, where it is already documented dead).
   - `ps_osmore.c`, `ps_path.c` — `mkdir` takes 1 arg under mingw, `lstat` and
     `S_ISLNK` are missing → guarded `#ifdef _WIN32` shims or feature-off
     stubs.
   - `ps_time.c` — `gmtime_r`/`localtime_r` → `gmtime_s`/`localtime_s`.
   The other 13 runtime files compile clean for Windows as-is.

## What does not cross-compile

- **Island/fallback programs.** `ps_island.c` embeds CPython; the link needs a
  cross-built `libpythonX.Y` for the target. Keep the policy from the C5
  decision: fallback is explain-only per target, not a porting goal.
- **Mix on static musl.** `dlopen` cannot work in a static binary; already
  documented dead there. Same verdict will apply to Windows unless the mix
  seam is ported.
- **Windows-native beyond mingw.** If MSVC ABI is ever wanted, that is a
  separate sysroot/linker story; `windows-gnu` covers the realistic surface.

## Recommendation

Ship it as a link-layer feature, not an emitter feature:

1. **Two-stage link in `link_binary`.** Compile `.ll` to `.o`, then link
   objects + runtime C. Neutral for clang today, required for zig+macOS, and
   it makes the pipeline target-clean (objects are per-target artifacts).
2. **Driver + target plumbing.** `pyscriptc --target <triple>
   [--driver zig|clang]`. `zig cc` is the only driver that makes this cheap:
   `clang --target` works but demands a per-target sysroot and linker that we
   would have to acquire, document, and pin ourselves — that is exactly what
   `zig cc` bundles. Clang stays the default driver for host builds.
3. **Tier the matrix honestly:**
   - Tier A (ship now, compile-verified in CI): linux-gnu
     x86_64/aarch64/riscv64, linux-musl static, macOS arm64/x86_64.
   - Tier B (one focused PR): `windows-gnu` — the 4-file shim list above.
   - Tier C (explain-only): island fallback on any cross target; mix on
     musl/Windows.
4. **Verification cost is the real constraint, not the toolchain.** Compiling
   for aarch64/riscv64/musl/macos is already proven; *running* needs
   `qemu-user` for the Linux arches (cheap, one apt package, extend
   `zig-smoke` into a matrix) and a macOS/Windows runner for those targets
   (CI service decision, not code).

Effort estimate: plumbing + CI matrix ≈ one small PR. Windows shims ≈ one
more. This is overwhelmingly a build-systems task; no `lower/` or IR changes
are involved, so it does not conflict with the E0 move track.

## Sources

- Andrew Kelley, "`zig cc`: a Powerful Drop-In Replacement for GCC/Clang":
  https://andrewkelley.me/post/zig-cc-powerful-drop-in-replacement-gcc-clang.html
- Zig 0.16.0 release / targets: https://ziglang.org/download/0.16.0/release-notes.html
- Repo evidence: `packaging/zig/smoke.sh`, `.github/workflows/ci.yml` zig-smoke job,
  `src/pyscriptc/llvm.py` `link_binary`, `src/pyscriptc/runtime/` per-file scans.
