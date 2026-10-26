"""The empty-environment acceptance matrix of issue #661.

The headline test of the embedding slice: a bundle is compiled, the bundle is
relocated to a fresh directory, the pack cache and the source tree are hidden,
and the bundle runs under `env -i` (no PATH, no HOME, no PYTHONHOME). The
run's stdout, stderr and exit code must equal CPython's, byte for byte, for
three programs:

1. a stdlib-only island program (the fractions fixture),
2. a program importing a bundled pure-Python dependency (issue #660),
3. a program reading a staged data file (issue #665).

The loader assertions pin the relative rpath and the absence of an absolute
path into the build tree, and the size gates are the plan's (macOS 40 MB,
Linux 60 MB, uncompressed).

The bundle compile needs the pinned runtime pack, so the whole module is
skipped unless `PYSC_RUNTIME_DIR` points at an unpacked pack (offline) or the
pack is already in the cache. The CI job in `.github/workflows/embed.yml`
runs the same matrix on Linux x86_64 and macOS arm64.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import embed

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "embed"

STDLIB_PROGRAM = FIXTURES / "island_fractions.py"
DEPENDENCY_PROGRAM = FIXTURES / "island_dependency.py"
DATA_PROGRAM = FIXTURES / "datafile"

MACOS_BUNDLE_LIMIT = 40 * 1024 * 1024
LINUX_BUNDLE_LIMIT = 60 * 1024 * 1024


def _pack_is_available() -> bool:
    """Whether the pinned pack is unpacked and usable on this machine."""
    try:
        pack = embed.lock_pack()
    except embed.EmbedError:
        return False
    root = embed.runtime_dir()
    if root is not None:
        return embed.pack_is_ready(pack)
    return embed.pack_is_ready(pack)


pytestmark = pytest.mark.skipif(
    not _pack_is_available(),
    reason="needs the pinned runtime pack (unpacked in the cache or PYSC_RUNTIME_DIR)",
)


def _compile(source: Path, out: Path, *extra: str) -> Path:
    """A bundle compile of `source` into `out`; returns the bundle directory."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            "--emit",
            "bin",
            "--embed-python=bundle",
            *extra,
            str(source),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr
    return out


def _run(binary: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a bundle binary in an empty environment.

    `env={}` is the `env -i` shape: no PATH, no HOME, no PYTHONHOME, no
    VIRTUAL_ENV, nothing the program could inherit from the compiler.
    """
    return subprocess.run(
        [str(binary)],
        env={},
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _reference(script: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """CPython's own run of the same program."""
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_same_as_cpython(got: subprocess.CompletedProcess[str], want: subprocess.CompletedProcess[str]) -> None:
    assert got.stdout == want.stdout, (got.stdout, want.stdout)
    assert got.stderr == want.stderr, (got.stderr, want.stderr)
    assert got.returncode == want.returncode, (got.returncode, want.returncode)


def test_acceptance_matrix_runs_in_an_empty_environment(tmp_path: Path) -> None:
    """The three programs of the matrix, relocated and compared to CPython."""
    work = tmp_path / "work"
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    stdlib_bundle = _compile(STDLIB_PROGRAM, work / "stdlib" / "prog", "--island")
    dependency_bundle = _compile(DEPENDENCY_PROGRAM, work / "sitedep" / "depprog", "--island")
    data_bundle = _compile(DATA_PROGRAM, work / "datafile" / "prog", "--program", "--island", "--fallback=cpython")

    # The build tree goes away: the bundles move to a fresh directory and the
    # compile-time outputs are deleted.
    shutil.move(str(stdlib_bundle), str(fresh / "stdlib"))
    shutil.move(str(dependency_bundle), str(fresh / "sitedep"))
    shutil.move(str(data_bundle), str(fresh / "datafile"))
    shutil.rmtree(work)

    got = _run(fresh / "stdlib" / "bin" / "prog")
    _assert_same_as_cpython(got, _reference(STDLIB_PROGRAM))

    got = _run(fresh / "sitedep" / "bin" / "depprog")
    _assert_same_as_cpython(got, _reference(DEPENDENCY_PROGRAM))

    got = _run(fresh / "datafile" / "bin" / "prog")
    _assert_same_as_cpython(got, _reference(DATA_PROGRAM / "__main__.py", cwd=DATA_PROGRAM))


def test_the_bundle_carries_only_a_relative_rpath(tmp_path: Path) -> None:
    """No absolute path into the build tree survives in the image."""
    bundle = _compile(STDLIB_PROGRAM, tmp_path / "out" / "prog", "--island")
    binary = bundle / "bin" / "prog"
    if sys.platform == "darwin":
        listed = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, timeout=60)
        load = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, timeout=60)
        assert listed.returncode == 0 and load.returncode == 0
        assert "@executable_path/../lib" in load.stdout
        assert "@rpath/libpython" in listed.stdout
        # `otool` prints the binary's own path as its first line; the payload
        # is what may not carry a build path.
        payload = "\n".join(listed.stdout.splitlines()[1:])
    else:
        listed = subprocess.run(["readelf", "-d", str(binary)], capture_output=True, text=True, timeout=60)
        assert listed.returncode == 0
        assert "$ORIGIN/../lib" in listed.stdout
        payload = listed.stdout
    # The build tree path itself never appears in the image.
    assert str(tmp_path) not in payload


def test_the_bundle_stays_inside_the_size_gate(tmp_path: Path) -> None:
    """macOS 40 MB, Linux 60 MB, uncompressed."""
    bundle = _compile(STDLIB_PROGRAM, tmp_path / "out" / "prog", "--island")
    size = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file())
    limit = MACOS_BUNDLE_LIMIT if sys.platform == "darwin" else LINUX_BUNDLE_LIMIT
    assert size <= limit, f"the bundle is {size} bytes, over the {limit} byte gate"


def test_a_missing_pack_is_refused_without_a_traceback(tmp_path: Path) -> None:
    """A pack-less run fails clearly, naming `pyscriptc embed fetch`."""
    empty = tmp_path / "empty"
    empty.mkdir()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            "--island",
            "--embed-python=bundle",
            str(STDLIB_PROGRAM),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "out" / "prog"),
        ],
        env={**os.environ, "PYSC_RUNTIME_DIR": str(empty)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 1
    assert "pyscriptc embed fetch" in done.stderr
    assert "Traceback" not in done.stderr
