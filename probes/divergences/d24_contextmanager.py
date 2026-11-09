from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def cm() -> Iterator[int]:
    try:
        yield 1
    except ValueError:
        print("caught inside")
    print("after yield")


with cm():
    raise ValueError("boom")
