# SPDX-License-Identifier: Apache-2.0
"""`int / int` past 2**53 (divergence 88).

CPython divides the exact integers and rounds the quotient once. The
lowerer widens both operands to float first, so the wide dividend rounds
before the division: 9007199254740993 / 3 is the exact integer
3002399751580331, which CPython prints as 3002399751580331.0.
"""

big: int = 9007199254740993
three: int = 3
print(big / three)
