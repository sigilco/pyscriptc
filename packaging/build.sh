#!/usr/bin/env bash
# Build the pyscriptc release binary with Nuitka onefile.
#
#   packaging/build.sh                 build for the host OS and arch
#   packaging/build.sh --check-env     print the detected target and tool versions, build nothing
#
# Output: packaging/dist/pyscriptc-<os>-<arch>[.exe]
#   os: macos|linux|windows, arch: arm64|aarch64|x86_64
# Run it where the target runs. Nuitka does not cross compile, so each target is built on
# its own OS and arch (linux targets inside the Linux dev container or a native Linux host).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"

uname_s="$(uname -s)"
case "$uname_s" in
  Darwin) os=macos ;;
  Linux) os=linux ;;
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  *) echo "unsupported OS: $uname_s" >&2; exit 2 ;;
esac
arch="$(uname -m)"
case "$os-$arch" in
  macos-arm64|macos-x86_64|linux-aarch64|linux-x86_64|windows-x86_64) ;;
  *)
    echo "unsupported target $os-$arch (documented: macos-arm64, macos-x86_64, linux-aarch64, linux-x86_64, windows-x86_64)" >&2
    exit 2
    ;;
esac
# venv directory stays free of the .exe suffix so it is not confused with the artifact.
stem="pyscriptc-$os-$arch"
if [ "$os" = windows ]; then
  name="$stem.exe"
else
  name="$stem"
fi

if [ "${1:-}" = "--check-env" ]; then
  echo "target: $name"
  command -v clang >/dev/null && clang --version | head -1 || echo "clang: MISSING (needed at run time, not build time)"
  command -v uv >/dev/null && uv --version || echo "uv: MISSING"
  exit 0
fi

cd "$root"
# One venv per target under packaging/build/ so a Linux container run and a macOS run never share
# an interpreter, and the dev venv is left alone.
venv="${UV_PROJECT_ENVIRONMENT:-$here/build/venv-$stem}"
export UV_PROJECT_ENVIRONMENT="$venv"
if [ "$os" = linux ] && ! command -v patchelf >/dev/null; then
  echo "patchelf is required for Nuitka onefile on Linux (apt-get install patchelf)" >&2
  exit 2
fi
uv sync --group packaging --quiet

out="$here/dist"
work="$here/build"
mkdir -p "$out" "$work"

# --include-package-data=mypy carries mypy's xml/ and misc data but silently drops every
# .pyi stub, so the mypy preflight dies with 'Failed to find builtin module
# "_collections_abc", perhaps typeshed is broken?'. Ship typeshed as an explicit data dir.
mypy_pkg="$(uv run --group packaging python -c 'import os, mypy; print(os.path.dirname(mypy.__file__))')"

# pyrefly is the default preflight and is a native executable, not a Python module, so the
# onefile cannot reach it through `sys.executable -m pyrefly`. Ship it as a data file under
# bin/; entry.py points PYREFLY_BIN at the extracted copy.
pyrefly_exe="$(uv run --group packaging python -c 'import os, sysconfig; print(os.path.join(sysconfig.get_path("scripts"), "pyrefly" + (sysconfig.get_config_var("EXE") or "")))')"
[ -f "$pyrefly_exe" ] || { echo "pyrefly executable not found at $pyrefly_exe" >&2; exit 1; }
pyrefly_dest=bin/pyrefly
[ "$os" = windows ] && pyrefly_dest=bin/pyrefly.exe

# Nuitka does not understand MSYS-style paths (/d/a/...) in --include-data-files
# globs: "does not match any files". Pass native Windows paths on Windows.
nroot="$root"
nwork="$work"
if [ "$os" = windows ]; then
  nroot="$(cygpath -w "$root")"
  nwork="$(cygpath -w "$work")"
  pyrefly_exe="$(cygpath -w "$pyrefly_exe")"
fi

# Data files the compiler reads at run time: the C runtime it links beside every program.
# llvm.py globs runtime/*.c next to its own __file__, so they must sit at pyscriptc/runtime/.
# mypy needs its bundled typeshed stubs for the preflight.
args=(
  --onefile
  --assume-yes-for-downloads
  --output-dir="$nwork"
  --output-filename="$name"
  --include-package=pyscriptc
  --include-package=mypy
  --include-package-data=mypy
  "--include-data-files=$mypy_pkg/typeshed=mypy/typeshed/=**/*.pyi"
  "--include-data-files=$pyrefly_exe=$pyrefly_dest"
  "--include-data-files=$nroot/src/pyscriptc/runtime/*.c=pyscriptc/runtime/"
  "--include-data-files=$nroot/src/pyscriptc/runtime/*.h=pyscriptc/runtime/"
  --nofollow-import-to=pytest
  --nofollow-import-to=tkinter
  --python-flag=no_docstrings
  --remove-output
  # check.py runs `sys.executable -m mypy`; entry.py answers that call, so allow the self call.
  --no-deployment-flag=self-execution
)
# Small hosts (the 2 GB dev container) can run out of memory compiling lower.py and mypy.
if [ "${NUITKA_LOW_MEMORY:-0}" = 1 ]; then args+=(--low-memory); fi
if [ -n "${NUITKA_JOBS:-}" ]; then args+=("--jobs=$NUITKA_JOBS"); fi
if [ "$os" = macos ]; then
  args+=(--macos-target-arch="$arch")
fi
if [ "$os" = windows ]; then
  # Experimental artifact. Nuitka on Python 3.13+ rejects MinGW64 unless this
  # switch is set. --assume-yes-for-downloads (above) fetches that toolchain.
  # Emitted programs still need POSIX headers and are out of scope.
  args+=(--mingw64 --experimental=force-mingw64)
fi

PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" \
  uv run --group packaging python -m nuitka "${args[@]}" "$here/entry.py"

mv -f "$work/$name" "$out/$name"
chmod +x "$out/$name"
echo "built $out/$name ($(du -h "$out/$name" | cut -f1))"
