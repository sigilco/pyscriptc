"""tuple[T, ...] of one lowered element is the list struct.unpack already returns.

The value is a list. Length, index, and the sum of the ints match CPython.
The repr and the type name do not, so the probe prints an int. A list assigned
to that annotation is the known delta, so mypy is told to ignore it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import struct
from typing import Tuple

def via_typing() -> Tuple[int, ...]:
    nums: Tuple[int, ...] = [2, 5, 9]  # type: ignore[assignment]
    return nums

def via_unpack() -> tuple[int, ...]:
    raw: bytes = struct.pack("<iii", 2, 5, 9)
    return struct.unpack("<iii", raw)

def probe() -> int:
    nums: tuple[int, ...] = [2, 5, 9]  # type: ignore[assignment]
    words: tuple[str, ...] = ["Ada", "Lovelace"]  # type: ignore[assignment]
    empty: tuple[int, ...] = []  # type: ignore[assignment]
    pair: tuple[int, int] = (4, 7)
    got: tuple[int, ...] = via_typing()
    packed: tuple[int, ...] = via_unpack()
    return (
        len(nums) * 1000000
        + nums[1] * 100000
        + sum(nums) * 1000
        + len(words) * 100
        + len(words[0]) * 10
        + len(empty)
        + pair[0]
        + len(got)
        + packed[0]
        + sum(packed)
    )
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_tuple_ellipsis_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "tuple_ellipsis.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "3516255"


def test_tuple_ellipsis_fences(tmp_path: Path) -> None:
    cases = [
        (
            "mixed.py",
            "def read() -> tuple[int, str, ...]:\n    return [1]\n",
            "tuple[T, ...] is not in the IR",
        ),
        (
            "nested.py",
            "def read() -> tuple[tuple[int, ...], ...]:\n    return []\n",
            "tuple[T, ...] is not in the IR",
        ),
        (
            "two.py",
            "def read() -> tuple[int, int, ...]:\n    return [1]\n",
            "tuple[T, ...] is not in the IR",
        ),
        (
            "bools.py",
            "def read() -> tuple[bool, ...]:\n    return []\n",
            "list[bool] is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
