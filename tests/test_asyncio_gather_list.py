# SPDX-License-Identifier: Apache-2.0
"""`asyncio.gather` answers a list (issue #1278).

The checker read the tuple typeshed spells on the fixed-arity `gather`
overloads, while the lowerer collects the results into a list literal
and CPython answers a list, so the same program was accepted and then
mis-lowered, or fenced at the annotation site. The checker now reads the
list, and the shapes here pin that:

- `list[T]` is the annotation a gather result takes,
- a tuple annotation is refused (it is not what CPython answers),
- the results keep the argument order,
- `return_exceptions=True` fences instead of being silently dropped
  (the runtime has no exception slot in the result cells),
- the committed differential probe
  `probes/differential/asyncio_gather_list.py` is byte-identical to the
  pinned CPython 3.14.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case

from pyscriptc.check import CheckFailed, run_checker

ZIG = shutil.which("zig") is not None
needs_zig = pytest.mark.skipif(not ZIG, reason="zig not on PATH")
needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly binary not on PATH")

PROBE = ROOT / "probes" / "differential" / "asyncio_gather_list.py"

GATHER_LIST = """\
import asyncio


async def work(n: int) -> int:
    return n * 10


async def root() -> int:
    r: list[int] = await asyncio.gather(work(1), work(2))
    print("gather", r)
    return r[0] + r[1]


print("sum", asyncio.run(root()))
"""

GATHER_TUPLE = """\
import asyncio


async def work(n: int) -> int:
    return n * 10


async def root() -> int:
    r: tuple[int, int] = await asyncio.gather(work(1), work(2))
    return r[0] + r[1]
"""


def _fence(tmp_path: Path, name: str, source: str) -> str:
    """The compiler's stderr for a program that must not compile."""
    path = write_case(tmp_path, name + ".py", source)
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode != 0, proc.stdout
    return proc.stderr


@needs_pyrefly
def test_the_checker_reads_the_gather_result_as_a_list(tmp_path: Path) -> None:
    path = write_case(tmp_path, "gather_list.py", GATHER_LIST)
    run_checker(path, "pyrefly")


@needs_pyrefly
def test_the_checker_refuses_a_tuple_gather_result(tmp_path: Path) -> None:
    path = write_case(tmp_path, "gather_tuple.py", GATHER_TUPLE)
    with pytest.raises(CheckFailed):
        run_checker(path, "pyrefly")


@needs_zig
def test_the_gather_result_is_a_list_at_run_time(tmp_path: Path) -> None:
    path = write_case(tmp_path, "gather_run.py", GATHER_LIST)
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "gather_run"),
            "--run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert built.returncode == 0, built.stderr
    assert built.stdout == "gather [10, 20]\nsum 30\n"


@needs_zig
def test_the_gather_keeps_the_argument_order(tmp_path: Path) -> None:
    source = """\
import asyncio


async def work(n: int) -> int:
    await asyncio.sleep(0.05 - n * 0.01)
    return n * 10


async def root() -> int:
    r: list[int] = await asyncio.gather(work(1), work(2), work(3))
    print("gather", r)
    return r[0]


print("sum", asyncio.run(root()))
"""
    path = write_case(tmp_path, "gather_order.py", source)
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "gather_order"),
            "--run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert built.returncode == 0, built.stderr
    assert built.stdout == "gather [10, 20, 30]\nsum 10\n"


def _compile(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    """Compile `source` with the checker bypassed; return the process."""
    path = write_case(tmp_path, name + ".py", source)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "-o",
            str(tmp_path / name),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )


RETURN_EXCEPTIONS = """\
import asyncio


async def work(n: int) -> int:
    return n


async def root() -> int:
    r: list[int] = await asyncio.gather(work(1), work(2), return_exceptions=True)
    return r[0]
"""


@needs_pyrefly
def test_return_exceptions_true_is_refused_by_the_checker(tmp_path: Path) -> None:
    """CPython answers `list[BaseException | int]` there, so `list[int]` is not it."""
    path = write_case(tmp_path, "gather_exceptions.py", RETURN_EXCEPTIONS)
    with pytest.raises(CheckFailed):
        run_checker(path, "pyrefly")


def test_return_exceptions_true_fences_in_the_lowerer(tmp_path: Path) -> None:
    """The keyword changes what CPython answers, so it cannot be dropped."""
    proc = _compile(tmp_path, "gather_exceptions", RETURN_EXCEPTIONS)
    assert proc.returncode != 0
    assert "PS2003" in proc.stderr
    assert "return_exceptions" in proc.stderr


def test_return_exceptions_false_is_accepted(tmp_path: Path) -> None:
    """The default (`False`) is the behavior the subset lowers."""
    proc = _compile(tmp_path, "gather_default", GATHER_DEFAULT_KW)
    assert proc.returncode == 0, proc.stderr
    assert "PS2003" not in proc.stderr


GATHER_DEFAULT_KW = """\
import asyncio


async def work(n: int) -> int:
    return n


async def root() -> int:
    r: list[int] = await asyncio.gather(work(1), work(2), return_exceptions=False)
    print("gather", r)
    return r[0]
"""


@needs_zig
def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe byte-compares stdout, stderr and the exit code."""
    assert PROBE.is_file()
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(PROBE),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "probe"),
            "--run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert built.returncode == 0, built.stderr
    oracle = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert oracle.returncode == built.returncode
    assert oracle.stdout == built.stdout
    assert oracle.stderr == built.stderr
