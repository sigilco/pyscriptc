def fannkuch(n: int) -> int:
    perm1 = list(range(n))
    count = [0] * n
    max_flips = 0
    checksum = 0
    r = n
    sign = 1
    while True:
        while r != 1:
            count[r - 1] = r
            r -= 1
        perm = perm1[:]
        flips = 0
        k = perm[0]
        while k != 0:
            perm[: k + 1] = perm[k::-1]
            flips += 1
            k = perm[0]
        if flips > max_flips:
            max_flips = flips
        checksum += sign * flips
        sign = -sign
        while True:
            if r == n:
                print(checksum)
                return max_flips
            first = perm1[0]
            for i in range(r):
                perm1[i] = perm1[i + 1]
            perm1[r] = first
            count[r] -= 1
            if count[r] > 0:
                break
            r += 1


print(f"Pfannkuchen(7) = {fannkuch(7)}")
