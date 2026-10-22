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
#   4. manifest: the cross matrix builds hello.ll again with one `[[source]]`
#      unit from packaging/zig/ffiprobe/ffi.toml (the F2 static-link path,
#      issue #220); x86_64-musl runs it and prints 63.
#   5. sqlite pilot: the cross matrix builds sqlite.ll with the vendored
#      amalgamation and the adapter thunk (the F3 pilot, issue #66);
#      x86_64-musl runs it and prints the two rows. The amalgamation is
#      fetched and hash-pinned by fetch_vendor.py; offline hosts skip.
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
# Python.h, and the wasm-only runtime/wasm/rt_wasm.c (a wasm32 EH setjmp shim,
# not part of the tier-A link set). Same rule as link_binary for programs with
# no ps_island_ symbols.
mapfile -t rt < <(find "$root/src/pyscriptc/runtime" -maxdepth 1 -name '*.c' ! -name 'ps_island.c' | sort)

# build <ll> <target> <out> [extra C units...]: every unit to .o, then objects
# link. C sources and -O on the link line both crash zig's Mach-O driver on
# 0.16. The extra units are the manifest `[[source]]` entries: they ride the
# same compile loop and the same link line, no second link shape (issue #220).
# The units compile with the manifest's own include dirs, like the compiler's
# per-unit loop: the runtime dir (ps_rt.h) plus any dir after a `--include`.
build() {
    local ll="$1" target="$2" out="$3"
    shift 3
    local includes=()
    while [ "${1:-}" = "--include" ]; do
        includes+=("-I" "$2")
        shift 2
    done
    local objects=()
    local unit name
    for unit in "$ll" "${rt[@]}" "$@"; do
        name="$(basename "$unit")"
        if [ ${#includes[@]} -gt 0 ]; then
            zig cc -O2 "${includes[@]}" -c "$unit" -target "$target" -o "$work/${name%.*}.o" 2>/dev/null \
                || fail "zig cc -c failed on $unit ($target)"
        else
            zig cc -O2 -c "$unit" -target "$target" -o "$work/${name%.*}.o" 2>/dev/null \
                || fail "zig cc -c failed on $unit ($target)"
        fi
        objects+=("$work/${name%.*}.o")
    done
    zig cc "${objects[@]}" -target "$target" -lm -o "$out" 2>/dev/null \
        || fail "zig cc failed on $ll ($target)"
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

# 4. the manifest-built unit in the cross matrix (issue #220). ffi.c comes from
# the `[[source]]` entry of packaging/zig/ffiprobe/ffi.toml, so it is compiled
# with the target cc and linked statically, on the same link line as the
# runtime. Every target compiles it; the ones with a runner also run it.
for entry in "${matrix[@]}"; do
    IFS='|' read -r name target runner <<<"$entry"
    out="$work/ffiprobe-$name"
    build "$here/ffiprobe.ll" "$target" "$out" "$here/ffiprobe/ffi.c"
    [ -s "$out" ] || fail "empty manifest output for $name"
    case "$runner" in
        native)
            got="$("$out")" || fail "manifest $name did not run"
            [ "$got" = "63" ] || fail "manifest $name printed '$got', expected 63" ;;
        qemu-*)
            if command -v "$runner" >/dev/null; then
                got="$("$runner" "$out")" || fail "manifest $name did not run under $runner"
                [ "$got" = "63" ] || fail "manifest $name printed '$got' under $runner, expected 63"
            else
                echo "note: $runner not installed, manifest $name is compile-only" >&2
            fi ;;
    esac
    echo "ok: manifest $name ($target)"
done

# 5. the sqlite pilot in the cross matrix (issue #66). The vendored
# amalgamation and the adapter thunk are the manifest's own `[[source]]`
# units; the thunk includes "ps_rt.h" and "vendor/sqlite3.h", so the adapter
# directory is on the include path. The amalgamation is fetched by
# fetch_vendor.py and hash-pinned; without it the pilot is skipped, so an
# offline host still passes the rest of the smoke.
adapter="$root/packaging/adapters/pyscriptc-sqlite"
# the pilot prints one name per row, two rows: ada then bo
rows=$'ada\nbo'
if [ -f "$adapter/vendor/sqlite3.c" ] && [ -f "$adapter/vendor/sqlite3.h" ]; then
    for entry in "${matrix[@]}"; do
        IFS='|' read -r name target runner <<<"$entry"
        out="$work/sqlite-$name"
        build "$here/sqlite.ll" "$target" "$out" --include "$root/src/pyscriptc/runtime" --include "$adapter" \
            "$adapter/vendor/sqlite3.c" "$adapter/ps_ffi_sqlite.c" \
            || fail "sqlite $name did not build"
        case "$runner" in
            native)
                got="$($out)" || fail "sqlite $name did not run"
                [ "$got" = "$rows" ] || fail "sqlite $name printed '$got', expected two rows" ;;
            qemu-*)
                if command -v "$runner" >/dev/null; then
                    got="$($runner "$out")" || fail "sqlite $name did not run under $runner"
                    [ "$got" = "$rows" ] || fail "sqlite $name printed '$got' under $runner, expected two rows"
                else
                    echo "note: $runner not installed, sqlite $name is compile-only" >&2
                fi ;;
        esac
        echo "ok: sqlite $name ($target)"
    done
else
    echo "note: vendored sqlite amalgamation missing, run packaging/adapters/pyscriptc-sqlite/fetch_vendor.py; sqlite pilot skipped" >&2
fi

echo "ZIG SMOKE OK ($(zig version), tier-A matrix ${#matrix[@]} targets)"
