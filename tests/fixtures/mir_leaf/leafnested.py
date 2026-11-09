def matmul(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    n = len(a)
    m = len(b[0])
    out = [[0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            out[i][j] = a[i][0] + b[0][j]
    return out


def first_row(m: list[list[int]]) -> list[int]:
    return m[0]


def swap(m: list[list[int]], i: int, j: int) -> None:
    tmp = m[i]
    m[i] = m[j]
    m[j] = tmp
