"""The typed entry over the fractions island module."""

import fractions_island


def main() -> None:
    a: str = fractions_island.frac_sum(3, 4, 1, 6)
    b: str = fractions_island.frac_scale(3, 4, 3)
    print(a)
    print(b)


if __name__ == "__main__":
    main()
