# SPDX-License-Identifier: Apache-2.0
"""Integer arithmetic, division and rounding.

The plain integer surface every target has to agree on: the arithmetic
operators, the floor division, the modulo and the built-in rounding
helpers. Nothing here touches the host: every value is a literal or a
computed integer.
"""

a: int = 17
b: int = 5

print(a + b)
print(a - b)
print(a * b)
print(a // b)
print(a % b)
print(-a // b)
print(-a % b)
print(a**3)
print(divmod(a, b))
print(abs(-a))
print(max(a, b), min(a, b))
print(round(3.5), round(2.5), round(-2.5))
print(int(7.9), int(-7.9))
print(pow(a, b, 1000))
print(sum(range(10)))
