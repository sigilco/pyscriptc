"""L wave: single inheritance, method decorators, dataclass, integer enum, *args, set."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def test_object_base_matches_a_bare_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "box.py",
        "\n".join(
            [
                "class Box(object):",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def twice(self) -> int:",
                "        return self.n + self.n",
                "class Pair(object,):",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def twice(self) -> int:",
                "        return self.n + self.n",
                "def measure() -> int:",
                "    return Box(3).twice() + Pair(4).twice()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "measure")
    assert got.stdout == "14\n"
    ir = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "box.ir.json"))
    assert ir.returncode == 0, ir.stderr
    text = (tmp_path / "box.ir.json").read_text(encoding="utf-8")
    assert "object.__init__" not in text
    assert '"name": "object"' not in text


def test_a_user_class_named_object_stays_a_base(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "named.py",
        "\n".join(
            [
                "class object:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "class Child(object):",
                "    def read(self) -> int:",
                "        return self.n",
                "def measure() -> int:",
                "    return Child(6).read()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "6\n"


def test_single_inheritance_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "inherit.py",
        "\n".join(
            [
                "class Point:",
                "    x: int",
                "    y: int",
                "    def __init__(self, x: int, y: int) -> None:",
                "        self.x = x",
                "        self.y = y",
                "    def manhattan(self) -> int:",
                "        return self.x + self.y",
                "class Spot(Point):",
                "    z: int",
                "    def __init__(self, x: int, y: int, z: int) -> None:",
                "        self.x = x",
                "        self.y = y",
                "        self.z = z",
                "    def volume(self) -> int:",
                "        return self.manhattan() + self.z",
                "class AppError(Exception):",
                "    pass",
                "class Worse(AppError):",
                "    pass",
                "class Bad(ValueError):",
                "    pass",
                "def measure() -> int:",
                "    spot: Spot = Spot(1, 2, 3)",
                "    caught: int = 0",
                "    try:",
                "        raise AppError('no')",
                "    except AppError:",
                "        caught = 1",
                "    try:",
                "        raise Worse('x')",
                "    except Exception:",
                "        caught = caught + 1",
                "    try:",
                "        raise Worse('y')",
                "    except AppError:",
                "        caught = caught + 1",
                "    try:",
                "        raise Bad('z')",
                "    except ValueError:",
                "        caught = caught + 1",
                "    return spot.volume() + caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "measure")


def test_method_decorators_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "deco.py",
        "\n".join(
            [
                "class Box:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    @property",
                "    def doubled(self) -> int:",
                "        return self.n + self.n",
                "    @staticmethod",
                "    def twice(n: int) -> int:",
                "        return n + n",
                "    @classmethod",
                "    def make(cls, n: int) -> int:",
                "        return n + 1",
                "def total() -> int:",
                "    box: Box = Box(3)",
                "    return box.doubled + Box.twice(4) + box.twice(1) + Box.make(5)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "total")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "total")


def test_flat_dataclass_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "data.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "@dataclass",
                "class Pair:",
                "    x: int",
                "    y: int = 2",
                "def total() -> int:",
                "    first: Pair = Pair(1, 3)",
                "    second: Pair = Pair(4)",
                "    return first.x + first.y + second.x + second.y",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "total")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "total")


def test_integer_enum_matches_where_cpython_agrees(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "colors.py",
        "\n".join(
            [
                "from enum import Enum, IntEnum",
                "class Color(IntEnum):",
                "    RED = 1",
                "    BLUE = 2",
                "class Flag(Enum):",
                "    RED = 1",
                "    BLUE = 2",
                "def total() -> int:",
                "    same: int = 0",
                "    if Flag.RED == Flag.RED:",
                "        if Flag.RED == Flag.BLUE:",
                "            return 0",
                "        same = 1",
                "    return Color.RED + Color.BLUE + same",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "total")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "total")


def test_varargs_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "varargs.py",
        "\n".join(
            [
                "def total(*xs: int) -> int:",
                "    acc: int = 0",
                "    for item in xs:",
                "        acc = acc + item",
                "    if len(xs) == 0:",
                "        return 0",
                "    return acc + len(xs) + xs[0]",
                "def run() -> int:",
                "    return total(1, 2, 3) + total()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_set_matches_cpython_without_order(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sets.py",
        "\n".join(
            [
                "def total() -> int:",
                "    xs: set[int] = {1, 2, 1}",
                "    xs.add(2)",
                "    xs.add(3)",
                "    ys: set[str] = set(['a', 'b', 'a'])",
                "    ys.add('c')",
                "    acc: int = 0",
                "    for item in xs:",
                "        acc = acc + item",
                "    chars: int = 0",
                "    for text in ys:",
                "        chars = chars + len(text)",
                "    if 3 in xs and 'c' in ys and 4 not in xs:",
                "        return acc + len(xs) + chars + len(ys)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "total")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "total")


def test_dict_keys_match_cpython_insertion_order(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "keys.py",
        "\n".join(
            [
                "names: list[str] = ['b', 'c']",
                "extra: dict[str, list[str]] = {'a': ['x'], 'd': []}",
                "merged: list[str] = sorted(names + list(extra.keys()))",
                "labels: set[str] = set(['b', 'a', 'b'])",
                "",
                "def keys_order() -> int:",
                "    words: dict[str, int] = {'b': 1, 'a': 2, 'c': 3}",
                "    joined: str = ''",
                "    for word in words.keys():",
                "        joined = joined + word",
                "    copied: list[str] = list(words.keys())",
                "    nums: dict[int, str] = {3: 'c', 1: 'a', 2: 'b'}",
                "    acc: int = 0",
                "    place: int = 1",
                "    for num in nums.keys():",
                "        acc = acc + num * place",
                "        place = place * 10",
                "    again: dict[str, int] = {'a': 1, 'b': 2, 'a': 9}",
                "    dup: list[str] = list(again.keys())",
                "    nested: dict[str, list[str]] = {'z': ['a'], 'm': []}",
                "    nest: str = ''",
                "    for part in nested.keys():",
                "        nest = nest + part",
                "    nest_copy: list[str] = list(nested.keys())",
                "    empty: dict[str, int] = {}",
                "    if joined != 'bac' or copied[0] != 'b' or copied[1] != 'a' or copied[2] != 'c':",
                "        return 0",
                "    if len(copied) != 3 or acc != 213 or dup[0] != 'a' or dup[1] != 'b' or len(dup) != 2:",
                "        return 0",
                "    if nest != 'zm' or nest_copy[0] != 'z' or nest_copy[1] != 'm' or len(empty.keys()) != 0:",
                "        return 0",
                "    if merged[0] != 'a' or merged[1] != 'b' or merged[2] != 'c' or merged[3] != 'd':",
                "        return 0",
                "    if len(merged) != 4:",
                "        return 0",
                "    if 'a' not in labels or 'b' not in labels or 'c' in labels or len(labels) != 2:",
                "        return 0",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "keys_order")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "keys_order")
    assert got.stdout.strip() == "1"


def test_lwave_fences(tmp_path: Path) -> None:
    cases = [
        (
            "super.py",
            "\n".join(
                [
                    "class Point:",
                    "    x: int",
                    "    def __init__(self, x: int) -> None:",
                    "        self.x = x",
                    "class Spot(Point):",
                    "    y: int",
                    "    def __init__(self, x: int, y: int) -> None:",
                    "        super().__init__(x)",
                    "        self.y = y",
                    "def f() -> int:",
                    "    return Spot(1, 2).y",
                    "",
                ]
            ),
            "super is not lowered",
        ),
        (
            "multi.py",
            "\n".join(
                [
                    "class A:",
                    "    x: int",
                    "class B:",
                    "    y: int",
                    "class C(A, B):",
                    "    pass",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "multiple inheritance is not lowered",
        ),
        (
            "objectmulti.py",
            "\n".join(
                [
                    "class A:",
                    "    x: int",
                    "    def __init__(self, x: int) -> None:",
                    "        self.x = x",
                    "class D(object, A):",
                    "    pass",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "multiple inheritance is not lowered",
        ),
        (
            "kwargs.py",
            "def f(**kwargs) -> int:\n    return 1\n",
            "parameter form",
        ),
        (
            "star.py",
            "def g(x: int) -> int:\n    return x\ndef f() -> int:\n    xs: list[int] = [1]\n    return g(*xs)\n",
            "starred calls are not lowered",
        ),
        (
            "userdeco.py",
            "\n".join(
                [
                    "def deco(fn: int) -> int:",
                    "    return fn",
                    "class Box:",
                    "    @deco",
                    "    def n(self) -> int:",
                    "        return 1",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "decorators are not lowered",
        ),
        (
            "badset.py",
            "def f() -> int:\n    xs: set[float] = {1.0}\n    return len(xs)\n",
            "set elements are int or str",
        ),
        (
            "items.py",
            "def f() -> int:\n    d: dict[str, int] = {'a': 1}\n    return len(list(d.items()))\n",
            "call of attribute is not lowered (d.items)",
        ),
        (
            "values.py",
            "def f() -> int:\n    d: dict[str, int] = {'a': 1}\n    return len(list(d.values()))\n",
            "call of attribute is not lowered (d.values)",
        ),
        (
            "strkeys.py",
            "def f() -> int:\n    text: str = 'ab'\n    return len(list(text.keys()))\n",
            "call of attribute is not lowered (text.keys)",
        ),
        (
            "keyargs.py",
            "def f() -> int:\n    d: dict[str, int] = {'a': 1}\n    return len(list(d.keys(1)))\n",
            "dict.keys expects 0 argument(s)",
        ),
        (
            "setsub.py",
            "def f() -> int:\n    xs: set[int] = {1, 2}\n    return xs[0]\n",
            "a set has no subscript",
        ),
        (
            "setitem.py",
            "def f() -> int:\n    xs: set[int] = {1}\n    xs[0] = 2\n    return 1\n",
            "a set has no item assignment",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
