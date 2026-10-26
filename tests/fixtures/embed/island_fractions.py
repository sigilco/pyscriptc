import fractions


def main() -> None:
    f = fractions.Fraction(3, 4)
    s: str = str(f + fractions.Fraction(1, 6))
    print(s)


if __name__ == "__main__":
    main()
