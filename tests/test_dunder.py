"""R25: dunder dispatch — __str__/__repr__/__eq__/__len__/__iter__/__next__/__enter__/__exit__,
super(), and multiple inheritance."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dunder_str_repr_len_eq(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "p.py",
        "\n".join(
            [
                "from __future__ import annotations",
                "class P:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "    def __str__(self) -> str:",
                '        return "P(" + str(self.x) + ")"',
                "    def __repr__(self) -> str:",
                '        return "Rep(" + str(self.x) + ")"',
                "    def __len__(self) -> int:",
                "        return self.x",
                "    def __eq__(self, other: P) -> bool:  # type: ignore[override]",
                "        return self.x == other.x",
                "def probe() -> int:",
                "    p: P = P(3)",
                "    print(str(p))",
                "    print(repr(p))",
                "    print(len(p))",
                "    print(p == P(3))",
                "    print(p != P(4))",
                "    print(p)",
                '    print(f"F{p}")',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_super_and_multiple_inheritance(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "q.py",
        "\n".join(
            [
                "class A:",
                "    a: int",
                "    def __init__(self, a: int) -> None:",
                "        self.a = a",
                "    def am(self) -> int:",
                "        return self.a",
                "class B:",
                "    b: int",
                "    def __init__(self, b: int) -> None:",
                "        self.b = b",
                "    def bm(self) -> int:",
                "        return self.b",
                "class C(A, B):",
                "    def __init__(self, a: int, b: int) -> None:",
                "        self.a = a",
                "        self.b = b",
                "class P:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "    def hello(self) -> str:",
                '        return "hi" + str(self.x)',
                "class Q(P):",
                "    def __init__(self, x: int) -> None:",
                "        super().__init__(x + 1)",
                "    def hello(self) -> str:",
                '        return super().hello() + "!"',
                "def probe() -> int:",
                "    q: Q = Q(5)",
                "    print(q.hello())",
                "    print(q.x)",
                "    c: C = C(1, 2)",
                "    print(c.am() + c.bm())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dunder_iter_next(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "it.py",
        "\n".join(
            [
                "class It:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __iter__(self) -> It:",
                "        return self",
                "    def __next__(self) -> int:",
                "        if self.n <= 0:",
                "            raise StopIteration()",
                "        self.n = self.n - 1",
                "        return self.n + 1",
                "class Bag:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __iter__(self) -> It:",
                "        return It(self.n)",
                "def probe() -> int:",
                "    for i in Bag(3):",
                "        print(i)",
                "    it: It = It(2)",
                "    print(next(it))",
                "    print(next(it, 9))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_with_custom_context_manager(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "cm.py",
        "\n".join(
            [
                "class CM:",
                "    swallow: int",
                "    def __init__(self, swallow: int) -> None:",
                "        self.swallow = swallow",
                "    def __enter__(self) -> str:",
                '        return "in"',
                "    def __exit__(self, et: str | None, ev: Exception | None, tb: str | None) -> bool:"
                "  # type: ignore[exit-return]",
                "        if et is None:",
                '            print("clean exit")',
                "        elif ev is not None:",
                '            print("exit saw an error")',
                "        return self.swallow != 0",
                "def probe() -> int:",
                "    print(noisy(False))",
                "    print(noisy(True))",
                "    return 0",
                "def noisy(raise_it: bool) -> int:",
                "    with CM(1 if raise_it else 0) as tag:",
                "        print(tag)",
                "        if raise_it:",
                '            raise ValueError("boom")',
                '        print("body end")',
                '    print("after with")',
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_dunder_fences(tmp_path: Path) -> None:
    cases = [
        (
            "badexit.py",
            "\n".join(
                [
                    "class CM:",
                    "    def __enter__(self) -> str:",
                    '        return "in"',
                    "    def __exit__(self, et: str, ev: str, tb: str) -> bool:",
                    "        return False",
                    "def f() -> int:",
                    "    with CM() as tag:",
                    "        print(tag)",
                    "    return 0",
                    "",
                ]
            ),
            "CM.__exit__ takes (Optional[str], Optional[Exception], Optional[str])",
        ),
        (
            "badstr.py",
            "\n".join(
                [
                    "class P:",
                    "    def __str__(self) -> int:",
                    "        return 1",
                    "def f() -> int:",
                    "    p: P = P()",
                    "    print(str(p))",
                    "    return 1",
                    "",
                ]
            ),
            "P.__str__ returns int, not str",
        ),
        (
            "badsuper.py",
            "def f() -> int:\n    super().m()\n    return 1\n",
            "super is not lowered",
        ),
        (
            "badmro.py",
            "\n".join(
                [
                    "class A:",
                    "    x: int",
                    "class B(A):",
                    "    pass",
                    "class C(A, B):",
                    "    pass",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "an inconsistent method resolution order",
        ),
        (
            "conflict.py",
            "\n".join(
                [
                    "class A:",
                    "    x: int",
                    "class B:",
                    "    x: str",
                    "class C(A, B):",
                    "    pass",
                    "def f() -> int:",
                    "    return 1",
                    "",
                ]
            ),
            "base field x has conflicting types",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
