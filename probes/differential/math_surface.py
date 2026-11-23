# SPDX-License-Identifier: Apache-2.0
# Issue #493: the math surface completion (the integer helpers, the float
# helpers, the gamma family, log with a base, fsum and prod).
# math.comb(100, 50) is excluded: on a 64-bit i64 target it raises
# OverflowError where the host oracle grows a big int (divergence D87).
import math

print(math.comb(5, 2), math.comb(5, 0), math.comb(0, 0), math.comb(5, 6))
print(math.comb(10, 3), math.comb(52, 5))
print(math.perm(5, 2), math.perm(5, 0), math.perm(5, 6))
print(math.perm(10, 3), math.perm(52, 2))
print(math.isqrt(0), math.isqrt(1), math.isqrt(17), math.isqrt(2**62))
print(math.copysign(3.0, -1.0), math.copysign(3.0, 1.0), math.copysign(0.0, -1.0))
print(math.cbrt(27.0), math.cbrt(-8.0), math.cbrt(0.0), math.cbrt(2.0))
print(math.erf(0.0), math.erf(0.5), math.erf(-0.5), math.erf(1.0))
print(math.erfc(0.0), math.erfc(0.5), math.erfc(-0.5), math.erfc(2.0))
print(math.gamma(0.5), math.gamma(1.5), math.gamma(2.5), math.gamma(-0.5))
print(math.gamma(5.0), math.gamma(10.0), math.gamma(100.0))
print(math.lgamma(0.5), math.lgamma(1.0), math.lgamma(2.0), math.lgamma(10.0))
print(math.lgamma(-0.5), math.lgamma(-2.5))
print(math.log(8.0, 2.0), math.log(100.0, 10.0), math.log(2.0, 0.5))
print(math.log(10.0, 3.0), math.log(8, 2), math.log(1.0, 2.0))
print(math.floor(3), math.trunc(3), math.ceil(3))
print(math.floor(-3), math.trunc(-3), math.ceil(-3))
print(math.floor(2**60 + 1), math.trunc(2**60 + 1), math.ceil(2**60 + 1))
print(math.fsum([0.1, 0.2, 0.3]), math.fsum([1e16, 1.0, -1e16]))
print(math.fsum([1.5, 2.5]), math.fsum([1, 2, 3]), math.fsum([1e-16, 1.0, 2.0]))
print(math.prod([1, 2, 3]), math.prod([1.0, 2.0]), math.prod([2, 3, 4]))
print(math.prod([0.5, 4.0, 2.0]), math.prod([-1, 2, -3]))
try:
    print(math.comb(-1, 2))
except ValueError:
    print("comb(-1, 2) -> ValueError")
try:
    print(math.comb(5, -1))
except ValueError:
    print("comb(5, -1) -> ValueError")
try:
    print(math.perm(-1, 2))
except ValueError:
    print("perm(-1, 2) -> ValueError")
try:
    print(math.perm(5, -1))
except ValueError:
    print("perm(5, -1) -> ValueError")
try:
    print(math.isqrt(-1))
except ValueError:
    print("isqrt(-1) -> ValueError")
try:
    print(math.gamma(0.0))
except ValueError:
    print("gamma(0.0) -> ValueError")
try:
    print(math.gamma(-1.0))
except ValueError:
    print("gamma(-1.0) -> ValueError")
try:
    print(math.lgamma(0.0))
except ValueError:
    print("lgamma(0.0) -> ValueError")
try:
    print(math.lgamma(-2.0))
except ValueError:
    print("lgamma(-2.0) -> ValueError")
try:
    print(math.log(0.0, 2.0))
except ValueError:
    print("log(0.0, 2.0) -> ValueError")
try:
    print(math.log(8.0, 0.0))
except ValueError:
    print("log(8.0, 0.0) -> ValueError")
try:
    print(math.log(-4.0, 2.0))
except ValueError:
    print("log(-4.0, 2.0) -> ValueError")
try:
    print(math.log(8.0, 1.0))
except ZeroDivisionError:
    print("log(8.0, 1.0) -> ZeroDivisionError")
try:
    print(math.fsum([1e308, 1e308]))
except OverflowError:
    print("fsum([1e308, 1e308]) -> OverflowError")
