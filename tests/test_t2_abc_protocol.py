"""T2-2 ABC/Protocol erasure (tier-2 typed compile-time lowering).

`class C(ABC)` / `metaclass=ABCMeta`, `@abstractmethod`, `class P(Protocol)`
and `@overload` erase before LLVM emission: the class lowers as a plain
class, the decorator drops off the list, an overload-declared signature
disappears so only the final implementation lowers. Static constructs only —
there is no runtime enforcement: `ABC()` instantiates, declaration-only
(`...`/`pass`) bodies are skipped rather than emitted, and a protocol-typed
parameter accepts any class value (the erased class is an empty marker).
`@runtime_checkable` and `isinstance` on a Protocol stay fenced. Each probe
diffs stdout against the CPython oracle; fence cases assert the diagnostic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_t2_abc_base_and_abstractmethod(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "abc_base.py",
        "\n".join(
            [
                "from abc import ABC, abstractmethod",
                "",
                "class Shape(ABC):",
                "    @abstractmethod",
                "    def area(self) -> int: ...",
                "",
                "    def describe(self) -> str:",
                "        return 'shape'",
                "",
                "class Square(Shape):",
                "    side: int",
                "",
                "    def __init__(self, side: int) -> None:",
                "        self.side = side",
                "",
                "    def area(self) -> int:",
                "        return self.side * self.side",
                "",
                "def run() -> int:",
                "    s: Square = Square(3)",
                "    print(s.area())",
                "    print(s.describe())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_t2_abcmeta_keyword_and_abstract_property(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "abc_meta.py",
        "\n".join(
            [
                "from abc import ABCMeta, abstractmethod",
                "",
                "class Base(metaclass=ABCMeta):",
                "    v: int",
                "",
                "    def __init__(self, v: int) -> None:",
                "        self.v = v",
                "",
                "    @property",
                "    @abstractmethod",
                "    def doubled(self) -> int: ...",
                "",
                "class Impl(Base):",
                "    @property",
                "    def doubled(self) -> int:",
                "        return self.v * 2",
                "",
                "def run() -> int:",
                "    i: Impl = Impl(4)",
                "    print(i.doubled)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_t2_protocol_annotation_is_permissive(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "proto.py",
        "\n".join(
            [
                "from typing import Protocol",
                "",
                "class Named(Protocol):",
                "    def name(self) -> str: ...",
                "",
                "def greet(n: Named) -> str:",
                "    return 'hi'",
                "",
                "class User(Named):",
                "    n: str",
                "",
                "    def __init__(self, n: str) -> None:",
                "        self.n = n",
                "",
                "    def name(self) -> str:",
                "        return self.n",
                "",
                "class Alias(Named):",
                "    tag: str",
                "",
                "    def __init__(self, tag: str) -> None:",
                "        self.tag = tag",
                "",
                "    def name(self) -> str:",
                "        return self.tag",
                "",
                "def run() -> int:",
                "    u: User = User('q')",
                "    a: Alias = Alias('z')",
                "    print(greet(u))",
                "    print(greet(a))",
                "    print(u.name())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_t2_overload_stubs_erase(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ovl.py",
        "\n".join(
            [
                "from typing import overload",
                "",
                "@overload",
                "def pick(x: int) -> int: ...",
                "",
                "@overload",
                "def pick(x: int, y: int) -> int: ...",
                "",
                "def pick(x: int, y: int = 0) -> int:",
                "    return x + y + 1",
                "",
                "def run() -> int:",
                "    print(pick(1))",
                "    print(pick(1, 2))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_t2_dotted_spellings(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "dotted.py",
        "\n".join(
            [
                "import abc",
                "import typing",
                "",
                "class Base(abc.ABC):",
                "    @abc.abstractmethod",
                "    def go(self) -> int: ...",
                "",
                "class Named(typing.Protocol):",
                "    def name(self) -> str: ...",
                "",
                "class Impl(Base, Named):",
                "    def go(self) -> int:",
                "        return 5",
                "",
                "    def name(self) -> str:",
                "        return 'i'",
                "",
                "def pick(n: Named) -> int:",
                "    return 1",
                "",
                "def run() -> int:",
                "    print(Impl().go())",
                "    print(pick(Impl()))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_t2_protocol_fences_stay(tmp_path: Path) -> None:
    cases = [
        (
            "isinstance_proto.py",
            "\n".join(
                [
                    "from typing import Protocol",
                    "",
                    "class P(Protocol):",
                    "    def go(self) -> int: ...",
                    "",
                    "class Box:",
                    "    v: int",
                    "",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "",
                    "def run() -> int:",
                    "    b: Box = Box(1)",
                    "    print(isinstance(b, P))",
                    "    return 0",
                    "",
                ]
            ),
            "isinstance on a Protocol is not lowered",
        ),
        (
            "runtime_checkable.py",
            "\n".join(
                [
                    "from typing import Protocol, runtime_checkable",
                    "",
                    "@runtime_checkable",
                    "class Sized(Protocol):",
                    "    def size(self) -> int: ...",
                    "",
                    "def run() -> int:",
                    "    return 0",
                    "",
                ]
            ),
            "decorators are not lowered",
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
