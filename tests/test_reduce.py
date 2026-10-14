"""`functools.reduce(operator.add, xs)` is a loop of `+` on a list of int.

The checklist list `[1, 2, 3]` sums to 6. An initializer stays fenced.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import functools
import operator

def checklist() -> int:
    return functools.reduce(operator.add, [1, 2, 3])

def one() -> int:
    return functools.reduce(operator.add, [4])

def two() -> int:
    xs: list[int] = [2, 5]
    return functools.reduce(operator.add, xs)

def add(a: int, b: int) -> int:
    return a + b

def named() -> int:
    return functools.reduce(add, [1, 2, 3])

def named_init() -> int:
    return functools.reduce(add, [1, 2, 3], 4)
"""


def test_reduce_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "reduce.py", _PROBE)
    expected = {"checklist": "6", "one": "4", "two": "7", "named": "6", "named_init": "10"}
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_reduce_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "init.py",
            "import functools\nimport operator\n"
            "def probe() -> int:\n    return functools.reduce(operator.add, [1, 2], 0)\n",
            "functools.reduce initializer is not lowered",
        ),
        (
            "other.py",
            "import functools\nimport operator\n"
            "def probe() -> int:\n    return functools.reduce(operator.mul, [1, 2])\n",
            "functools.reduce takes a direct function name",
        ),
        (
            "partial.py",
            "import functools\ndef probe() -> int:\n    functools.partial(probe, 1)\n    return 1\n",
            "functools.partial is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
