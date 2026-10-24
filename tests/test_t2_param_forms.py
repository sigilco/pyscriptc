"""T2-3 signature-shape normalization: `/` positional-only and `*`
keyword-only parameters lower into the named-parameter machinery.

Semantics follow CPython at bind time (in `_fill_args`/`_fill_varargs`):
positional-only parameters refuse keywords, keyword-only parameters refuse
positional slots and must be passed by name. `**kwargs` capture stays
fenced (T2-4). Each oracle test diffs compiled stdout against CPython.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def _fence(tmp_path: Path, name: str, source: str, text: str) -> None:
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
    assert got.returncode == 3, got.stderr
    assert text in got.stderr, got.stderr


def test_posonly_positional_call(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_posonly.py",
        "\n".join(
            [
                "def f(a: int, /, b: int) -> int:",
                "    return a * 10 + b",
                "",
                "def probe() -> int:",
                "    return f(1, 2) + f(3, b=4)",
                "",
            ]
        ),
    )


def test_posonly_with_defaults(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_posonly_defaults.py",
        "\n".join(
            [
                "def f(a: int, /, b: int = 5, c: int = 7) -> int:",
                "    return a * 100 + b * 10 + c",
                "",
                "def probe() -> int:",
                "    return f(1) + f(1, 2) + f(1, 2, 3) + f(1, c=9)",
                "",
            ]
        ),
    )


def test_kwonly_required_and_default(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_kwonly.py",
        "\n".join(
            [
                "def f(a: int, *, k: int, k2: int = 1) -> int:",
                "    return a * 100 + k * 1000 + k2",
                "",
                "def probe() -> int:",
                "    return f(1, k=3) + f(1, k=3, k2=7) + f(2, k2=0, k=4)",
                "",
            ]
        ),
    )


def test_mixed_posonly_kwonly(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_mixed.py",
        "\n".join(
            [
                "def f(a: int, /, b: int = 5, *, k: int, k2: int = 1) -> int:",
                "    return a * 100 + b * 10 + k * 1000 + k2",
                "",
                "def probe() -> int:",
                "    return f(1, k=3) + f(1, 2, k=3, k2=7) + f(1, b=4, k=2)",
                "",
            ]
        ),
    )


def test_varargs_then_kwonly(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_varargs_kwonly.py",
        "\n".join(
            [
                "def h(a: int, *rest: int, k: int = 9) -> int:",
                "    total: int = a + k",
                "    for r in rest:",
                "        total += r",
                "    return total",
                "",
                "def probe() -> int:",
                "    return h(1, 2, 3) + h(1, 2, 3, k=10) + h(0, k=1)",
                "",
            ]
        ),
    )


def test_method_self_posonly(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_method.py",
        "\n".join(
            [
                "class P:",
                "    v: int",
                "",
                "    def __init__(self, v: int) -> None:",
                "        self.v = v",
                "",
                "    def add(self, /, x: int, *, scale: int = 2) -> int:",
                "        return self.v + x * scale",
                "",
                "def probe() -> int:",
                "    p: P = P(10)",
                "    return p.add(3) + p.add(1, scale=4)",
                "",
            ]
        ),
    )


def test_posonly_refuses_keyword(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "t2_pos_kw.py",
        "\n".join(
            [
                "def f(a: int, /, b: int) -> int:",
                "    return a + b",
                "",
                "def probe() -> int:",
                "    return f(a=1, b=2)",
                "",
            ]
        ),
        "positional-only",
    )


def test_kwonly_refuses_positional(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "t2_kw_pos.py",
        "\n".join(
            [
                "def f(a: int, *, k: int) -> int:",
                "    return a + k",
                "",
                "def probe() -> int:",
                "    return f(1, 2)",
                "",
            ]
        ),
        "expects 1 argument(s)",
    )


def test_kwonly_missing_required(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "t2_kw_missing.py",
        "\n".join(
            [
                "def f(a: int, *, k: int) -> int:",
                "    return a + k",
                "",
                "def probe() -> int:",
                "    return f(1)",
                "",
            ]
        ),
        "expects",
    )


def test_kwargs_with_kwonly_and_posonly_lowers(tmp_path: Path) -> None:
    # `**kwargs` capture lowered in T2-4 (#121), so a signature that mixes it
    # with `/`-positional and `*`-keyword parameters must compile and match
    # CPython instead of staying fenced.
    _oracle_diff(
        tmp_path,
        "t2_kwargs_mixed.py",
        "\n".join(
            [
                "def f(a: int, /, b: int = 2, *, c: int = 3, **kw: int) -> int:",
                "    return a + b + c + len(kw)",
                "",
                "def probe() -> int:",
                "    return f(1, c=4, d=5)",
                "",
            ]
        ),
    )


def test_unannotated_kwargs_still_fenced(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "t2_kwargs.py",
        "\n".join(
            [
                "def f(a: int, **kw) -> int:",
                "    return a",
                "",
                "def probe() -> int:",
                "    return f(1)",
                "",
            ]
        ),
        "keyword forwarding (**kwargs)",
    )
