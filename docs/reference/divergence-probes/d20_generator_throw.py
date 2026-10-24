from collections.abc import Generator

def g() -> Generator[int, None, None]:
    try:
        yield 1
    except ValueError:
        print("gen caught")
    yield 2

gi: Generator[int, None, None] = g()
print(next(gi))
try:
    gi.throw(ValueError)
except ValueError:
    print("ValueError out")
