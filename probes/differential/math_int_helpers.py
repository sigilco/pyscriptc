# Issue #493: fmod and the integer helpers (factorial, lcm; gcd pre-existing).
# math.factorial(21) is excluded: on a 64-bit i64 target it raises
# OverflowError where the host oracle grows a big int (divergence D60).
import math

print(math.fmod(7.5, 2.0), math.fmod(-7.5, 2.0), math.fmod(7.5, -2.0))
print(math.gcd(12, 18), math.gcd(-12, 18), math.gcd(0, 0))
print(math.lcm(4, 6), math.lcm(-4, 6), math.lcm(0, 5), math.lcm(7, 13))
print(math.factorial(0), math.factorial(1), math.factorial(5), math.factorial(20))
for call in ("fmod(1.0, 0.0)", "factorial(-1)", "lcm(3037000500, 3037000499)"):
    try:
        print("math." + call, "=", eval("math." + call))
    except ValueError:
        print("math." + call, "-> ValueError")
    except OverflowError:
        print("math." + call, "-> OverflowError")
