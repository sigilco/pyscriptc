"""Checker-alignment shims: the lowered contract, not typeshed, is the truth.

`os.getenv(key)` returns `str | None`, `struct.unpack` yields
a homogeneous `list`, `frozenset(...)` is the set list, and
`from posixpath import join` binds like `posixpath.join`. Each probe below
compiles WITHOUT `--skip-check` and matches the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import ENV, compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_getenv_str_annotation_compiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = """
import os

def probe() -> int:
    v: str | None = os.getenv("PYSC_TEST_ENV")
    if v is None:
        return -1
    return len(v)
"""
    monkeypatch.setitem(ENV, "PYSC_TEST_ENV", "hello")
    monkeypatch.setenv("PYSC_TEST_ENV", "hello")
    path = write_case(tmp_path, "getenv_str.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "5"


@pytest.mark.linux_container
def test_getenv_unset_returns_none(tmp_path: Path) -> None:
    source = """
import os

def probe() -> int:
    v: str | None = os.getenv("PYSC_TEST_ENV_UNSET")
    if v is None:
        return -7
    return len(v)
"""
    path = write_case(tmp_path, "getenv_unset.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "-7"


@pytest.mark.linux_container
def test_unpack_list_annotation_compiles(tmp_path: Path) -> None:
    source = """
import struct

def probe() -> int:
    data: bytes = struct.pack(">ii", 3, 4)
    vals: list[int] = struct.unpack(">ii", data)
    return vals[0] + vals[1]
"""
    path = write_case(tmp_path, "unpack_list.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "7"


@pytest.mark.linux_container
def test_frozenset_set_annotation_compiles(tmp_path: Path) -> None:
    source = """
def probe() -> int:
    xs: set[int] = frozenset([1, 2, 3])
    return len(xs)
"""
    path = write_case(tmp_path, "frozenset_set.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "3"


@pytest.mark.linux_container
def test_from_posixpath_import_join(tmp_path: Path) -> None:
    source = """
from posixpath import join

def probe() -> int:
    p: str = join("a", "b")
    return len(p)
"""
    path = write_case(tmp_path, "posixpath_join.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "3"


def test_checker_still_rejects_real_mismatch(tmp_path: Path) -> None:
    source = """
import os

def probe() -> int:
    v: int = os.getenv("PYSC_TEST_ENV")
    return v
"""
    path = write_case(tmp_path, "getenv_mismatch.py", source)
    got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / "mismatch.ll"))
    assert got.returncode == 2, got.stderr
    assert "Incompatible types" in got.stderr
