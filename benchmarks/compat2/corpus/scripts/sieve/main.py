def sieve(n: int) -> list[int]:
    flags = [True] * (n + 1)
    flags[0] = False
    flags[1] = False
    i = 2
    while i * i <= n:
        if flags[i]:
            for j in range(i * i, n + 1, i):
                flags[j] = False
        i += 1
    return [k for k in range(n + 1) if flags[k]]


primes = sieve(20000)
print(len(primes), primes[-1], sum(primes))
