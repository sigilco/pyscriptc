#!/usr/bin/env bash
# Regenerate the committed .ll fixtures for packaging/zig/smoke.sh.
# Standalone emissions (--emit bin --call) produced by the real compiler.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv run pyscriptc compile "$here/hello.py" --emit bin --call add --args '[20, 22]' --skip-check -o "$work/hello" >/dev/null
uv run pyscriptc compile "$here/mixdemo.py" --ffi "$root/benchmarks/native/nativeprobe.toml" --emit bin --call digest --args '[]' --skip-check -o "$work/mixdemo" >/dev/null
cp "$work/hello.ll" "$work/mixdemo.ll" "$here/"
echo "wrote $here/hello.ll $here/mixdemo.ll"
