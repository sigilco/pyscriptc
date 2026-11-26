from typing import Generator


def gen() -> Generator[int, None, None]:
    yield 1


g: Generator[int, None, None] = gen()
g.throw(ValueError("x"))
