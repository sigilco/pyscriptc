#!/usr/bin/env bash
# Wheel install smoke: build the wheel, install it into a fresh venv, and
# compile and run a tiny program with the repo venv and pyrefly dirs off PATH.
# Same shape as the wheel-smoke job in ci.yml and the verify job in
# release.yml. Run it through mise: `mise run wheel:smoke`.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cd "$root"

uv build
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv venv "$work/venv"
uv pip install --python "$work/venv/bin/python" dist/pyscriptc-*-any.whl
printf 'def main() -> None:\n    print("hello")\n' > "$work/hello.py"

# Repo venv and pyrefly dirs are off PATH on purpose: the wheel is the only
# source of the compiler.
env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/local/bin \
  "$work/venv/bin/pyscriptc" compile "$work/hello.py" --emit bin -o "$work/hello-bin"
out="$("$work/hello-bin")"
echo "$out"
[ "$out" = "hello" ]
