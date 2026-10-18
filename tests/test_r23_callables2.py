"""R23 callables-as-values pack 2: `h = f` rebinds, `functools.partial`,
user `@dec` decorators, and callables stored in dicts.

Each oracle test compiles a probe and diffs its stdout against CPython.
Callable cells (pair + env) are heap-allocated so they escape frames and
containers safely; module-level callable binds live in `ps_global_*_ptr`
slots initialized at the top of `main`.
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


def test_unannotated_function_rebind(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_rebind.py",
        "\n".join(
            [
                "def f(x: int) -> int:",
                "    return x * 2",
                "",
                "def probe() -> int:",
                "    h = f",
                "    h2 = h",
                "    return h(20) + h2(1)",
                "",
            ]
        ),
    )


def test_annotated_callable_local(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_annotated.py",
        "\n".join(
            [
                "from typing import Callable",
                "",
                "def f(x: int) -> int:",
                "    return x + 5",
                "",
                "def probe() -> int:",
                "    h: Callable[[int], int] = f",
                "    return h(10)",
                "",
            ]
        ),
    )


def test_callable_dict_member(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_dict.py",
        "\n".join(
            [
                "from typing import Callable",
                "",
                "def f(x: int) -> int:",
                "    return x * 3",
                "",
                "def probe() -> int:",
                '    d: dict[str, Callable[[int], int]] = {"a": f, "b": lambda x: x * x}',
                '    return d["a"](7) + d["b"](4)',
                "",
            ]
        ),
    )


def test_partial_binds_arguments(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_partial.py",
        "\n".join(
            [
                "import functools",
                "",
                "def add(a: int, b: int, c: int) -> int:",
                "    return a * 100 + b * 10 + c",
                "",
                "def probe() -> int:",
                "    p = functools.partial(add, 4)",
                "    p2 = functools.partial(add, 1, 2)",
                "    return p(0, 2) + p2(3)",
                "",
            ]
        ),
    )


def test_module_level_callable_binds(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_module_bind.py",
        "\n".join(
            [
                "import functools",
                "",
                "def f(x: int) -> int:",
                "    return x * 2",
                "",
                "def add(a: int, b: int) -> int:",
                "    return a + b",
                "",
                "h = f",
                "p = functools.partial(add, 7)",
                "",
                "def probe() -> int:",
                "    return h(10) + p(1)",
                "",
            ]
        ),
    )


def test_module_user_decorator(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_module_dec.py",
        "\n".join(
            [
                "from typing import Callable",
                "",
                "def dec(f: Callable[[int], int]) -> Callable[[int], int]:",
                "    def wrapped(x: int) -> int:",
                "        return f(x) + 100",
                "    return wrapped",
                "",
                "@dec",
                "def g(x: int) -> int:",
                "    return x * 2",
                "",
                "def probe() -> int:",
                "    return g(5)",
                "",
            ]
        ),
    )


def test_nested_user_decorator(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_nested_dec.py",
        "\n".join(
            [
                "from typing import Callable",
                "",
                "def dec(f: Callable[[int], int]) -> Callable[[int], int]:",
                "    def wrapped(x: int) -> int:",
                "        return f(x) * 10",
                "    return wrapped",
                "",
                "def outer() -> int:",
                "    @dec",
                "    def g(x: int) -> int:",
                "        return x + 1",
                "    return g(4)",
                "",
                "def probe() -> int:",
                "    return outer()",
                "",
            ]
        ),
    )


def test_rebind_to_different_function(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_swap.py",
        "\n".join(
            [
                "def f(x: int) -> int:",
                "    return x + 1",
                "",
                "def g(x: int) -> int:",
                "    return x + 2",
                "",
                "def probe() -> int:",
                "    h = f",
                "    a: int = h(1)",
                "    h = g",
                "    return a * 100 + h(1)",
                "",
            ]
        ),
    )


def test_partial_through_value_call(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r23_partial_value.py",
        "\n".join(
            [
                "import functools",
                "from typing import Callable",
                "",
                "def mul(a: int, b: int) -> int:",
                "    return a * b",
                "",
                "def probe() -> int:",
                "    p: Callable[[int], int] = functools.partial(mul, 6)",
                "    return p(7)",
                "",
            ]
        ),
    )


def test_stacked_user_decorators_stay_fenced(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "r23_stacked.py",
        "\n".join(
            [
                "def dec(f):",
                "    return f",
                "",
                "@dec",
                "@dec",
                "def probe() -> int:",
                "    return 1",
                "",
            ]
        ),
        "stacked decorators are not lowered",
    )


def test_mismatched_decorator_stays_fenced(tmp_path: Path) -> None:
    _fence(
        tmp_path,
        "r23_bad_dec.py",
        "\n".join(
            [
                "def deco(fn: int) -> int:",
                "    return fn",
                "",
                "@deco",
                "def probe() -> int:",
                "    return 1",
                "",
            ]
        ),
        "user decorators are not lowered",
    )
