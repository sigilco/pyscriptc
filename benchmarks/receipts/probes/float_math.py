# SPDX-License-Identifier: Apache-2.0
"""Float arithmetic and the math module.

The float surface: the arithmetic operators, the comparisons and the
`math` helpers. Every value is a literal or a computed float, so the
output does not depend on the host's floating point mode. The results
are printed through `repr`-shaped formatting, so the comparison is on
the exact digits.
"""

import math

a: float = 1.5
b: float = 0.25

print(a + b, a - b, a * b, a / b)
print(-a, abs(-a))
print(a > b, a == b, a != b)
print(math.floor(a), math.ceil(a), math.trunc(a))
print(math.sqrt(2.0))
print(math.floor(math.pi))
print(round(math.pi, 5))
print(math.fmod(7.5, 2.0))
print(math.gcd(12, 18), math.lcm(4, 6))
print(math.floor(math.exp(1.0)))
print(math.log(math.e) == 1.0)
