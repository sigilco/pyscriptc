def register(c: int) -> int:
    print("registered")
    return c


class Base:
    def describe(self) -> str:
        return "base"


@register
class Thing(Base):
    def __init__(self) -> None:
        self.n = 3

    def total(self) -> int:
        return self.n + 7


t = Thing()
print(t.describe())
print(t.total())
print(isinstance(t, Thing))
