# Issue #1226: str() of a tagged dynamic value, and a class attribute
# annotated `object`. `str(v)` on a value typed `object` is CPython's
# `str()` of the arm the cell holds: the str arm's text, every other arm's
# repr. A class attribute annotated `object` is not a compile-time constant
# (the tagged cell has no const spelling), so it lowers in the class's
# attribute initializer and reads through the slot the way CPython does.
from dataclasses import dataclass


def show(v: object) -> str:
    return str(v)


print(show(None))
print(show(True))
print(show(False))
print(show(41))
print(show(-7))
print(show(2.5))
print(show("hi"))
print(show(""))
print(show([1, "x", None]))
print(show({"a": 1, "b": "two"}))
print(show([[1, 2], {"k": "v"}]))

# The class attribute surface: the default is the boxed cell and C.v reads
# it back the way CPython does.


class Holder:
    v: object = 1
    w: object = "seed"
    n: object = None
    f: object = 2.5
    b: object = True


print(Holder.v, Holder.w, Holder.n, Holder.f, Holder.b)
print(Holder().v, Holder().w)

# A dataclass field default is the class attribute CPython leaves behind,
# so the field reads the same value the class attribute reads.


@dataclass
class Rec:
    v: object = 5
    s: object = "text"


print(Rec.v, Rec.s)
print(Rec().v, Rec().s)
print(Rec(9, "other").v, Rec(9, "other").s)


# A parameter default is the box call replayed per call site.
def with_default(v: object = 3) -> str:
    return str(v)


print(with_default(), with_default(4))
