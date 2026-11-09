from collections.abc import Iterator


def gen() -> Iterator[int]:
    try:
        yield 1
    finally:
        print("fin")


g: Iterator[int] = gen()
next(g)
g.close()
