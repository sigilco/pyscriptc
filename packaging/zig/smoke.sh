#!/usr/bin/env bash
# zig cc smoke: prove emitted LLVM IR plus the C runtime builds and runs under
# `zig cc` for a pinned glibc target. Two checks:
#
#   1. non-mix: hello.ll + all runtime/*.c (minus ps_island.c) -> prints 42
#   2. mix:     mixdemo.ll + runtime + libmix.so built by zig cc -> prints the
#      ISO-HDLC crc32 of the payload (dlopen through the glibc loader)
#
# Both .ll files are standalone emissions (compiled with --emit bin --call).
# Regenerate with: packaging/zig/emit.sh
#
#   packaging/zig/smoke.sh   # needs `zig` on PATH
#
# Not covered: Windows targets (no dlfcn.h under mingw-w64) and musl targets
# (static musl cannot dlopen, so the mix path is dead there).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
target="x86_64-linux-gnu.2.39"

fail() { echo "ZIG SMOKE FAIL: $*" >&2; exit 1; }
command -v zig >/dev/null || fail "zig is not on PATH"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The non-island link set: every runtime C file except ps_island.c, which needs
# Python.h. Same rule as link_binary for programs with no ps_island_ symbols.
mapfile -t rt < <(find "$root/src/pyscriptc/runtime" -name '*.c' ! -name 'ps_island.c' | sort)

# 1. non-mix program: -x ir is unsupported by zig cc, .ll is picked up by extension.
zig cc -O2 "$here/hello.ll" "${rt[@]}" -target "$target" -lm -o "$work/hello" 2>/dev/null \
  || fail "zig cc failed on hello.ll"
got="$(cd "$work" && ./hello)" || fail "hello did not run"
[ "$got" = "42" ] || fail "hello printed '$got', expected 42"

# 2. mix program: libmix.so built by zig cc, loaded via PYSCRIPTC_MIX_SO at run time.
zig cc -O2 -shared "$here/mix.c" -target "$target" -o "$work/libmix.so" 2>/dev/null \
  || fail "zig cc failed on mix.c"
zig cc -O2 "$here/mixdemo.ll" "${rt[@]}" -target "$target" -lm -o "$work/mixdemo" 2>/dev/null \
  || fail "zig cc failed on mixdemo.ll"
# zlib.crc32(b"the quick brown fox") == 2445345482
got="$(cd "$work" && PYSCRIPTC_MIX_SO="$work/libmix.so" ./mixdemo)" \
  || fail "mixdemo did not run (PYSCRIPTC_MIX_SO=$work/libmix.so)"
[ "$got" = "2445345482" ] || fail "mixdemo printed '$got', expected 2445345482"

echo "ZIG SMOKE OK ($target, $(zig version))"
