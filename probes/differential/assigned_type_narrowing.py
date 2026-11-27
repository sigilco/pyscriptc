# SPDX-License-Identifier: Apache-2.0
# Issue #415: `if x is None: x = v` narrows the local to the assigned
# value's type, so a parameter annotated `str | None` reads as `str`
# after the guard (iniconfig's `data` parameter). The narrowing follows
# assignments nested in `with` and `if` blocks too.
def read(data: str | None) -> str:
    if data is None:
        data = "fallback"
    return data


def main() -> None:
    print(read(None))
    print(read("given"))


if __name__ == "__main__":
    main()
