"""The four first-hour fences of issue #1246.

`str.find` with a start index, a starred expression in a list display,
`print` of a dataclass instance, and `except json.JSONDecodeError`.
Each case compiles clean, runs, and byte-compares stdout against the
host CPython the same way the differential probes do.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

needs_zig = pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")
    return got.stdout


@needs_zig
def test_str_find_start(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "find_start.py",
        """
def main() -> int:
    s: str = "hello world"
    print(s.find("world", 3))
    print(s.find("world", 7))
    print(s.find("o", 5))
    print(s.find("", 11))
    print(s.find("", 12))
    print(s.find("xyz", 2))
    print(s.find("o", -5))
    print(s.find("o", -1))
    return 0
""",
    )
    assert out == "6\n-1\n7\n11\n-1\n-1\n7\n-1\n0\n"


@needs_zig
def test_starred_list_display(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "starred_list.py",
        """
def main() -> int:
    a: list[int] = [1, 2]
    b: list[int] = [0, *a, 3]
    print(b)
    c: list[int] = [*a]
    print(c)
    d: list[int] = []
    print([*d, 1])
    e: list[int] = [*a, *a]
    print(e)
    return 0
""",
    )
    assert out == "[0, 1, 2, 3]\n[1, 2]\n[1]\n[1, 2, 1, 2]\n0\n"


@needs_zig
def test_dataclass_repr_scalars(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "dataclass_repr_scalars.py",
        """
from dataclasses import dataclass

@dataclass
class P:
    x: int
    s: str
    f: float

def main() -> int:
    print(P(1, "hi", 1.5))
    return 0
""",
    )
    assert out == "P(x=1, s='hi', f=1.5)\n0\n"


@needs_zig
def test_dataclass_repr_containers(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "dataclass_repr_containers.py",
        """
from dataclasses import dataclass

@dataclass
class C:
    xs: list[int]
    m: dict[str, int]

def main() -> int:
    print(C([1, 2], {"k": 1}))
    return 0
""",
    )
    assert out == "C(xs=[1, 2], m={'k': 1})\n0\n"


@needs_zig
def test_dataclass_repr_nested(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "dataclass_repr_nested.py",
        """
from dataclasses import dataclass

@dataclass
class Inner:
    a: int

@dataclass
class Outer:
    inner: Inner

def main() -> int:
    print(Outer(Inner(5)))
    return 0
""",
    )
    assert out == "Outer(inner=Inner(a=5))\n0\n"


@needs_zig
def test_dataclass_written_repr_wins(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "dataclass_written_repr.py",
        """
from dataclasses import dataclass

@dataclass
class P:
    x: int

    def __repr__(self) -> str:
        return "P!"

def main() -> int:
    print(P(1))
    return 0
""",
    )
    assert out == "P!\n0\n"


@needs_zig
def test_dataclass_empty_repr(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "dataclass_empty.py",
        """
from dataclasses import dataclass

@dataclass
class E:
    pass

def main() -> int:
    print(E())
    return 0
""",
    )
    assert out == "E()\n0\n"


@needs_zig
def test_dataclass_repr_field_fences_print(tmp_path: Path) -> None:
    """A dataclass whose field has no repr keeps the print fence."""
    path = write_case(
        tmp_path,
        "dataclass_fence.py",
        """
from dataclasses import dataclass

@dataclass
class P:
    xs: list[object]

def main() -> int:
    print(P([]))
    return 0
""",
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode != 0
    assert "PS2003" in got.stderr


@needs_zig
def test_except_json_decode_error(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "except_json_decode.py",
        """
import json

def main() -> int:
    try:
        d: dict[str, int] = json.loads("not json")
        print(d)
    except json.JSONDecodeError:
        print("caught")
    try:
        e: list[int] = json.loads("[1, ")
        print(e)
    except ValueError:
        print("caught parent")
    return 0
""",
    )
    assert out == "caught\ncaught parent\n0\n"


@needs_zig
def test_json_decode_error_name_on_uncaught(tmp_path: Path) -> None:
    """An uncaught json failure names JSONDecodeError on stderr."""
    path = write_case(
        tmp_path,
        "uncaught_json.py",
        """
import json

def main() -> int:
    d: dict[str, int] = json.loads("not json")
    print(d)
    return 0
""",
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode != 0
    assert "JSONDecodeError" in got.stderr


@needs_zig
def test_json_ok_without_except(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "json_ok.py",
        """
import json

def main() -> int:
    d: dict[str, int] = json.loads('{"a": 1}')
    print(d["a"])
    return 0
""",
    )
    assert out == "1\n0\n"
