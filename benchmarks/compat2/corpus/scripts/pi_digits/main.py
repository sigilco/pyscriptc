def pi_digits(count: int) -> str:
    q, r, t, k, m, x = 1, 0, 1, 1, 3, 3
    out = []
    while len(out) < count:
        if 4 * q + r - t < m * t:
            out.append(str(m))
            q, r, m = 10 * q, 10 * (r - m * t), (10 * (3 * q + r)) // t - 10 * m
        else:
            q, r, t, k, m, x = q * k, (2 * q + r) * x, t * x, k + 1, (q * (7 * k + 2) + r * x) // (t * x), x + 2
    return "".join(out)


print(pi_digits(60))
