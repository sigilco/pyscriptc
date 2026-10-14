def popcount(n: int) -> int:
    c = 0
    while n:
        n &= n - 1
        c += 1
    return c


print(sum(popcount(i) for i in range(1, 4096)))
print(0xFF ^ 0x0F, 1 << 40, -17 >> 2, ~5, 6 | 9, 12 & 10)
print(bin(1234), hex(65535), oct(64), int("ff", 16))
