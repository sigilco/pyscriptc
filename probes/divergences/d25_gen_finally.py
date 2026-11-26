# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043 - the typing.Generator spelling is the construct under test (the frame's throw and close methods exist only on a generator frame)

from typing import Generator


def gen() -> Generator[int, None, None]:
    try:
        yield 1
    finally:
        print("fin")


def main() -> None:
    g: Generator[int, None, None] = gen()
    next(g)
    # The generator is dropped without a close(): CPython's garbage
    # collector finalizes it and the pending finally runs.
    del g
    print("dropped")


main()
