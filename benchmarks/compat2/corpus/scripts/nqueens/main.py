def solve(n: int, row: int, cols: list[bool], d1: list[bool], d2: list[bool]) -> int:
    if row == n:
        return 1
    found = 0
    for c in range(n):
        if not cols[c] and not d1[row + c] and not d2[row - c + n - 1]:
            cols[c] = True
            d1[row + c] = True
            d2[row - c + n - 1] = True
            found += solve(n, row + 1, cols, d1, d2)
            cols[c] = False
            d1[row + c] = False
            d2[row - c + n - 1] = False
    return found


for n in range(4, 9):
    print(n, solve(n, 0, [False] * n, [False] * (2 * n), [False] * (2 * n)))
