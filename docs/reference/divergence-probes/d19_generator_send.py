from collections.abc import Generator

def g() -> Generator[int, int, None]:
    yield 1
    yield 2

gi: Generator[int, int, None] = g()
try:
    got: int = gi.send(9)
    print("sent got", got)
except TypeError:
    print("TypeError out")
