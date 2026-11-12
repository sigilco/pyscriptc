# Divergence D60: int is i64 (D1), so 21! overflows where CPython grows a
# big int. The domain errors below match CPython exactly.
import math

print(math.factorial(20))
try:
    print(math.factorial(21))
except OverflowError:
    print("OverflowError")
try:
    print(math.factorial(-1))
except ValueError:
    print("ValueError")
