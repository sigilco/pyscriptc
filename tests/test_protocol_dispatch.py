"""Issue #445 (O3a): operator protocol dispatch on user classes.

The resolved surfaces (compare, ordering, hash, truthiness, format) run
against the CPython oracle; the refusals the decision fences stay compile
errors. Differential probe: probes/differential/protocol_dispatch.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_dispatch_eq_ne_ordering(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "cmp.py",
        "\n".join(
            [
                "class Pt:",
                "    x: int",
                "    y: int",
                "    def __init__(self, x: int, y: int) -> None:",
                "        self.x = x",
                "        self.y = y",
                "    def __eq__(self, other: Pt) -> bool:  # type: ignore[override]",
                "        return self.x == other.x and self.y == other.y",
                "    def __ne__(self, other: Pt) -> bool:  # type: ignore[override]",
                "        return True",
                "    def __lt__(self, other: Pt) -> bool:  # type: ignore[override]",
                "        return self.x < other.x",
                "    def __gt__(self, other: Pt) -> bool:  # type: ignore[override]",
                "        return self.x > other.x",
                "def probe() -> int:",
                "    a: Pt = Pt(1, 2)",
                "    b: Pt = Pt(1, 3)",
                "    print(a == a, a == b, a != b, a < b, b < a, a > b, b > a)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_dispatch_ne_reflected(tmp_path: Path) -> None:
    # A subclass on the right: CPython's subclass priority tries
    # Sub.__ne__ first; a class with no __ne__ negates the == result
    # (object.__ne__'s rule).
    path = write_case(
        tmp_path,
        "ne.py",
        "\n".join(
            [
                "class Base:",
                "    v: int",
                "    def __init__(self, v: int) -> None:",
                "        self.v = v",
                "    def __eq__(self, other: Base) -> bool:  # type: ignore[override]",
                "        return self.v == other.v",
                "class Sub(Base):",
                "    def __ne__(self, other: Base) -> bool:  # type: ignore[override]",
                "        return False",
                "def probe() -> int:",
                "    b: Base = Base(1)",
                "    s: Sub = Sub(1)",
                "    print(s != b, b != s, b != Base(2))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_dispatch_hash_bool_format(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "hbf.py",
        "\n".join(
            [
                "class P:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "    def __hash__(self) -> int:",
                "        return self.x",
                "    def __bool__(self) -> bool:",
                "        return self.x != 0",
                "    def __str__(self) -> str:",
                '        return "P"',
                "    def __format__(self, spec: str) -> str:",
                '        return "F" + spec',
                "class OnlyLen:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __len__(self) -> int:",
                "        return self.n",
                "def probe() -> int:",
                "    p: P = P(7)",
                "    print(hash(p) == 7, hash(P(-1)) == -2)",
                "    print(bool(p), bool(P(0)), not p, not P(0))",
                "    z: OnlyLen = OnlyLen(0)",
                "    print(bool(z), not z)",
                "    while z:",
                "        break",
                '    print("loop ok")',
                '    print(f"{p:x}", format(p, "q"), f"{p}")',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_protocol_dispatch_fences(tmp_path: Path) -> None:
    cases = [
        # hash on an eq-without-hash class is the unhashable fence.
        (
            "unhashable.py",
            "\n".join(
                [
                    "class W:",
                    "    v: int",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "    def __eq__(self, other: W) -> bool:  # type: ignore[override]",
                    "        return self.v == other.v",
                    "def f() -> int:",
                    "    print(hash(W(1)))",
                    "    return 0",
                    "",
                ]
            ),
            "defines __eq__ without __hash__",
        ),
        # A return of NotImplemented is the fence, not the fall-through.
        (
            "notimpl.py",
            "\n".join(
                [
                    "class C:",
                    "    v: int",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "    def __eq__(self, other: C) -> bool:  # type: ignore[override]",
                    "        return NotImplemented",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "NotImplemented",
        ),
        # Ordering with no resolvable dunder is the TypeError fence.
        (
            "noorder.py",
            "\n".join(
                [
                    "class C:",
                    "    v: int",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "    def __eq__(self, other: C) -> bool:  # type: ignore[override]",
                    "        return self.v == other.v",
                    "def f() -> int:",
                    "    a: C = C(1)",
                    "    b: C = C(2)",
                    "    print(a < b)",
                    "    return 0",
                    "",
                ]
            ),
            "< on C and C cannot decide the dunder",
        ),
        # A class dict key stays refused.
        (
            "classkey.py",
            "\n".join(
                [
                    "class C:",
                    "    v: int",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "def f() -> int:",
                    "    d: dict[C, int] = {}",
                    "    return 0",
                    "",
                ]
            ),
            "dict",
        ),
        # bool() return-type check rides the extended want table.
        (
            "badbool.py",
            "\n".join(
                [
                    "class C:",
                    "    v: int",
                    "    def __init__(self, v: int) -> None:",
                    "        self.v = v",
                    "    def __bool__(self) -> int:",
                    "        return self.v",
                    "def f() -> int:",
                    "    c: C = C(1)",
                    "    if c:",
                    "        print(1)",
                    "    return 0",
                    "",
                ]
            ),
            "C.__bool__ returns int, not bool",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_dispatch_identity_fallback(tmp_path: Path) -> None:
    # The subclass-priority reflected call: Sub.__eq__ answers a Sub == Base
    # pair because Sub is the strict subclass.
    path = write_case(
        tmp_path,
        "sub.py",
        "\n".join(
            [
                "class Base:",
                "    v: int",
                "    def __init__(self, v: int) -> None:",
                "        self.v = v",
                "    def __eq__(self, other: Base) -> bool:  # type: ignore[override]",
                "        return self.v == other.v",
                "class Sub(Base):",
                "    def __eq__(self, other: Base) -> bool:  # type: ignore[override]",
                "        return False",
                "def probe() -> int:",
                "    b: Base = Base(1)",
                "    s: Sub = Sub(1)",
                "    print(s == b, b == s)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
