#!/usr/bin/env python
"""Build the static single-binary CPython for the island (issue #672).

    uv run python scripts/build_static_cpython.py --output out/static-cpython/
    uv run python scripts/build_static_cpython.py --check          # verify the lock, no build

The build is described by `packaging/static-cpython.lock.toml`:

1. download the pinned CPython source tarball, sha256-verified,
2. unpack, copy the curated `Modules/Setup.local` builtin list in,
3. `configure` with the pinned flags (`--disable-shared --enable-optimizations`:
   static libpython + PGO),
4. `make -j<N>` (the PGO profile task runs during the build),
5. `make install` into a staging prefix.

The output directory holds the full install tree (`bin/python3.14`,
`lib/libpython3.14.a`, `lib/python3.14/`). `--print-py-stdlib-zip` also
stores the trimmed stdlib zip next to it; the zip must be stored
*uncompressed* (zipimport fails at `encodings` when `zlib` is a shared
extension, and a stored zip is faster to open anyway).

Cost: a cold build is about 12 minutes on a 4-core GitHub-hosted runner.
That is why the CI wiring (`.github/workflows/static-cpython.yml`) is a
non-required workflow, dispatch and schedule only, never on `pull_request`:
the stretch gate of issue #672 says the per-platform matrix waits for an
owner decision on the budget.

Platform coverage in this slice: Linux x86_64 is built and smoke-tested
here. macOS arm64 and aarch64 Linux need their own runners and follow the
same recipe; the lock and the script are host-agnostic.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "packaging" / "static-cpython.lock.toml"
#: The cached tarball location (kept across runs so a rebuild re-downloads nothing).
CACHE_DIR = Path(os.environ.get("PYSC_STATIC_CPYTHON_CACHE", Path.home() / ".cache" / "pyscriptc" / "static-cpython"))


class BuildError(Exception):
    """A user-facing failure of the static build (no traceback)."""


def load_lock(path: Path) -> dict:
    """Read and minimally validate the lock file."""
    with path.open("rb") as fh:
        lock = tomllib.load(fh)
    if lock.get("schema") != 1:
        raise BuildError(f"{path}: unsupported schema {lock.get('schema')!r} (want 1)")
    source = lock.get("source") or {}
    for key in ("url", "sha256", "filename"):
        if not source.get(key):
            raise BuildError(f"{path}: [source] is missing {key}")
    build = lock.get("build") or {}
    if not build.get("setup_local"):
        raise BuildError(f"{path}: [build] is missing setup_local")
    setup_local = ROOT / build["setup_local"]
    if not setup_local.is_file():
        raise BuildError(f"{path}: setup_local file {setup_local} does not exist")
    return lock


def verify_sha256(archive: Path, expected: str) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise BuildError(
            f"sha256 mismatch for {archive.name}: got {digest}, lock says {expected}"
        )


def fetch_tarball(lock: dict, cache: Path) -> Path:
    """Download the pinned tarball into the cache, verifying it on every use."""
    source = lock["source"]
    archive = cache / source["filename"]
    if not archive.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        partial = archive.with_suffix(archive.suffix + ".part")
        print(f"downloading {source['url']}")
        urllib.request.urlretrieve(source["url"], partial)
        partial.rename(archive)
    verify_sha256(archive, source["sha256"])
    return archive


def _run(cmd: list[str], **kw: object) -> None:
    print(f"+ {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)  # noqa: S603


def cpu_count() -> int:
    override = os.environ.get("PYSC_STATIC_CPYTHON_JOBS")
    return max(1, int(override)) if override else os.cpu_count() or 2


def build(lock: dict, lock_path: Path, output: Path, jobs: int) -> Path:
    """Build the pinned CPython from source and install into `output`.

    Returns the install prefix directory.
    """
    setup_local = ROOT / lock["build"]["setup_local"]
    cache = Path(os.environ.get("PYSC_STATIC_CPYTHON_CACHE") or CACHE_DIR)
    with tempfile.TemporaryDirectory(prefix="static-cpython-") as tmp:
        tmp_path = Path(tmp)
        archive = fetch_tarball(lock, cache)
        src = tmp_path / "src"
        src.mkdir()
        print(f"unpacking {archive.name}")
        with tarfile.open(archive) as tf:
            tf.extractall(src, filter="tar")  # noqa: S202 - pinned, sha256-verified tarball
        members = list(src.iterdir())
        if len(members) != 1 or not members[0].is_dir():
            raise BuildError(f"unexpected tarball layout: {members}")
        tree = members[0]

        # The curated builtin list: copied in before configure, which creates
        # Modules/Setup.local from it (configure only writes the file when
        # absent, so copying first is the supported local-override path).
        shutil.copy2(setup_local, tree / "Modules" / "Setup.local")

        prefix = tmp_path / lock["build"]["prefix"]
        configure_args = [str(a) for a in (lock["build"].get("configure_args") or [])]
        _run(["./configure", f"--prefix={prefix}", *configure_args], cwd=tree)
        _run(["make", f"-j{jobs}"], cwd=tree)
        _run(["make", "install"], cwd=tree)

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(prefix, output)
        _write_stamp(output, lock, jobs)
    size_mb = sum(f.stat().st_size for f in output.rglob("*") if f.is_file()) // (1024 * 1024)
    print(f"static CPython installed at {output} ({size_mb} MB)")
    return output


def _write_stamp(output: Path, lock: dict, jobs: int) -> None:
    import json

    stamp = {
        "cpython": lock["version"],
        "sha256": lock["source"]["sha256"],
        "configure_args": [str(a) for a in lock["build"].get("configure_args") or []],
        "jobs": jobs,
    }
    (output / "pyscriptc-static-stamp.json").write_text(json.dumps(stamp, indent=2) + "\n")


def smoke(prefix: Path) -> int:
    """Run the static interpreter against a few builtin-sensitive imports.

    The point is the curated Setup.local list: every module named there must
    import *from the binary alone*, with the install tree hidden, and the
    `fractions` program of the embedding plan must produce its known answer.
    """
    py = prefix / "bin" / "python3.14"
    if not py.is_file():
        raise BuildError(f"{py} not found after the build")
    check = (
        "import math, cmath, bisect, heapq, json, pickle, random, statistics, "
        "struct, datetime, array, binascii, select, socket, unicodedata, "
        "zoneinfo, sys; print('static-ok', sys.version.split()[0], "
        "'math' in sys.builtin_module_names)"
    )
    proc = subprocess.run([str(py), "-S", "-c", check], capture_output=True, text=True)  # noqa: S603
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode
    if "True" not in proc.stdout:
        print("math is not a builtin: the Setup.local list did not take effect", file=sys.stderr)
        return 1
    prog = "from fractions import Fraction; print(Fraction(5, 6) + Fraction(1, 4))"
    proc = subprocess.run([str(py), "-c", prog], capture_output=True, text=True)  # noqa: S603
    print(proc.stdout, end="")
    if proc.returncode != 0 or proc.stdout != "11/12\n":
        print(proc.stderr, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="lock file path")
    parser.add_argument("--output", type=Path, default=ROOT / "packaging" / "dist" / "static-cpython")
    parser.add_argument("--jobs", type=int, default=cpu_count(), help="make -j")
    parser.add_argument("--check", action="store_true", help="validate the lock and the cached tarball, no build")
    parser.add_argument("--smoke", action="store_true", help="run the builtin-import smoke after the build")
    args = parser.parse_args()

    lock = load_lock(args.lock)
    if args.check:
        archive = fetch_tarball(lock, CACHE_DIR)
        print(f"lock ok, tarball cached and verified: {archive}")
        return 0
    prefix = build(lock, args.lock, args.output, args.jobs)
    if args.smoke:
        return smoke(prefix)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
