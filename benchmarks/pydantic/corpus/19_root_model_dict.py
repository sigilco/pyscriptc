"""RootModel wrapping a dict."""

from pydantic import RootModel


class Map(RootModel[dict[str, int]]):
    pass


m = Map({"a": 1})
print(m.root)
