"""RootModel wrapping a list."""

from pydantic import RootModel


class Ints(RootModel[list[int]]):
    pass


r = Ints([1, 2, 3])
print(r.root)
