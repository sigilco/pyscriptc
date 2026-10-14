import math


def eval_a(i: int, j: int) -> float:
    return 1.0 / ((i + j) * (i + j + 1) // 2 + i + 1)


def times(u: list[float], transpose: bool) -> list[float]:
    n = len(u)
    out = [0.0] * n
    for i in range(n):
        s = 0.0
        for j in range(n):
            s += (eval_a(j, i) if transpose else eval_a(i, j)) * u[j]
        out[i] = s
    return out


def main() -> None:
    n = 60
    u = [1.0] * n
    v = [0.0] * n
    for _ in range(10):
        v = times(times(u, False), True)
        u = times(times(v, False), True)
    vbv = 0.0
    vv = 0.0
    for i in range(n):
        vbv += u[i] * v[i]
        vv += v[i] * v[i]
    print(f"{math.sqrt(vbv / vv):.9f}")


main()
