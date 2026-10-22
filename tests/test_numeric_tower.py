"""R12 numeric tower: `/`, `%`, mixed int/float, `**` float, `+x`, bitwise.

`/` always returns a float, `%` and `//` keep CPython floor semantics on
ints and floats, a mixed int/float operand promotes the int through
sitofp, `**` on a float operand or a constant negative exponent lowers to
`llvm.pow.f64`, unary `+` is a no-op, and `& | ^ << >> ~` are i64 ops.
Float results come back as `str(...)` so the oracle's `%.17g` print and
the binary's float repr agree on whole floats like `8.0`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def div() -> str:
    return str(7 / 2)

def div_even() -> str:
    return str(4 / 2)

def div_float() -> str:
    return str(7.5 / 2)

def div_mixed_l() -> str:
    return str(7 / 2.5)

def div_mixed_r() -> str:
    return str(7.5 / 3)

def imod() -> int:
    return 7 % 3

def imod_neg_a() -> int:
    return -7 % 3

def imod_neg_b() -> int:
    return 7 % -3

def imod_neg_both() -> int:
    return -7 % -3

def imod_min() -> int:
    m: int = -9223372036854775807 - 1
    return m % -1

def fmod() -> str:
    return str(7.5 % 2)

def fmod_neg() -> str:
    return str(-7.5 % 2)

def fmod_mixed() -> str:
    return str(7 % 2.5)

def ffloordiv() -> str:
    return str(7.5 // 2)

def ffloordiv_neg() -> str:
    return str(-7.5 // 2)

def add_mixed() -> str:
    return str(1 + 2.5)

def sub_mixed() -> str:
    return str(2.5 - 1)

def mul_mixed() -> str:
    return str(2 * 3.5)

def lt_mixed() -> int:
    if 1 < 2.5:
        return 1
    return 0

def gt_mixed() -> int:
    if 3.5 > 3:
        return 1
    return 0

def eq_mixed() -> int:
    if 2 == 2.0:
        return 1
    return 0

def ne_mixed() -> int:
    if 2 != 2.0:
        return 1
    return 0

def le_mixed() -> int:
    if 2.5 <= 2:
        return 1
    return 0

def ge_mixed() -> int:
    if 3 >= 3.0:
        return 1
    return 0

def pow_float_base() -> str:
    return str(2.0 ** 3)

def pow_float_exp() -> str:
    return str(2 ** 3.0)

def pow_neg_exp() -> str:
    return str(2 ** -1)

def pow_neg_both() -> str:
    return str((-2) ** -2)

def pow_frac() -> str:
    return str(4.0 ** 0.5)

def pow_int() -> int:
    return 2 ** 3

def unary_pos() -> int:
    x: int = 7
    return +x

def unary_pos_float() -> str:
    return str(+2.5)

def invert() -> int:
    return ~5

def invert_neg() -> int:
    return ~-1

def band() -> int:
    return 6 & 3

def bor() -> int:
    return 5 | 2

def bxor() -> int:
    return 5 ^ 3

def shl() -> int:
    return 1 << 4

def shl_neg() -> int:
    return -1 << 2

def shr() -> int:
    return 32 >> 2

def shr_neg() -> int:
    return -8 >> 1

def aug() -> int:
    n: int = 17
    n %= 5
    n <<= 2
    n |= 3
    n &= 6
    n ^= 1
    n >>= 1
    return n

def aug_div() -> str:
    x: float = 10.0
    x /= 4
    return str(x)
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_numeric_tower_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "numeric_tower.py", _PROBE)
    expected = {
        "div": "3.5",
        "div_even": "2.0",
        "div_float": "3.75",
        "div_mixed_l": "2.8",
        "div_mixed_r": "2.5",
        "imod": "1",
        "imod_neg_a": "2",
        "imod_neg_b": "-2",
        "imod_neg_both": "-1",
        "imod_min": "0",
        "fmod": "1.5",
        "fmod_neg": "0.5",
        "fmod_mixed": "2.0",
        "ffloordiv": "3.0",
        "ffloordiv_neg": "-4.0",
        "add_mixed": "3.5",
        "sub_mixed": "1.5",
        "mul_mixed": "7.0",
        "lt_mixed": "1",
        "gt_mixed": "1",
        "eq_mixed": "1",
        "ne_mixed": "0",
        "le_mixed": "0",
        "ge_mixed": "1",
        "pow_float_base": "8.0",
        "pow_float_exp": "8.0",
        "pow_neg_exp": "0.5",
        "pow_neg_both": "0.25",
        "pow_frac": "2.0",
        "pow_int": "8",
        "unary_pos": "7",
        "unary_pos_float": "2.5",
        "invert": "-6",
        "invert_neg": "0",
        "band": "2",
        "bor": "7",
        "bxor": "6",
        "shl": "16",
        "shl_neg": "-4",
        "shr": "8",
        "shr_neg": "-4",
        "aug": "1",
        "aug_div": "2.5",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_numeric_tower_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "bool_add.py",
            "def probe() -> int:\n    return True + 1\n",
            "bool arithmetic is not lowered",
        ),
        (
            "percent_non_str.py",
            "def probe() -> int:\n    xs: list[int] = [1]\n    return xs % 3\n",
            "% formatting needs a str left operand",
        ),
        (
            "float_pow.py",
            'def probe() -> int:\n    return "ab" ** 2\n',
            "** is only lowered for int",
        ),
        (
            "invert_float.py",
            "def probe() -> float:\n    return ~2.0\n",
            "unary `~` on float is not lowered",
        ),
        (
            "band_float.py",
            "def probe() -> int:\n    return 5 & 2.0\n",
            "`&` on int and float is not lowered",
        ),
    ]
    skip_check = {"float_pow.py", "invert_float.py", "band_float.py", "percent_non_str.py"}
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        args = ["compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll")]
        if name in skip_check:
            args = ["compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll")]
        got = run_cli(*args)
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
