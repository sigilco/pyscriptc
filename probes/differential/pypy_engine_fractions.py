# Issue #668: the PyPy island engine seam. The probe runs on the pinned
# CPython oracle and, in the PyPy lane, on the compiled program linked
# against the PyPy runtime pack: the stdout, stderr and exit code must
# match byte for byte.
#
# The program exercises the seam's own surface: the module import path
# (`fractions` is an island import), the engine's exception objects (the
# `args` read the seam does with `PyObject_GetAttrString`, portable across
# both engines' C APIs), and the integer/float/ratio arithmetic the island
# routes through the engine's own objects.
import fractions

f = fractions.Fraction(3, 4)
s: str = str(f + fractions.Fraction(1, 6))
print(s)
print(f.numerator, f.denominator)
print(f == fractions.Fraction(6, 8))
print(float(f))
print(f + 1)

# The exception path: the seam reads the exception's `args` tuple.
try:
    raise ValueError("boom", 2)
except ValueError as exc:
    print(exc.args)
    print(type(exc).__name__)

try:
    fractions.Fraction(1, 0)
except ZeroDivisionError as exc:
    print(type(exc).__name__)
