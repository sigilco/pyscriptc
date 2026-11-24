# SPDX-License-Identifier: Apache-2.0
# Issue #493 tail: ulp, nextafter, modf, frexp, ldexp, expm1, log1p, fma,
# dist and sumprod, including the domain, range and invalid-operation arms.
import math
from collections.abc import Callable

print(math.ulp(0.0), math.ulp(1.0), math.ulp(math.inf), math.ulp(1e308))
print(math.nextafter(1.0, 2.0), math.nextafter(1.0, -2.0), math.nextafter(0.0, 1.0))
print(math.nextafter(math.inf, 0.0), math.nextafter(math.nan, 1.0), math.nextafter(1, 0.0))
print(math.modf(3.75), math.modf(-3.75), math.modf(0.0), math.modf(-0.0))
print(math.modf(math.inf), math.modf(-math.inf), math.modf(math.nan))
print(math.frexp(0.0), math.frexp(-0.0), math.frexp(8.0), math.frexp(-8.0), math.frexp(0.5))
print(math.frexp(math.inf), math.frexp(-math.inf), math.frexp(math.nan))
print(math.ldexp(0.75, 4), math.ldexp(0.75, -4), math.ldexp(1.5, 1023), math.ldexp(1.5, -1074))
print(math.ldexp(3, 2), math.ldexp(-0.0, 3))
print(math.expm1(0.0), math.expm1(1e-10), math.expm1(-1e-10), math.expm1(-745.0), math.expm1(1))
print(math.log1p(0.0), math.log1p(1e-10), math.log1p(-1e-10), math.log1p(math.e - 1.0), math.log1p(1))
print(math.log1p(math.nan))
print(math.fma(1.0, 2.0, 3.0), math.fma(0.1, 0.2, 0.3), math.fma(-1.0, 3.0, 2.0), math.fma(1, 2, 3))
print(math.fma(math.inf, math.inf, 1.0), math.fma(math.nan, 1.0, 1.0))
print(math.dist([0.0, 0.0], [3.0, 4.0]), math.dist([1, 1], [4, 5]), math.dist([1.0], [2.0]))
print(math.dist([1e308, 1e308], [0.0, 0.0]), math.dist([1e-10, 1e-10], [0.0, 0.0]))
print(math.sumprod([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), math.sumprod([1, 2], [3, 4]))
print(math.sumprod([0.1, 0.2], [0.3, 0.4]), math.sumprod([-1.0, 2.0], [3.0, -4.0]))
print(math.sumprod([1e100, 1e-100], [1e-100, 1e100]))


def check(call: Callable[[], object], label: str) -> None:
    try:
        print("math." + label, "=", call())
    except ValueError:
        print("math." + label, "-> ValueError")
    except OverflowError:
        print("math." + label, "-> OverflowError")


check(lambda: math.ldexp(1.5, 1024), "ldexp(1.5, 1024)")
check(lambda: math.ldexp(1.5, 2**64), "ldexp(1.5, 2**64)")
check(lambda: math.expm1(1000.0), "expm1(1000.0)")
check(lambda: math.log1p(-1.0), "log1p(-1.0)")
check(lambda: math.log1p(-2.0), "log1p(-2.0)")
check(lambda: math.fma(math.inf, 0.0, 1.0), "fma(inf, 0.0, 1.0)")
check(lambda: math.fma(1e308, 1e308, 1.0), "fma(1e308, 1e308, 1.0)")
check(lambda: math.dist([0.0, 0.0], [1.0]), "dist([0.0, 0.0], [1.0])")
check(lambda: math.sumprod([1.0, 2.0], [3.0]), "sumprod([1.0, 2.0], [3.0])")
