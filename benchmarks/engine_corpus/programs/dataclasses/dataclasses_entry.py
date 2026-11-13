"""The typed entry over the dataclasses island module."""

import dataclasses_island


def main() -> None:
    a: str = dataclasses_island.point_fields()
    b: str = dataclasses_island.make_named()
    c: str = dataclasses_island.as_dict()
    print(a)
    print(b)
    print(c)


if __name__ == "__main__":
    main()
