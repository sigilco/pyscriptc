from collections.abc import Iterator

def inner() -> Iterator[int]:
    yield 1
    yield 2

def outer() -> Iterator[int]:
    yield from inner()
    yield 3

for v in outer():
    print(v)
