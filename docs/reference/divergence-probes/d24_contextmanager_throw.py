from contextlib import contextmanager
from collections.abc import Iterator

@contextmanager
def mgr() -> Iterator[int]:
    print("enter")
    try:
        yield 1
    except ValueError:
        print("caught inside")
    print("exit")

with mgr() as v:
    print(v)
