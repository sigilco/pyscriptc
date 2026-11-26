from typing import Generator


def gen() -> Generator[int, None, None]:
    yield 1
    yield 2


g = gen()
print(next(g))
try:
    g.throw(ValueError("x"))
except ValueError as e:
    print("caught", repr(e))
