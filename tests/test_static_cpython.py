"""The static single-binary CPython pin (issue #672).

The static island interpreter is built from the pinned CPython *source*
tarball, not from a prebuilt archive: the python-build-standalone
`pgo+lto-full` archives ship an `libpython3.14.a` that is LLVM bitcode,
which Apple ld and zig cannot link. These tests pin the lock (sha256, flag
set, curated builtin list) and the behaviour of the build script that
consumes it. The build itself is not run here: it is a 12 minute source
build, exercised by the script's `--smoke` and by the non-required
`.github/workflows/static-cpython.yml` workflow.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging" / "static-cpython.lock.toml"
SETUP_LOCAL = ROOT / "packaging" / "Setup.local"
SCRIPT = ROOT / "scripts" / "build_static_cpython.py"


def _load_script():
    """Import the build script by path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location("build_static_cpython", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_static_cpython"] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


def test_lock_pins_the_source_tarball() -> None:
    lock = script.load_lock(LOCK)
    assert lock["schema"] == 1
    assert lock["version"].startswith("3.14.")
    assert lock["source"]["url"].startswith("https://www.python.org/ftp/python/")
    assert len(lock["source"]["sha256"]) == 64
    assert lock["source"]["filename"].endswith(".tar.xz")


def test_lock_tracks_the_shared_pack_micro() -> None:
    """The island runtime compiles against one Python.h: the micros must match."""
    import tomllib

    static = script.load_lock(LOCK)
    with (ROOT / "packaging" / "runtime.lock.toml").open("rb") as fh:
        shared = tomllib.load(fh)
    assert static["version"] == shared["cpython"]


def test_lock_configure_args_make_the_build_static_and_pgo() -> None:
    lock = script.load_lock(LOCK)
    args = list(lock["build"]["configure_args"])
    assert "--disable-shared" in args
    assert "--enable-optimizations" in args


def test_lock_records_the_measured_cost() -> None:
    lock = script.load_lock(LOCK)
    cost = lock["cost"]
    assert cost["build_minutes"] > 0
    assert cost["download_mb"] > 0


def test_setup_local_is_a_static_builtin_list() -> None:
    text = SETUP_LOCAL.read_text(encoding="utf-8")
    assert "*static*" in text
    assert "math mathmodule.c" in text
    # No extension that would drag in an external library we do not ship.
    # Module names only: the header comment names the excluded ones.
    names = {line.split()[0] for line in text.splitlines() if line and not line.startswith(("#", "*"))}
    for forbidden in ("_ssl", "_sqlite3", "readline", "_ctypes", "_curses", "zlib"):
        assert forbidden not in names


def test_setup_local_names_a_real_source_file() -> None:
    """Every curated module must name a source file of the pinned tarball."""
    text = SETUP_LOCAL.read_text(encoding="utf-8")
    modules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("*"):
            continue
        parts = line.split()
        assert len(parts) >= 2, line
        modules.append((parts[0], parts[1]))
    assert ("math", "mathmodule.c") in modules
    names = {name for name, _ in modules}
    # Modules the island import closure cannot do without.
    assert {"math", "_datetime", "_json", "_pickle", "array", "binascii"} <= names


def test_load_lock_refuses_an_unknown_schema(tmp_path: Path) -> None:
    bad = tmp_path / "lock.toml"
    bad.write_text('schema = 2\n[source]\nurl = "u"\nsha256 = "s"\nfilename = "f"\n', encoding="utf-8")
    with pytest.raises(script.BuildError, match="unsupported schema"):
        script.load_lock(bad)


def test_load_lock_refuses_a_missing_setup_local(tmp_path: Path) -> None:
    bad = tmp_path / "lock.toml"
    bad.write_text(
        'schema = 1\n[source]\nurl = "u"\nsha256 = "s"\nfilename = "f"\n'
        '[build]\nsetup_local = "packaging/does-not-exist"\n',
        encoding="utf-8",
    )
    with pytest.raises(script.BuildError, match="does not exist"):
        script.load_lock(bad)


def test_verify_sha256_refuses_a_mismatch(tmp_path: Path) -> None:
    blob = tmp_path / "tarball.tar.xz"
    blob.write_bytes(b"not the pinned tarball")
    with pytest.raises(script.BuildError, match="sha256 mismatch"):
        script.verify_sha256(blob, "0" * 64)


def test_verify_sha256_accepts_the_pinned_digest(tmp_path: Path) -> None:
    import hashlib

    blob = tmp_path / "tarball.tar.xz"
    blob.write_bytes(b"pinned")
    digest = hashlib.sha256(b"pinned").hexdigest()
    script.verify_sha256(blob, digest)
