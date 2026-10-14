import math

values = [0.1 + 0.2, 1 / 3, math.pi, 1e21, 1e-7, 123456789.123456789, -0.0, 2.5, 3.5]
for v in values:
    print(repr(v), f"{v:.3f}", round(v, 2))
print(int(7.9), int(-7.9), 7 // 2, -7 // 2, 7 % -3, -7 % 3, 2 ** -2)
