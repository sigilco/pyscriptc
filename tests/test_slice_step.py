"""A slice step selects every step-th element.

`len('abcd'[::2])` is 2. Step 1 stays the old slice. A negative step stays
fenced. Step 0 raises ValueError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
class Counter:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n

def bump(box: Counter) -> int:
    box.n = box.n + 1
    return 2

def checklist() -> int:
    return len("abcd"[::2])

def text() -> str:
    return "abcd"[::2]

def window() -> str:
    return "abcdef"[1:5:2]

def from_end() -> str:
    return "abcd"[-3::2]

def evens() -> int:
    xs: list[int] = [0, 1, 2, 3, 4]
    ys: list[int] = xs[::2]
    return ys[0] + ys[1] + ys[2]

def raw() -> int:
    data: bytes = b"abcd"
    return len(data[::2])

def once() -> int:
    box: Counter = Counter(0)
    text: str = "abcd"
    got: str = text[:: bump(box)]
    return len(got) * 10 + box.n

def zero() -> int:
    text: str = "abcd"
    try:
        got: str = text[::0]
        return len(got)
    except ValueError:
        return 7
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_slice_step_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "slice.py", _PROBE)
    expected = {
        "checklist": "2",
        "text": "ac",
        "window": "bd",
        "from_end": "bd",
        "evens": "6",
        "raw": "2",
        "once": "21",
        "zero": "7",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_slice_step_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "neg.py",
            'def probe() -> str:\n    return "abcd"[::-1]\n',
            "a negative slice step is not lowered",
        ),
        (
            "assign.py",
            "def probe() -> int:\n    xs: list[int] = [1, 2, 3]\n    xs[::2] = [0, 0]\n    return xs[0]\n",
            "slices are not lowered",
        ),
        (
            "step.py",
            'def probe() -> str:\n    return "abcd"[::1.0]\n',
            "a slice step is int",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            *(["--skip-check"] if name == "step.py" else []),
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
