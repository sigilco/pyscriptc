def gcd(a: int, b: int) -> int:
    while b != 0:
        a, b = b, a % b
    return a


total = 0
for a in range(1, 200):
    for b in range(1, 200):
        g = gcd(a, b)
        total += a * b // g
print(total)
print(gcd(2**64, 6**20))
