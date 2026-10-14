#!/usr/bin/env bash
# Release smoke for the Nuitka onefile on this host.
# The onefile self-extracts when it starts. The smoke then compiles
# examples/add.py, runs it, and compares stdout to CPython 3.12.
set -euo pipefail

packaging_dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$packaging_dir/.." && pwd)

case "$(uname -s)" in
  Linux) os_name=linux ;;
  Darwin) os_name=darwin ;;
  *)
    echo "unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac
arch=$(uname -m)
bin="$packaging_dir/dist/pyscriptc-${os_name}-${arch}"

if [[ ! -x "$bin" ]]; then
  echo "missing release binary: $bin" >&2
  echo "build it with packaging/build.sh" >&2
  exit 1
fi
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 is required for the oracle" >&2
  exit 1
fi
python3.12 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
if ! command -v clang >/dev/null 2>&1; then
  echo "system clang is required to link the compiled program" >&2
  exit 1
fi

func=add
args='[1, 2]'
source="$root/examples/add.py"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Launching the onefile is the unpack step: Nuitka onefile self-extracts
# its payload and then runs this entry. A successful --help means that happened.
if ! "$bin" --help >"$work/help.txt" 2>"$work/help.err"; then
  echo "release binary failed to start (onefile unpack or entry failed)" >&2
  cat "$work/help.err" >&2
  exit 1
fi

# The frozen interpreter's sys.executable is the onefile itself, so the
# mypy preflight (sys.executable -m mypy) cannot run. --skip-check exercises
# lower, LLVM emit, system clang link, and --run.
if ! "$bin" compile "$source" \
  --run \
  --call "$func" \
  --args "$args" \
  --skip-check \
  -o "$work/add" \
  >"$work/compiled.txt" \
  2>"$work/compiled.err"; then
  echo "release compile failed" >&2
  cat "$work/compiled.err" >&2
  exit 1
fi

python3.12 "$packaging_dir/smoke/oracle_add.py" "$source" "$func" "$args" >"$work/cpython.txt"

if ! cmp -s "$work/compiled.txt" "$work/cpython.txt"; then
  echo "stdout mismatch against CPython 3.12" >&2
  echo "compiled:" >&2
  cat >&2 "$work/compiled.txt"
  echo "cpython 3.12:" >&2
  cat >&2 "$work/cpython.txt"
  echo "compiler stderr:" >&2
  cat >&2 "$work/compiled.err"
  exit 1
fi

echo "smoke ok: examples/add.py ${func}${args} matches CPython 3.12"
echo "host: ${os_name} ${arch}"
echo "binary: $bin"
echo "stdout:"
cat "$work/compiled.txt"
