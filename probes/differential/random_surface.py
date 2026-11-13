import random

random.seed(12345)
xs: list[int] = [10, 20, 30, 40, 50, 60, 70, 80]
ss: list[str] = ["a", "b", "c", "d"]
ws: list[float] = [0.5, 1.5, 3.0, 0.25]
wi: list[int] = [1, 2, 3, 4]

print(random.choices(xs, k=5))
print(random.choices(ss, weights=ws, k=6))
print(random.choices(ss, weights=wi, k=6))
print(random.choices(ss))
print(random.sample(xs, 3))
print(random.sample(ss, 4))
big: list[int] = list(range(100))
print(random.sample(big, 7))
print(random.sample(big, 40))
print(random.randbytes(11))
print(random.randbytes(0))
print(random.gauss(0.0, 1.0))
print(random.gauss(5, 2))
print(random.gauss())
print(random.normalvariate(1.0, 2.0))
print(random.normalvariate())
print(random.lognormvariate(0.0, 0.5))
print(random.expovariate(2.0))
print(random.expovariate())
print(random.triangular(0.0, 10.0, 3.0))
print(random.triangular(2.0, 4.0))
print(random.triangular())
print(random.triangular(1.0, 1.0, 1.0))
print(random.gammavariate(2.5, 1.5))
print(random.gammavariate(1.0, 2.0))
print(random.gammavariate(0.5, 2.0))
print(random.betavariate(2.0, 5.0))
random.seed(7)
print(random.gauss(0.0, 1.0))
random.seed(7)
print(random.gauss(0.0, 1.0))
print(random.gauss(0.0, 1.0))
try:
    random.sample(xs, 20)
except ValueError as e:
    print("ValueError", e)
try:
    random.choices(ss, weights=[1.0, 2.0], k=2)
except ValueError as e:
    print("ValueError", e)
try:
    random.gammavariate(-1.0, 1.0)
except ValueError as e:
    print("ValueError", e)
try:
    random.expovariate(0.0)
except ZeroDivisionError as e:
    print("ZeroDivisionError", e)
