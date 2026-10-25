"""Annotated int growth stays CPython-correct under the fallback tier (issue #677).

Annotated `int` lowers as i64 (divergence 1), so a fully annotated
definition whose ints compound (`*`, `<<`, `**` in a loop or recursion)
aborted with `OverflowError` where CPython prints the big integer. The
planner routes such definitions to the island; these tests diff the binary
against CPython.
"""

from __future__ import annotations

from pathlib import Path

from test_fallback_definitions import _compile_and_compare

FACTORIAL = """def fact(n: int) -> int:
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


print(fact(30))
"""

SHIFT_CARRY = """def bits(n: int) -> int:
    m: int = 1
    for _ in range(n):
        m = m << 1
    return m


print(bits(100))
"""

LOOP_POW = """def pows(n: int) -> int:
    r: int = 2
    for _ in range(n):
        r **= 8
    return r


print(pows(2))
print(pows(3))
"""


def test_bigint_annotated_factorial_matches_cpython(tmp_path: Path) -> None:
    native, cpython = _compile_and_compare(tmp_path, FACTORIAL, "bigint_fact")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "265252859812191058636308480000000\n"


def test_bigint_annotated_shift_carry_matches_cpython(tmp_path: Path) -> None:
    native, cpython = _compile_and_compare(tmp_path, SHIFT_CARRY, "bigint_shift")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_bigint_annotated_loop_pow_matches_cpython(tmp_path: Path) -> None:
    native, cpython = _compile_and_compare(tmp_path, LOOP_POW, "bigint_pow")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_bigint_plain_multiply_stays_native(tmp_path: Path) -> None:
    """A definition whose multiply cannot compound stays native and correct."""
    source_text = """def sq(x: int) -> int:
    return x * x


def main() -> None:
    print(sq(12))


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, "bigint_plain")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "144\n"


def test_bigint_bounded_pow_no_loop_stays_native(tmp_path: Path) -> None:
    """`x = x ** k` with a small literal exponent and no loop stays native."""
    source_text = """def raise2(x: int) -> int:
    y = x ** 8
    return y


def main() -> None:
    print(raise2(3))


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, "bigint_bounded")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "6561\n"
