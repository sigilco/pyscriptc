# The static single-binary island interpreter (#672)

Issue #672 (stretch, v0.10.0). The static island interpreter is a
`--disable-shared --enable-optimizations` CPython built from the pinned
*source* tarball, with a curated `Modules/Setup.local` builtin list, so an
island program can link one file and ship as a single executable plus the
stdlib zip. This document is the design and the measured CI cost; the user
facing page is `docs/reference/runtime-pack.md`.

## Why source, not a prebuilt archive

The python-build-standalone `pgo+lto-full` archives ship `libpython3.14.a`
as LLVM bitcode (producer LLVM 23.1.3). Apple ld (LLVM 21) fails with
"Unknown attribute kind (105)" and zig 0.16 fails with "unknown cpu
architecture: 0" on it. A source build produces a plain object archive
every linker we use can consume, and `--enable-optimizations` (PGO) buys
about 10 percent over a plain build on the embedding plan's benchmark.

## What is pinned where

- `packaging/static-cpython.lock.toml`: the CPython micro (tracks
  `packaging/runtime.lock.toml`), the tarball URL and sha256, the
  `configure_args` and the `setup_local` list path. The script verifies the
  sha256 on every use and refuses a mismatch.
- `packaging/Setup.local`: the curated builtin list, copied to
  `Modules/Setup.local` before configure. The first rule for a module wins
  in makesetup, so the `*static*` block overrides the shared builds
  `Modules/Setup.stdlib` would otherwise generate. Selection rule: every
  extension the island import closure can hit on a desktop/server target,
  nothing that drags in an external library (no OpenSSL, no sqlite, no
  readline, no curses, no libffi, no zlib/bz2/lzma/zstd). A stored stdlib
  zip needs no zlib (see the embedding plan).
- `scripts/build_static_cpython.py`: fetch (cached under
  `~/.cache/pyscriptc/static-cpython/`), verify, unpack, copy the list in,
  configure, `make -j`, `make install`, then a smoke run: every curated
  module must import from the binary alone and the plan's `fractions`
  program must print `11/12`.

## Measured build cost (the stretch gate)

Measured on the Dory VPS (Debian 13, 6 cores, gcc 14.2.0, 2026-11-12),
cold cache, `configure + make -j6 + make install` with PGO:

- Linux x86_64: about 12 minutes wall clock, 24 MB download.

A GitHub-hosted `ubuntu-24.04` or `macos-26` runner is 4 cores, so the
same build runs about 1.5x slower: **15-20 minutes per cell cold**, plus
about 1 minute warm with a ccache/sccache key on the tarball hash. That is
an order of magnitude over the embed budget of issue #662 (4 minutes per
cell, 12 per full run), and the macOS cell bills at 10x.

Decision recorded on #672: the build path lands as a script plus a
non-required workflow (`.github/workflows/static-cpython.yml`,
`workflow_dispatch` and nightly only, never `pull_request`); the
per-platform required matrix waits for an owner call on the budget.
Options when the budget is approved: cache the *built prefix* (not just
ccache) on the lock hash, which makes reruns about a minute; or run the
build only in the nightly and the release rehearsal, as the #662 macOS
escape hatch does.

## The link path (design, not wired in this slice)

`src/pyscriptc/emit/llvm/_driver.py` links `ps_island.c` against one
libpython today: the host shared library (`-lpython3.14`) or the pinned
pack's shared library by path. A static link instead passes
`lib/python3.14/config-<platform>-python/libpython3.14.a` plus
`-ldl -lm -lpthread -lutil` (the same ELF extras `embed.link_flags` adds
for the shared bundle), compiles `ps_island.c` against the static build's
`include/python3.14`, and stages only the stdlib zip next to the binary —
no `lib/` directory, no rpath. The output is `name/bin/name` plus
`name/lib/python314.zip`, the 6.4 MB + 0.25 MB of the POC. The compiler
change is a third `embed` mode resolved in `embed.resolve_mode`; the
staging change is a `stage_static` beside `stage_bundle`. Both stay out of
this PR: they need the per-platform CI matrix to be trustworthy, and that
is exactly what the stretch gate withholds.

Known static-link wrinkles, measured on the Linux x86_64 build:

- getpath needs `lib/python3.14/lib-dynload/` to exist even when empty (the
  plan measured "Could not find platform dependent libraries" otherwise).
- The stdlib zip must be **stored** (uncompressed) when `zlib` is not
  builtin: zipimport fails at `encodings` with "can't decompress data; zlib
  not available". The shared bundle path deflates its zip (`stdlibzip.py`),
  which is fine there because the PBS pack builds zlib into the library.
- The curated list changes the link line: `binascii` picks up `-lz` (the
  zlib CRC32 path) and `math`/`cmath`/`_statistics`/`_datetime` pick up
  `-lm`, so a static link needs `-lz -lm` on top of the ELF extras
  `-ldl -lm -lpthread -lutil`. `socket` needs no extra on Linux.
- The curated list is per-micro: it tracks `Modules/Setup.stdlib.in`, which
  moves between micros. A lock bump re-checks the module names.

### Measured link (Linux x86_64, this VPS)

`gcc -O2 -I <prefix>/include/python3.14 embed.c <prefix>/lib/python3.14/config-3.14-x86_64-linux-gnu/libpython3.14.a -ldl -lm -lpthread -lutil -lz`
against a 15-line `Py_InitializeEx` + `PyRun_SimpleString` program:

- the executable is **33.4 MB** (a debug-info-free `-O2` link of the whole
  interpreter), not the 6.4 MB of the macOS POC: the POC's number came from
  a Darwin build where the linker drops more, and this build carries
  `-g` debug info in the archive (the static archive is 75.9 MB). A
  `strip`ped link and a Darwin build are the honest comparison to run
  before quoting a size; the number here is an upper bound.
- it runs from an empty environment (`env -i`, no `PYTHONHOME`, no PATH)
  once `lib/python314.zip` sits next to it and `lib/python3.14/lib-dynload/`
  exists: `fractions` prints `11/12` and `math` is reported as a builtin.
- the stdlib zip of the *untrimmed* tree is 107 MB stored; the trimming the
  bundle path already does (`stdlibzip.py`, `.pyc` bodies only) is what
  brings it to the plan's 0.25 MB for a small program.

## Divergence note

A static island binary runs the curated interpreter: the pure-Python
`zlib`/`gzip` modules import, but their compression backends (`zlib`,
`_bz2`, `_lzma`, `_zstd`) are absent by policy, so anything that compresses
stops at run time where a host-linked island would not. This is the same
class as the existing island divergences (the island mirrors the host it
links) and is recorded here, not in `divergences.md`, until the link path
wires in.
