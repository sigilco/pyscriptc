"""math: the script surface, diffed against the CPython oracle byte for byte."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, lines: list[str]) -> None:
    source = "\n".join(["import math", "", "def run() -> int:", *[f"    {line}" for line in lines], "    return 0", ""])
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def _raises(call: str) -> list[str]:
    return [
        "try:",
        f"    print({call})",
        "except ValueError:",
        "    print('ValueError')",
        "except OverflowError:",
        "    print('OverflowError')",
    ]


def test_math_roots_and_rounding(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "round.py",
        [
            "print(math.sqrt(2.0), math.sqrt(16), math.fabs(-3.5), math.fabs(-2))",
            "print(math.floor(2.7), math.floor(-2.1), math.ceil(2.1))",
            "print(math.ceil(-2.7), math.trunc(-2.9), math.trunc(2.9))",
            "print(math.pow(2.0, 10.0), math.pow(2, 0.5), math.hypot(3.0, 4.0), math.hypot(5, 12))",
            *_raises("math.sqrt(-1.0)"),
            *_raises("math.pow(0.0, -1.0)"),
            *_raises("math.pow(-8.0, 0.5)"),
            *_raises("math.pow(10.0, 400.0)"),
            *_raises("math.floor(math.inf)"),
        ],
    )


def test_math_logs_and_exp(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "logs.py",
        [
            "print(math.log(math.e), math.log(10.0), math.log2(8.0))",
            "print(math.log10(1000.0), math.log2(1), math.log10(100))",
            "print(math.exp(0.0), math.exp(1.0), math.exp(-745.0), math.exp(2))",
            *_raises("math.log(0.0)"),
            *_raises("math.log(-1.0)"),
            *_raises("math.log2(0.0)"),
            *_raises("math.log10(-5.0)"),
            *_raises("math.exp(1000.0)"),
        ],
    )


def test_math_trig_and_atan2(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "trig.py",
        [
            "print(math.sin(0.5), math.cos(0.5), math.tan(0.5), math.sin(math.pi), math.cos(math.pi))",
            "print(math.atan2(1.0, 1.0), math.atan2(-1.0, -1.0), math.atan2(0.0, -1.0), math.atan2(-0.0, -1.0))",
            "print(math.atan2(1, 0), math.atan2(0.0, 0.0), math.atan2(math.inf, math.inf), math.atan2(math.nan, 1.0))",
        ],
    )


def test_math_constants_and_predicates(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "pred.py",
        [
            "print(math.pi, math.e, math.tau, math.inf, -math.inf, math.nan)",
            "print(math.isnan(math.nan), math.isnan(1.0))",
            "print(math.isinf(math.inf), math.isinf(-math.inf), math.isinf(1.0))",
            "print(math.isfinite(1.0), math.isfinite(math.inf), math.isfinite(math.nan))",
        ],
    )


def test_math_gcd(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "gcd.py",
        [
            "print(math.gcd(12, 18), math.gcd(-12, 18), math.gcd(12, -18))",
            "print(math.gcd(0, 5), math.gcd(5, 0), math.gcd(0, 0))",
            "print(math.gcd(17, 5), math.gcd(2**62, 2**40), math.gcd(-9223372036854775807, 3))",
        ],
    )


def test_math_isclose(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "close.py",
        [
            "print(math.isclose(1.0, 1.0), math.isclose(0.1 + 0.2, 0.3), math.isclose(1.0, 1.1), math.isclose(1, 1))",
            "print(math.isclose(1.0, 1.1, rel_tol=0.2))",
            "print(math.isclose(0.0, 1e-10), math.isclose(0.0, 1e-10, abs_tol=1e-9))",
            "print(math.isclose(math.inf, math.inf), math.isclose(math.inf, 1e308), math.isclose(math.nan, math.nan))",
            "print(math.isclose(100.0, 101.0, abs_tol=2.0, rel_tol=0.0))",
            "try:",
            "    print(math.isclose(1.0, 1.0, rel_tol=-1.0))",
            "except ValueError:",
            "    print('ValueError')",
        ],
    )


def test_math_inverse_and_hyperbolic(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "inverse.py",
        [
            "print(math.asin(0.5), math.asin(-1.0), math.asin(1.0), math.asin(1))",
            "print(math.acos(1.0), math.acos(0.5), math.acos(-0.5))",
            "print(math.atan(1.0), math.atan(-1.0), math.atan(0.0))",
            "print(math.sinh(0.5), math.cosh(0.5), math.tanh(0.5))",
            "print(math.asinh(1.0), math.acosh(1.0), math.acosh(2.0))",
            "print(math.atanh(0.5), math.atanh(-0.5), math.atanh(0.0))",
            "print(math.asin(float('nan')), math.acos(float('nan')), math.atan(float('nan')), math.asinh(float('nan')))",
            *_raises("math.asin(1.5)"),
            *_raises("math.asin(-1.5)"),
            *_raises("math.acos(2.0)"),
            *_raises("math.acosh(0.5)"),
            *_raises("math.atanh(1.0)"),
            *_raises("math.atanh(-1.0)"),
            *_raises("math.atanh(2.0)"),
            *_raises("math.sinh(1000.0)"),
            *_raises("math.cosh(1000.0)"),
        ],
    )


def test_math_degrees_radians_fmod(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "anglemod.py",
        [
            "print(math.degrees(math.pi), math.degrees(0.0), math.degrees(-math.pi / 2))",
            "print(math.radians(180.0), math.radians(0.0), math.radians(90.0))",
            "print(math.fmod(7.5, 2.0), math.fmod(-7.5, 2.0), math.fmod(7.5, -2.0), math.fmod(7, 2))",
            "print(math.fmod(5.0, math.inf), math.fmod(-0.0, 1.0))",
            *_raises("math.fmod(1.0, 0.0)"),
            *_raises("math.fmod(math.inf, 1.0)"),
            *_raises("math.fmod(float('nan'), 1.0)"),
        ],
    )


def test_math_factorial_lcm(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "intfns.py",
        [
            "print(math.factorial(0), math.factorial(1), math.factorial(5), math.factorial(20))",
            "print(math.lcm(4, 6), math.lcm(-4, 6), math.lcm(4, -6), math.lcm(-4, -6))",
            "print(math.lcm(0, 5), math.lcm(5, 0), math.lcm(0, 0))",
            "print(math.lcm(7, 13), math.lcm(1024, 768), math.lcm(2**31, 2**17))",
            *_raises("math.factorial(-1)"),
            *_raises("math.factorial(21)"),
            *_raises("math.lcm(3037000500, 3037000499)"),
        ],
    )
