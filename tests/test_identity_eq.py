"""R18 + R37: container equality, `is` identity, and `id()`.

list/dict/tuple/set `==` and `!=` compare contents; `x is y` compares pointer
identity for heap objects, backing-buffer identity for str/bytes, and value
equality for int/float/bool (which carry no runtime address). `id()` is the
object address, so tests assert only relations between ids, never values.
A user class without __eq__ falls back to identity for `==`/`!=`, as CPython.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def seq() -> int:
    print([1] == [1])
    print([1, 2, 3] == [1, 2, 3])
    print([1, 2] == [1, 3])
    print([1, 2] == [1, 2, 3])
    print([1] != [1])
    print([[1], [2]] == [[1], [2]])
    print(["a"] == ["a"])
    print([1.5] == [1.5])
    return 0


def sets() -> int:
    print({1, 2} == {2, 1})
    print({1, 2} == {1, 3})
    print({1, 2} == {1, 2, 3})
    print({"a", "b"} == {"b", "a"})
    return 0


def maps() -> int:
    print({"a": 1, "b": 2} == {"b": 2, "a": 1})
    print({"a": 1} == {"a": 2})
    print({"a": 1} == {"b": 1})
    print({"a": 1} != {"a": 2})
    print({"a": {"x": 1}} == {"a": {"x": 1}})
    print({"a": [1]} == {"a": [1]})
    return 0


def pairs() -> int:
    print((1, "a") == (1, "a"))
    print((1, "a") == (1, "b"))
    print((1, "a") != (1, "a"))
    print(("a", "b") == ("a", "b"))
    t: tuple[int, str] = (7, "x")
    u: tuple[int, str] = t
    print(t == u)
    print(t is u)
    print(t is t)
    return 0


def refs() -> int:
    a: list[int] = [1, 2]
    b: list[int] = a
    c: list[int] = [1, 2]
    print(a is b)
    print(a is c)
    print(a is not c)
    print(b is not a)
    d: dict[str, int] = {"k": 1}
    e: dict[str, int] = d
    print(d is e)
    print(d == e)
    st: set[int] = {1, 2}
    st2: set[int] = st
    print(st is st2)
    print(st is {1, 2})
    return 0


def classes() -> int:
    x: Box = Box(1)
    y: Box = x
    z: Box = Box(1)
    print(x is y)
    print(x is z)
    print(x is not z)
    print(x == y)
    print(x == z)
    print(x != z)
    return 0


def scalars() -> int:
    n: int = 7
    m: int = 7
    print(n is m)
    print(n is not m)
    f: float = 1.5
    g: float = f
    print(f is g)
    s: str = "hi"
    t: str = s
    print(s is t)
    print(True is True)
    print(None is None)
    print(n is s)
    return 0


def ids() -> int:
    a: list[int] = [1, 2]
    b: list[int] = a
    c: list[int] = [1, 2]
    print(id(a) == id(b))
    print(id(a) != id(c))
    x: Box = Box(1)
    y: Box = x
    z: Box = Box(1)
    print(id(x) == id(y))
    print(id(x) != id(z))
    d: dict[str, int] = {"k": 1}
    print(id(d) == id(d))
    return 0


def selfeq() -> int:
    nan: float = float("nan")
    xs: list[float] = [nan]
    print(xs == xs)
    ys: list[float] = [float("nan")]
    print(xs == ys)
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_identity_eq_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "identity_eq.py", _PROBE)
    for func in ["seq", "sets", "maps", "pairs", "refs", "classes", "scalars", "ids", "selfeq"]:
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func), func


def test_identity_eq_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "scalar_id.py",
            "def probe() -> int:\n    print(id(1))\n    return 0\n",
            "id is not lowered for these arguments",
        ),
        (
            "chain.py",
            (
                "def probe() -> int:\n"
                "    a: list[int] = [1]\n"
                "    b: list[int] = [1]\n"
                "    if a is b is a:\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "is is not lowered",
        ),
        (
            "mismatch.py",
            (
                "def probe() -> int:\n"
                "    a: list[int] = [1]\n"
                "    b: list[str] = ['a']\n"
                "    if a == b:\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "comparisons need two values of the same scalar type",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
