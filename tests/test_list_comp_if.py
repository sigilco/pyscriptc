"""Comprehension filters and widening (R39).

Filters and nested `for` clauses desugar to guarded loops: each level's `if`s
wrap the next level's loop, and the element (and key, for dicts) evaluates
once per kept item. Set comprehension inserts dedupe like a set literal;
dict comprehension lowers to `setitem` on the accumulator.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def bump(hits: list[int], n: int) -> int:
    hits[0] = hits[0] + 1
    return n

def pred(checks: list[int], n: int) -> bool:
    checks[0] = checks[0] + 1
    return n > 1

def checklist() -> int:
    xs: list[int] = [i for i in range(4) if i > 1]
    return xs[0]

def probe() -> str:
    checks: list[int] = [0]
    kept: list[int] = [0]
    xs: list[int] = [bump(kept, i) for i in range(4) if pred(checks, i)]
    rejected: list[int] = [0]
    missed: list[int] = [bump(rejected, i) for i in range(3) if i > 5]
    return (
        str(checklist())
        + str(checks[0])
        + str(kept[0])
        + str(xs[0])
        + str(xs[1])
        + str(len(missed))
        + str(rejected[0])
    )

def plain() -> int:
    xs: list[int] = [i + 1 for i in range(3)]
    return xs[2]

def from_list() -> int:
    src: list[int] = [0, 1, 2, 3]
    xs: list[int] = [i + 1 for i in src if i > 1]
    return xs[0]

def truthy() -> int:
    xs: list[int] = [i for i in range(4) if i]
    return xs[0]

def two_if() -> int:
    xs: list[int] = [i for i in range(6) if i > 1 if i < 4]
    return xs[0] + xs[1]

def dict_comp() -> str:
    xs: list[int] = [1, 2, 3]
    d: dict[str, int] = {str(i): i * 2 for i in xs}
    return str(d["2"]) + str(len(d))

def dict_if() -> int:
    xs: list[int] = [1, 2, 3, 4]
    d: dict[int, int] = {i: i * i for i in xs if i % 2 == 0}
    return d[2] + d[4]

def set_comp() -> int:
    xs: list[int] = [1, 1, 2, 3, 3]
    s: set[int] = {i for i in xs}
    t: set[int] = {i * 2 for i in xs if i > 1}
    return len(s) + len(t)

def nested() -> int:
    xs: list[list[int]] = [[1, 2], [3]]
    ys: list[int] = [x for a in xs for x in a]
    return ys[0] + ys[1] + ys[2]

def nested_if() -> int:
    xs: list[list[int]] = [[1, 2], [3], [4, 5]]
    ys: list[int] = [x for a in xs if len(a) > 1 for x in a if x > 2]
    return ys[0] + ys[1]

def nested_side_effect() -> str:
    checks: list[int] = [0]
    xs: list[list[int]] = [[1], [2, 3]]
    ys: list[int] = [x for a in xs if pred(checks, len(a)) for x in a]
    return str(checks[0]) + str(len(ys))

def comp_in_comp() -> int:
    xs: list[list[int]] = [[1, 2], [3]]
    ys: list[list[int]] = [[x * 10 for x in a] for a in xs]
    return ys[0][1] + ys[1][0]

def unpack() -> int:
    xs: list[int] = [5, 6]
    ys: list[int] = [i * x for i, x in enumerate(xs)]
    return ys[1]

def over_dict() -> str:
    d: dict[str, int] = {"a": 1, "b": 2}
    ks: list[str] = [k for k in d]
    return ks[0] + ks[1]
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_list_comp_if_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "comp.py", _PROBE)
    expected = {
        "probe": "2422300",
        "plain": "3",
        "from_list": "3",
        "truthy": "1",
        "two_if": "5",
        "dict_comp": "43",
        "dict_if": "20",
        "set_comp": "5",
        "nested": "6",
        "nested_if": "9",
        "nested_side_effect": "22",
        "comp_in_comp": "50",
        "unpack": "6",
        "over_dict": "ab",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_list_comp_if_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "float_if.py",
            (
                "def probe() -> int:\n"
                "    n: float = 1.0\n"
                "    xs: list[int] = [i for i in range(2) if n]\n"
                "    return xs[0]\n"
            ),
            "condition must be a bool, not a truthy value",
        ),
        # R24: generator expressions materialize eagerly (the ListComp path);
        # `len((i for i in range(2)))` now compiles.
        (
            "async_comp.py",
            "def probe() -> int:\n    xs: list[int] = [i async for i in [1]]\n    return xs[0]\n",
            "async comprehensions are not lowered",
        ),
        (
            "star_unpack.py",
            "def probe() -> int:\n    xs: list[int] = [x for a, *rest in [1]]\n    return xs[0]\n",
            "for unpacks flat names",
        ),
        (
            "set_elem.py",
            "def probe() -> int:\n    s: set[float] = {1.5 for i in range(2)}\n    return len(s)\n",
            "set elements are int or str",
        ),
        (
            "dict_key.py",
            "def probe() -> int:\n    d: dict[list[int], int] = {[i]: i for i in [1]}\n    return len(d)\n",
            "dict keys are int or str",
        ),
        (
            "dict_bool.py",
            "def probe() -> int:\n    d: dict[int, bool] = {i: i > 0 for i in range(2)}\n    return len(d)\n",
            "dict values of bool are not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
