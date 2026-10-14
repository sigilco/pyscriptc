def matmul(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    n = len(a)
    m = len(b[0])
    k = len(b)
    out = [[0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            s = 0
            for t in range(k):
                s += a[i][t] * b[t][j]
            out[i][j] = s
    return out


size = 24
a = [[(i * 7 + j * 3) % 11 for j in range(size)] for i in range(size)]
b = [[(i * 5 + j * 2) % 13 for j in range(size)] for i in range(size)]
c = matmul(a, b)
print(sum(sum(row) for row in c), c[0][0], c[size - 1][size - 1])
