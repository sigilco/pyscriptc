"""A list comprehension filter is the existing if around the list push.

The element expression increments a counter. A false filter does not evaluate
it, so a rejected element leaves the counter at 0 and a kept element increments
it. The filter itself runs once per element. The checklist value is xs[0] of
`[i for i in range(4) if i > 1]`.
"""

from __future__ import annotations

from pathlib import Path

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
"""


def test_list_comp_if_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "comp.py", _PROBE)
    expected = {
        "probe": "2422300",
        "plain": "3",
        "from_list": "3",
        "truthy": "1",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_list_comp_if_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "two_if.py",
            "def probe() -> int:\n    xs: list[int] = [i for i in range(4) if i > 1 if i < 3]\n    return xs[0]\n",
            "list comprehensions have one if",
        ),
        (
            "two_for.py",
            "def probe() -> int:\n    xs: list[int] = [x for x in range(2) for y in range(2)]\n    return xs[0]\n",
            "list comprehensions have one for",
        ),
        (
            "if_before.py",
            (
                "def probe() -> int:\n"
                "    xs: list[int] = [x for x in range(2) if x > 0 for y in range(2)]\n"
                "    return xs[0]\n"
            ),
            "an if before the for is not lowered",
        ),
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
        (
            "set_comp.py",
            "def probe() -> int:\n    return len({i for i in range(2) if i > 0})\n",
            "SetComp is not lowered",
        ),
        (
            "dict_comp.py",
            "def probe() -> int:\n    return len({i: i for i in range(2) if i > 0})\n",
            "DictComp is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
