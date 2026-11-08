from collections.abc import Callable


def tag(n: int) -> Callable[[Callable[[int], int]], Callable[[int], int]]:
    def wrap(fn: Callable[[int], int]) -> Callable[[int], int]:
        def inner(x: int) -> int:
            return fn(x) + n

        return inner

    return wrap


def double(fn: Callable[[int], int]) -> Callable[[int], int]:
    def inner(x: int) -> int:
        return fn(x) * 2

    return inner


@tag(10)
@double
def module_fn(x: int) -> int:
    return x + 1


def outer() -> int:
    @tag(100)
    @double
    def nested(x: int) -> int:
        return x - 1

    return nested(5)


print(module_fn(3))
print(outer())
