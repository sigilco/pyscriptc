# SPDX-License-Identifier: Apache-2.0
# Issue #415: print of a tagged optional prints the payload or `None`,
# the way CPython prints `lineof`'s `int | None` answer. The scalar
# payload prints its str; a missing value prints `None`.
def lineof(name: str) -> int | None:
    if name == "known":
        return 7
    return None


def main() -> None:
    print(lineof("known"), lineof("missing"))
    print(lineof("missing"))


if __name__ == "__main__":
    main()
