"""Differential probe: an erased user class decorator (divergence 52, issue #732).

The class lowers undecorated, so the decorator call never runs and the
decorator's side effect is missing where CPython applies the wrap to the
class object. Every value the class itself produces (fields, methods,
construction, inheritance) matches CPython; the printed registry write is
the divergence's observable half (CPython prints "registered", the binary
does not) and the recorded row in `docs/reference/divergence-ledger.json`
captures both sides.
"""


def register(c: int) -> int:
    print("registered")
    return c


print(register(1))


KIND: int = 7


class Base:
    def describe(self) -> str:
        return "base"


@register
class Thing(Base):
    n: int

    def __init__(self) -> None:
        self.n = 3

    def total(self) -> int:
        return self.n + KIND


t = Thing()
print(t.describe())
print(t.total())
print(isinstance(t, Thing))
