from collections.abc import Iterator


def gen() -> Iterator[int]:
    yield 1


g: Iterator[int] = gen()
g.send(5)
