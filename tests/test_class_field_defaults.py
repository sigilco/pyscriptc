# SPDX-License-Identifier: Apache-2.0
"""Constant field defaults and `field(default=...)` (issue #444, O2b).

A plain class's `x: T = <constant>` is a class attribute in CPython, and
an instance read falls back to it until the instance writes its own. The
lowered class declares the instance field from the class body's
annotation, seeds the slot with the constant in the `__init__`
(synthesized when the class has no written one), and lets the instance
field shadow the class attribute afterwards.

`dataclasses.field(default=<constant>)` lowers like the bare spelling;
`metadata=` is accepted and dropped. `self.x = ...` writes from any
method prove the field, and a type conflict between the class attribute
and the field fences.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[dict[str, str]]:
    return lower_source(source, "case.py")["fences"]


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run"), got.stdout


def test_const_default_seeds_and_shadows(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "seed_shadow.py",
        "\n".join(
            [
                "class C:",
                "    n: int = 5",
                "",
                "    def __init__(self) -> None:",
                "        self.n = self.n + 1",
                "",
                "def run() -> int:",
                "    return C().n * 10 + C.n",
                "",
            ]
        ),
    )


def test_method_write_proves_the_const_field(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "method_write.py",
        "\n".join(
            [
                "class C:",
                "    n: int = 5",
                "",
                "    def bump(self) -> int:",
                "        self.n = self.n + 1",
                "        return self.n",
                "",
                "def run() -> int:",
                "    return C().bump() * 10 + C.n",
                "",
            ]
        ),
    )


def test_seeding_init_is_synthesized_when_absent(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "synth_init.py",
        "\n".join(
            [
                "class C:",
                "    n: int = 5",
                "",
                "    def get(self) -> int:",
                "        self.n = self.n + 1",
                "        return self.n",
                "",
                "def run() -> int:",
                "    return C().get() + C().get()",
                "",
            ]
        ),
    )


def test_defaults_declared_in_any_order(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "any_order.py",
        "\n".join(
            [
                "class C:",
                "    late: int",
                "    early: int = 3",
                "",
                "    def __init__(self) -> None:",
                "        self.late = self.early * 2",
                "",
                "def run() -> int:",
                "    return C().late + C.early",
                "",
            ]
        ),
    )


def test_const_default_inherited_and_shadowed(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "inherited.py",
        "\n".join(
            [
                "class Base:",
                "    n: int = 1",
                "",
                "",
                "class Sub(Base):",
                "    def __init__(self) -> None:",
                "        self.n = 5",
                "",
                "def run() -> int:",
                "    return Sub().n * 10 + Base.n",
                "",
            ]
        ),
    )


def test_field_default_keyword_lower(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "field_default.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field",
                "",
                "",
                "@dataclass",
                "class C:",
                "    a: int = field(default=7)",
                "    b: str = field(default='hi')",
                "    plain: float = 1.5",
                "",
                "def run() -> int:",
                "    c: C = C()",
                "    return c.a + len(c.b) + int(c.plain) + C(3).a",
                "",
            ]
        ),
    )


def test_field_metadata_keyword_is_dropped(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "field_metadata.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field",
                "",
                "",
                "@dataclass",
                "class C:",
                "    a: int = field(default=2, metadata={'fmt': 'hex'})",
                "",
                "def run() -> int:",
                "    return C().a",
                "",
            ]
        ),
    )


def test_field_default_mixed_with_factory(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "field_mix.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field",
                "",
                "",
                "@dataclass",
                "class C:",
                "    a: int = field(default=1)",
                "    xs: list[int] = field(default_factory=list)",
                "    b: str = 'x'",
                "",
                "def run() -> int:",
                "    one: C = C()",
                "    one.xs.append(3)",
                "    two: C = C(2, [7], 'y')",
                "    return one.a + two.a + len(two.b) + len(one.xs) + len(two.xs)",
                "",
            ]
        ),
    )


def test_type_conflict_between_const_and_field_fences() -> None:
    fences = _fences("class C:\n    x: int = 1\n    x: str\n\n    def __init__(self) -> None:\n        self.x = 'a'\n")
    assert any(fence["code"] == "PS1003" and "C.x" in fence["message"] for fence in fences)


def test_optional_const_attribute_seeds_the_field(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "optional_const.py",
        "\n".join(
            [
                "class C:",
                "    a: int | None = None",
                "",
                "    def fill(self) -> None:",
                "        self.a = 3",
                "",
                "def run() -> int:",
                "    c: C = C()",
                "    before: int = 0 if c.a is None else c.a",
                "    c.fill()",
                "    after: int = 0 if c.a is None else c.a",
                "    return before + after",
                "",
            ]
        ),
    )


def test_tuple_const_attribute_reads_through_the_class(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "tuple_const.py",
        "\n".join(
            [
                "class C:",
                "    pair: tuple[int, int] = (1, 2)",
                "",
                "def run() -> int:",
                "    c: C = C()",
                "    return C.pair[0] * 10 + C.pair[1] + c.pair[0]",
                "",
            ]
        ),
    )


def test_dataclass_tuple_default_lowers(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "dc_tuple_default.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field",
                "",
                "",
                "@dataclass",
                "class C:",
                "    pair: tuple[int, int] = (1, 2)",
                "    spelled: tuple[int, str] = field(default=(3, 'x'))",
                "",
                "def run() -> int:",
                "    c: C = C()",
                "    return c.pair[0] + c.pair[1] + c.spelled[0] + len(c.spelled[1])",
                "",
            ]
        ),
    )


def test_plain_class_field_call_fences_instead_of_crashing() -> None:
    fences = _fences("from dataclasses import field\n\n\nclass C:\n    a: int = field(default=7)\n")
    assert any(fence["code"] == "PS2002" for fence in fences)


def test_tuple_default_shape_mismatch_fences() -> None:
    fences = _fences(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass C:\n    a: tuple[int, int] = (1, 2, 3)\n"
    )
    assert any(fence["code"] == "PS2002" for fence in fences)


def test_field_default_non_constant_fences() -> None:
    fences = _fences(
        "def make() -> int:\n"
        "    return 1\n"
        "\n"
        "\n"
        "from dataclasses import dataclass, field\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class C:\n"
        "    a: int = field(default=make())\n"
    )
    assert any(fence["code"] == "PS2002" for fence in fences)


def test_field_rejects_both_default_and_factory() -> None:
    fences = _fences(
        "from dataclasses import dataclass, field\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class C:\n"
        "    xs: list[int] = field(default=[], default_factory=list)\n"
    )
    assert any(fence["code"] == "PS2002" for fence in fences)


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/class_field_defaults.py, the AGENTS.md
    rule)."""
    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "class_field_defaults.py"
    out = str(tmp_path / "probe.bin")
    got = run_cli("compile", str(probe), "--skip-check", "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
