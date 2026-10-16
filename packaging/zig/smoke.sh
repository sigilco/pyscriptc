#!/usr/bin/env bash
# zig cc smoke: prove emitted LLVM IR plus the C runtime builds and runs under
# `zig cc` for the tier-A cross matrix. Three checks:
#
#   1. run:  hello.ll + all runtime/*.c (minus ps_island.c) -> prints 42
#   2. mix:  mixdemo.ll + runtime + libmix.so built by zig cc -> prints the
#      ISO-HDLC crc32 of the payload (dlopen through the glibc loader)
#   3. cross matrix: build hello.ll + runtime for each tier-A target. Static
#      musl binaries run natively (x86_64) or under qemu-<arch>-static when it
#      is installed; gnu/macOS binaries are compile-only (no sysroot / runner).
#
# The .ll files are standalone emissions (compiled with --emit bin --call).
# Regenerate with: packaging/zig/emit.sh
#
#   packaging/zig/smoke.sh   # needs `zig` on PATH
#
# .ll is never passed on the link line: zig 0.16 writes an empty output for
# macOS in that shape, so every build goes .ll -> .o -> link. Not covered:
# Windows targets (POSIX runtime gaps: dlfcn.h, lstat, gmtime_r), musl mix
# (static musl cannot dlopen), island programs (need a cross-built libpython).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"

fail() { echo "ZIG SMOKE FAIL: $*" >&2; exit 1; }
command -v zig >/dev/null || fail "zig is not on PATH"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The non-island link set: every runtime C file except ps_island.c, which needs
# Python.h. Same rule as link_binary for programs with no ps_island_ symbols.
mapfile -t rt < <(find "$root/src/pyscriptc/runtime" -name '*.c' ! -name 'ps_island.c' | sort)

# build <ll> <target> <out>: every unit to .o, then objects link. C sources
# and -O on the link line both crash zig's Mach-O driver on 0.16.
build() {
    local objects=()
    local unit name
    for unit in "$1" "${rt[@]}"; do
        name="$(basename "$unit")"
        zig cc -O2 -c "$unit" -target "$2" -o "$work/${name%.*}.o" 2>/dev/null \
            || fail "zig cc -c failed on $unit ($2)"
        objects+=("$work/${name%.*}.o")
    done
    zig cc "${objects[@]}" -target "$2" -lm -o "$3" 2>/dev/null \
        || fail "zig cc failed on $1 ($2)"
}

# 1. non-mix program, host glibc: builds and prints 42.
build "$here/hello.ll" "x86_64-linux-gnu.2.39" "$work/hello"
got="$(cd "$work" && ./hello)" || fail "hello did not run"
[ "$got" = "42" ] || fail "hello printed '$got', expected 42"

# 2. mix program: libmix.so built by zig cc, loaded via PYSCRIPTC_MIX_SO.
zig cc -O2 -shared "$here/mix.c" -target "x86_64-linux-gnu.2.39" -o "$work/libmix.so" 2>/dev/null \
  || fail "zig cc failed on mix.c"
build "$here/mixdemo.ll" "x86_64-linux-gnu.2.39" "$work/mixdemo"
# zlib.crc32(b"the quick brown fox") == 2445345482
got="$(cd "$work" && PYSCRIPTC_MIX_SO="$work/libmix.so" ./mixdemo)" \
  || fail "mixdemo did not run (PYSCRIPTC_MIX_SO=$work/libmix.so)"
[ "$got" = "2445345482" ] || fail "mixdemo printed '$got', expected 2445345482"

# 3. tier-A cross matrix. name|target|runner; runner "" = compile-only.
matrix=(
    "aarch64-gnu|aarch64-linux-gnu.2.39|"
    "riscv64-gnu|riscv64-linux-gnu.2.39|"
    "x86_64-musl|x86_64-linux-musl|native"
    "aarch64-musl|aarch64-linux-musl|qemu-aarch64-static"
    "riscv64-musl|riscv64-linux-musl|qemu-riscv64-static"
    "aarch64-macos|aarch64-macos|"
    "x86_64-macos|x86_64-macos|"
)
for entry in "${matrix[@]}"; do
    IFS='|' read -r name target runner <<<"$entry"
    out="$work/hello-$name"
    build "$here/hello.ll" "$target" "$out"
    [ -s "$out" ] || fail "empty output for $name"
    case "$runner" in
        native)
            got="$("$out")" || fail "$name did not run"
            [ "$got" = "42" ] || fail "$name printed '$got', expected 42" ;;
        qemu-*)
            if command -v "$runner" >/dev/null; then
                got="$("$runner" "$out")" || fail "$name did not run under $runner"
                [ "$got" = "42" ] || fail "$name printed '$got' under $runner, expected 42"
            else
                echo "note: $runner not installed, $name is compile-only" >&2
            fi ;;
    esac
    echo "ok: $name ($target)"
done

echo "ZIG SMOKE OK ($(zig version), tier-A matrix ${#matrix[@]} targets)"
