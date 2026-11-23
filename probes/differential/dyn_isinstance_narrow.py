# Issue #1280: island-side type promotion. A value typed `object` narrows
# through `isinstance(x, float)` (and int, str, bool, list, dict) into the
# native type, so the narrowed value is usable as that type inside the
# branch. The tag test is the runtime's, the projection off the cell is the
# runtime's, and the answer matches CPython's byte for byte.
def describe(x: object) -> str:
    if isinstance(x, bool):
        if x:
            return "bool-true"
        return "bool-false"
    if isinstance(x, int):
        return "int:" + str(x + 1)
    if isinstance(x, float):
        return "float:" + str(x * 2.0)
    if isinstance(x, str):
        return "str:" + x.upper()
    if isinstance(x, list):
        return "list:" + str(len(x))
    if isinstance(x, dict):
        return "dict:" + str(len(x))
    return "other"


print(describe(True))
print(describe(False))
print(describe(41))
print(describe(2.5))
print(describe("hi"))
print(describe([1, 2, 3]))
print(describe({"a": 1}))
print(describe(None))


# The early-return shape: the branch that exits proves the arm for the
# rest of the body.
def early(x: object) -> int:
    if not isinstance(x, int):
        return -1
    return x + 1


print(early(41))
print(early("s"))


# The narrowing survives the branch and the value reads as its arm type
# in the arithmetic that follows.
def doubled(x: object) -> float:
    if isinstance(x, float):
        return x * 2.0
    return 0.0


print(doubled(1.25))
print(doubled(3))


# A `dict[str, object]` payload narrowed per value: the JSON-RPC shape.
def field(msg: dict[str, object], key: str) -> int:
    v = msg.get(key)
    if isinstance(v, int):
        return v * 10
    if isinstance(v, str):
        return len(v)
    return -1


print(field({"a": 4}, "a"))
print(field({"a": "four"}, "a"))
print(field({}, "a"))


# The bool-is-an-int rule of CPython: a bool cell answers the int arm too.
def as_int(x: object) -> int:
    if isinstance(x, int):
        return x + 1
    return 0


print(as_int(True))
print(as_int(False))
print(as_int(7))


# The narrowing is a projection, not a coercion: the value outside the
# branch is still the sum.
def outside(x: object) -> int:
    n = 0
    if isinstance(x, int):
        n = x
    if isinstance(x, int):
        n = n + 1
    return n


print(outside(5))
print(outside("s"))
