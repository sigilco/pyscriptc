def fact(n: int) -> int:
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


f = fact(30)
print(f)
print(len(str(fact(200))))
print(fact(25) % 1000000007)
