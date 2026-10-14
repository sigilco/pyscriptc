def sum_squares(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i * i
        i = i + 1
    return total


if __name__ == "__main__":
    print(sum_squares(10))
