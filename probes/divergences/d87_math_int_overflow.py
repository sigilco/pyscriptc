# SPDX-License-Identifier: Apache-2.0
# Divergence D86: the integer math helpers that grow a big int in CPython
# raise OverflowError here, because int is i64 (divergence 1). The domain
# errors below match CPython exactly.
import math

print(math.comb(52, 5))
try:
    print(math.comb(100, 50))
except OverflowError:
    print("OverflowError")
try:
    print(math.perm(21, 19))
except OverflowError:
    print("OverflowError")
try:
    print(math.prod([1 << 40, 1 << 40]))
except OverflowError:
    print("OverflowError")
try:
    print(math.comb(-1, 2))
except ValueError:
    print("ValueError")
try:
    print(math.perm(5, -1))
except ValueError:
    print("ValueError")
