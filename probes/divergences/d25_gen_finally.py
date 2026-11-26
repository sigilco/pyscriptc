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
