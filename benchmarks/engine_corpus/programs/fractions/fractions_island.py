"""The fractions corpus program: the island module the compiler routes to the engine.

Every corpus program is a pair: this module (declared `island` in the
program's `pyscriptc.toml`, so every definition in it runs on the selected
engine across the island seam) and a thin typed entry `fractions_entry.py`
that calls it and prints the results. The runner compiles the entry with
`--island`, runs the binary, and byte-compares stdout, stderr and the exit
code with CPython running the same entry.
"""

from fractions import Fraction


def frac_sum(a_num: int, a_den: int, b_num: int, b_den: int) -> str:
    f = Fraction(a_num, a_den) + Fraction(b_num, b_den)
    return str(f)


def frac_scale(num: int, den: int, n: int) -> str:
    f = Fraction(num, den) * n
    return str(f)
