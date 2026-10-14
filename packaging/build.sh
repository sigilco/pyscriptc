#!/usr/bin/env bash
# Build a Nuitka onefile of the pyscriptc console entry for THIS host.
# The toolchain is system clang. Nuitka is an ephemeral uv dependency, so
# pyproject.toml and uv.lock stay unchanged. This script does not cross-compile.
set -euo pipefail

packaging_dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$packaging_dir/.." && pwd)
cd "$root"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build the onefile (see packaging/README.md)" >&2
  exit 1
fi
if ! command -v clang >/dev/null 2>&1; then
  echo "system clang is required and was not found on PATH (see packaging/TOOLCHAIN.md)" >&2
  exit 1
fi

case "$(uname -s)" in
  Linux) os_name=linux ;;
  Darwin) os_name=darwin ;;
  *)
    echo "unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac
arch=$(uname -m)
name="pyscriptc-${os_name}-${arch}"

build_dir="$packaging_dir/build"
dist_dir="$packaging_dir/dist"
rm -rf "$build_dir"
mkdir -p "$build_dir" "$dist_dir"

# Nuitka compiles the onefile with system clang. The frozen compiler later
# shells out to `clang` on PATH to link user programs; clang is not bundled.
export CC=clang
export CXX=clang++

# lower.py is large, and cloud agents share the machine. One C compile job
# keeps the Nuitka process from being killed for memory.
jobs=1

# Nuitka warns that package data duplicates the data dir. The data-dir copy
# is the one packed; the runtime sources are identical either way.
uv run --with nuitka python -m nuitka \
  --onefile \
  --clang \
  --lto=no \
  --low-memory \
  --jobs="$jobs" \
  --assume-yes-for-downloads \
  --include-package=pyscriptc \
  --include-package-data=pyscriptc \
  --include-data-dir="$root/src/pyscriptc/runtime=pyscriptc/runtime" \
  --nofollow-import-to=pytest \
  --nofollow-import-to=tests \
  --output-dir="$build_dir" \
  --output-filename="$name" \
  "$packaging_dir/entry.py"

if [[ ! -f "$build_dir/$name" ]]; then
  echo "Nuitka did not write $build_dir/$name" >&2
  find "$build_dir" -maxdepth 3 -type f -print >&2
  exit 1
fi
install -m 0755 "$build_dir/$name" "$dist_dir/$name"
echo "built $dist_dir/$name"
echo "host: ${os_name} ${arch}"
echo "clang: $(clang --version | head -n 1)"
