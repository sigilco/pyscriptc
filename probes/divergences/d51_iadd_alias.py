def main() -> int:
    a: list[int] = [1]
    b: list[int] = a
    a += [2]
    print(b)
    return 0


main()
