def main() -> None:
    total: int = 0
    i: int = 0
    while i < 100_000_000:
        total += i
        i += 1
    print(total)


main()
