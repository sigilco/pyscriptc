from collections.abc import Callable


def deco(fn: Callable[[int], int]) -> Callable[[int], int]:
    print("wrapped")
    return fn


class Thing:
    @staticmethod
    def wrap(fn: Callable[[Thing], int]) -> Callable[[Thing], int]:
        print("wrapped method")
        return fn

    @wrap
    def total(self) -> int:
        return 3


@deco
def helper(n: int) -> int:
    return n + 4


print(Thing().total())
print(helper(4))
