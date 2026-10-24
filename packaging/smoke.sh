#!/usr/bin/env bash
# Release smoke test: run the built binary, compile a program, run it, compare with CPython 3.12.
#
#   packaging/smoke.sh [BINARY] [SOURCE.py] [FUNCTION] [ARGS_LITERAL]
#
# Defaults: BINARY=packaging/dist/pyscriptc-<os>-<arch>, SOURCE=packaging/smoke/hello.py (a fixed
# fixture that defines hello(n); examples/hello.py is a first-run demo and may change),
# FUNCTION=hello, ARGS=[20].
# The binary is copied to an empty temp directory and run from there, so nothing from the source
# tree (no src/, no venv) can leak into the result. Needs clang on PATH and `uv` (for CPython 3.12).
# Exit 0 on a match, 1 on any mismatch or failure.
# Windows (git bash): default binary packaging/dist/pyscriptc-windows-x86_64.exe.
# Experimental. explain and --help only, under env -i. No compile or run:
# link_binary compiles every runtime/*.c into each emitted program and ps_mix.c
# needs dlfcn.h/dlopen, which MinGW-w64 does not provide (dirent.h and unistd.h
# are also POSIX-only). compile+run would need a src/pyscriptc change.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
uname_s="$(uname -s)"
case "$uname_s" in
  Darwin) os=macos ;;
  Linux) os=linux ;;
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  *) os=unknown ;;
esac
arch="$(uname -m)"
if [ "$os" = windows ]; then
  bin="${1:-$here/dist/pyscriptc-windows-x86_64.exe}"
else
  bin="${1:-$here/dist/pyscriptc-$os-$arch}"
fi
src="${2:-$here/smoke/hello.py}"
func="${3:-hello}"
fargs="${4:-[20]}"

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
[ -f "$bin" ] || fail "binary not found: $bin"
if [ "$os" = windows ]; then
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir "$work/run"
  cp "$bin" "$work/run/pyscriptc.exe"
  (cd "$work/run" && env -i PATH="$PATH" HOME="$work" ./pyscriptc.exe explain PS2002 >/dev/null) \
    || fail "binary did not start (explain PS2002 failed)"
  (cd "$work/run" && env -i PATH="$PATH" HOME="$work" ./pyscriptc.exe --help >/dev/null) \
    || fail "binary did not answer --help"
  echo "SMOKE OK (windows explain-only, $(basename "$bin"))"
  exit 0
fi
[ -x "$bin" ] || fail "binary not found or not executable: $bin"
command -v clang >/dev/null || fail "clang is not on PATH (see packaging/TOOLCHAIN.md)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/run" "$work/ref"
cp "$bin" "$work/run/pyscriptc"
cp "$src" "$work/run/prog.py"
cp "$src" "$work/ref/prog.py"

# 1. the binary starts and answers without a Python install
(cd "$work/run" && env -i PATH="$PATH" HOME="$work" ./pyscriptc explain PS2002 >/dev/null) \
  || fail "binary did not start (explain PS2002 failed)"

# 2. compile and run, preflight on. The -o target must be absolute: cli.py runs the
# built program with `subprocess.run([str(binary)])`, and a bare name with no path
# separator is resolved through PATH, never through the cwd.
got="$(cd "$work/run" && env -i PATH="$PATH" HOME="$work" ./pyscriptc compile prog.py --run --call "$func" --args "$fargs" -o "$work/run/prog.bin" 2>"$work/stderr.txt")" \
  || { cat "$work/stderr.txt" >&2; fail "compile or run failed (exit $?)"; }
if [ -z "$got" ]; then
  echo "--- stderr from compile --run (empty stdout) ---" >&2
  cat "$work/stderr.txt" >&2
  ls -la "$work/run" >&2
  echo "--- rerun with --debug=all ---" >&2
  rc=0
  (cd "$work/run" && env -i PATH="$PATH" HOME="$work" ./pyscriptc compile prog.py --run --call "$func" --args "$fargs" -o "$work/run/prog.bin" --debug=all) >&2 || rc=$?
  echo "exit code: $rc" >&2
fi

# 3. the same function under CPython 3.14
want="$(cd "$work/ref" && uv run --no-project --python 3.14 python -I -c '
import ast, importlib.util, sys
spec = importlib.util.spec_from_file_location("prog", "prog.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(getattr(mod, sys.argv[1])(*ast.literal_eval(sys.argv[2])))
' "$func" "$fargs")" || fail "CPython 3.14 reference run failed"

echo "binary : $got"
echo "cpython: $want"
[ "$got" = "$want" ] || fail "stdout differs"
echo "SMOKE OK ($(basename "$bin"), $(basename "$src"))"
