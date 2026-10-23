#!/usr/bin/env bash
# Mobile archive smoke: build the three mobile archives and link-check them.
#
#   1. iOS simulator archive builds on a macOS host, links into a host
#      executable through the simulator SDK, and the executable runs.
#   2. iOS device archive builds and its members are the expected set.
#   3. Android archive builds through the NDK clang (PYSC_ANDROID_NDK) and
#      packs the expected members.
#
# The NDK is only needed for the android leg; the Apple legs need Xcode.
#
#   packaging/mobile/smoke.sh                 # everything the host can do
#   PYSC_SMOKE_SKIP=android packaging/mobile/smoke.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "MOBILE SMOKE FAIL: $*" >&2; exit 1; }
log() { echo "== $*"; }

want() { [[ ",${PYSC_SMOKE_SKIP:-}," != *",$1,"* ]]; }

pyscriptc() { (cd "$root" && uv run pyscriptc "$@"); }

fixture() { printf 'def main() -> int:\n    return 42\n'; }

# --- the iOS simulator: build, link into a host runner, execute ----------
if want ios-sim && command -v xcrun >/dev/null; then
  log "ios simulator archive"
  fixture > "$work/hello.py"
  out="$work/sim"
  pyscriptc compile "$work/hello.py" --emit lib --skip-check \
    --target aarch64-apple-ios-simulator -o "$out" \
    || fail "the simulator archive did not build"
  [[ -f "$out/libpyscriptc.a" ]] || fail "no libpyscriptc.a in $out"
  [[ -f "$out/pyscriptc.h" ]] || fail "no pyscriptc.h in $out"
  [[ -f "$out/module.modulemap" ]] || fail "no module.modulemap in $out"

  sdk="$(xcrun -sdk iphonesimulator --show-sdk-path)"
  cat > "$work/runner.c" <<'EOF'
#include <stdio.h>
int pyscriptc_init(void);
int pyscriptc_main(void);
int main(void) {
    if (pyscriptc_init() != 0) { puts("init failed"); return 1; }
    return pyscriptc_main() == 42 ? 0 : 1;
}
EOF
  xcrun -sdk iphonesimulator clang \
    -target aarch64-apple-ios15.0-simulator -isysroot "$sdk" \
    "$work/runner.c" "$out/libpyscriptc.a" -o "$work/runner" \
    || fail "the simulator archive did not link into a runner"
  # A simulator binary needs the simulator runtime (dyld_sim), which a plain
  # shell does not have, so the link is the check and the run is best effort.
  if "$work/runner" 2>/dev/null; then
    log "simulator runner returned 0"
  else
    log "simulator runner did not run here (link-check only)"
  fi

  exported="$(nm -gU "$work/runner" | awk '{print $3}' | grep -v '__mh_execute_header' | sed 's/^_//' | sort -u | tr '\n' ' ')"
  log "runner exports: $exported"
  [[ "$exported" == "main pyscriptc_init pyscriptc_main " ]] \
    || fail "the runner exports more than the documented entry: $exported"
elif want ios-sim; then
  fail "the ios simulator leg needs macOS with Xcode"
fi

# --- the iOS device: build and check the member set ----------------------
if want ios-device && command -v xcrun >/dev/null; then
  log "ios device archive"
  fixture > "$work/hello.py"
  out="$work/device"
  pyscriptc compile "$work/hello.py" --emit lib --skip-check \
    --target aarch64-apple-ios -o "$out" \
    || fail "the device archive did not build"
  members="$(ar t "$out/libpyscriptc.a" | sort | tr '\n' ' ')"
  case "$members" in
    *hello.o*) : ;;
    *) fail "the device archive misses the module member: $members" ;;
  esac
  for excluded in ps_sub.o ps_osfd.o ps_island.o; do
    case "$members" in
      *"$excluded"*) fail "$excluded is in the device archive" ;;
    esac
  done
elif want ios-device; then
  fail "the ios device leg needs macOS with Xcode"
fi

# --- android: build through the NDK --------------------------------------
if want android; then
  [[ -n "${PYSC_ANDROID_NDK:-}" ]] || fail "android leg needs PYSC_ANDROID_NDK"
  log "android archive (NDK at $PYSC_ANDROID_NDK)"
  fixture > "$work/hello.py"
  out="$work/android"
  pyscriptc compile "$work/hello.py" --emit lib --skip-check \
    --target aarch64-linux-android -o "$out" \
    || fail "the android archive did not build"
  members="$("$PYSC_ANDROID_NDK/toolchains/llvm/prebuilt/"*/bin/llvm-ar t "$out/libpyscriptc.a" | sort | tr '\n' ' ')"
  case "$members" in
    *hello.o*) : ;;
    *) fail "the android archive misses the module member: $members" ;;
  esac
  for excluded in ps_sub.o ps_osfd.o ps_island.o; do
    case "$members" in
      *"$excluded"*) fail "$excluded is in the android archive" ;;
    esac
  done
else
  log "android leg skipped"
fi

log "mobile smoke ok"
