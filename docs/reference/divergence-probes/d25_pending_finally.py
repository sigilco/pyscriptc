from collections.abc import Generator

def g() -> Generator[int, None, None]:
    try:
        yield 1
    finally:
        print("finally ran")

gi: Generator[int, None, None] = g()
print(next(gi))
gi.close()
print("after close")
