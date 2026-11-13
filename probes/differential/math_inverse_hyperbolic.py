# Issue #493: the inverse trig, hyperbolic and angle-conversion additions.
# Domain and overflow errors byte-compare against the pinned CPython; the
# nan and inf prints are exact so the float formatting rules are covered too.
import math

print(math.asin(0.5), math.asin(-1.0), math.asin(1.0))
print(math.acos(0.5), math.acos(-0.5), math.acos(1.0))
print(math.atan(1.0), math.atan(-1.0), math.atan(0.0))
print(math.sinh(0.5), math.cosh(0.5), math.tanh(0.5))
print(math.asinh(1.0), math.acosh(1.0), math.acosh(2.0))
print(math.atanh(0.5), math.atanh(-0.5), math.atanh(0.0))
print(math.degrees(math.pi), math.radians(180.0))
for call in ("asin(1.5)", "acos(2.0)", "acosh(0.5)", "atanh(1.0)", "atanh(2.0)", "sinh(1000.0)", "cosh(1000.0)"):
    try:
        print("math." + call, "=", eval("math." + call))
    except ValueError:
        print("math." + call, "-> ValueError")
    except OverflowError:
        print("math." + call, "-> OverflowError")
print(math.asin(float("nan")), math.acos(float("nan")), math.atan(float("nan")), math.tanh(float("inf")))
