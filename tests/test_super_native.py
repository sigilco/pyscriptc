"""Native zero- and two-arg super() tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_super_3_level_chain(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "s3.py",
        "\n".join(
            [
                "class A:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "    def name(self) -> str:",
                '        return "A"',
                "class B(A):",
                "    def __init__(self, x: int) -> None:",
                "        super().__init__(x + 1)",
                "    def name(self) -> str:",
                '        return super().name() + "B"',
                "class C(B):",
                "    def __init__(self, x: int) -> None:",
                "        super().__init__(x * 10)",
                "    def name(self) -> str:",
                '        return super().name() + "C"',
                "def probe() -> int:",
                "    c: C = C(2)",
                "    print(c.name())",
                "    print(c.x)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container
def test_super_two_arg_form(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "s2.py",
        "\n".join(
            [
                "class A:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "    def tag(self) -> str:",
                '        return "A"',
                "class B(A):",
                "    def __init__(self, x: int) -> None:",
                "        super(B, self).__init__(x + 1)",
                "    def tag(self) -> str:",
                '        return super(B, self).tag() + "B"',
                "def probe() -> int:",
                "    b: B = B(3)",
                "    print(b.tag())",
                "    print(b.x)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_super_fences(tmp_path: Path) -> None:
    cases = [
        (
            "s1arg.py",
            "class A:\n    pass\nclass B(A):\n    def f(self) -> int:\n        super(A).g()\n        return 1\n",
            "super() takes zero or two positional arguments",
        ),
        (
            "sbadcls.py",
            "class A:\n    pass\nclass B(A):\n    def f(self) -> int:\n        super(A, self).g()\n        return 1\n",
            "Object of class `object` has no attribute `g`",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "bin", "-o", str(tmp_path / name))
        assert got.returncode != 0, f"{name}: expected a compile refusal"
        assert text in got.stderr, f"{name}: {got.stderr}"
