"""operator: the arithmetic callables lower as the operators they name."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, lines: list[str]) -> None:
    source = "\n".join(
        ["import operator", "", "def run() -> int:", *[f"    {line}" for line in lines], "    return 0", ""]
    )
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_operator_arithmetic(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "arith.py",
        [
            "print(operator.add(2, 3), operator.sub(2, 3), operator.mul(4, 5), operator.neg(7), operator.neg(-2.5))",
            "print(operator.add(1.5, 2.0), operator.sub(1.5, 2), operator.mul(1.5, 2), operator.add('a', 'b'))",
            "print(operator.floordiv(7, 2), operator.floordiv(-7, 2))",
            "print(operator.floordiv(7, -2), operator.floordiv(7.5, 2.0))",
        ],
    )


def test_operator_truediv_and_mod(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "div.py",
        [
            "print(operator.truediv(7, 2), operator.truediv(-7, 2))",
            "print(operator.truediv(1.0, 3.0), operator.truediv(6, 3))",
            "print(operator.mod(7, 3), operator.mod(-7, 3))",
            "print(operator.mod(7, -3), operator.mod(7.5, 2.0), operator.mod(-7.5, 2.0))",
            "try:",
            "    print(operator.truediv(1, 0))",
            "except ZeroDivisionError:",
            "    print('ZeroDivisionError')",
            "try:",
            "    print(operator.mod(1, 0))",
            "except ZeroDivisionError:",
            "    print('ZeroDivisionError')",
            "try:",
            "    print(operator.floordiv(1, 0))",
            "except ZeroDivisionError:",
            "    print('ZeroDivisionError')",
        ],
    )


def test_operator_getters_stay_fenced(tmp_path: Path) -> None:
    for name, call in (("itemgetter", "operator.itemgetter(0)"), ("attrgetter", "operator.attrgetter('x')")):
        path = write_case(
            tmp_path, f"{name}.py", f"import operator\n\ndef run() -> int:\n    f = {call}\n    return 0\n"
        )
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0
        assert "PS2" in got.stderr
