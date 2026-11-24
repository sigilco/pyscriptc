# SPDX-License-Identifier: Apache-2.0
# Divergence D88: sumprod over int sequences overflows the i64 accumulator
# instead of growing a Python bigint (divergence 1). The float row and the
# length checks below match CPython exactly.
import math

print(math.sumprod([1, 2], [3, 4]))
try:
    print(math.sumprod([2**63], [1]))
except OverflowError:
    print("OverflowError")
try:
    print(math.sumprod([1.0, 2.0], [3.0]))
except ValueError:
    print("ValueError")
