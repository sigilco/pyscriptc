# SPDX-License-Identifier: Apache-2.0
# Divergence D105: `object` and `Any` are not interchangeable (issue #1226).
# This probe shows the compiling surface: `object` is the tagged dynamic
# value (the closed sum None | bool | int | float | str | list | dict),
# every arm boxes, the isinstance tag test narrows, and a bool answers
# isinstance(v, int) with True the way CPython's subclass rule does. The
# closed-sum fence (bytes, a tuple, a user class refuse the box) is the
# compile-time seam the entry names; the byte comparison here is the
# running surface, which is CPython's on every line.
import json


def total(msg: dict[str, object]) -> int:
    n = 0
    for k in msg:
        v = msg.get(k)
        if isinstance(v, int):
            n = n + 1
        if isinstance(v, str):
            n = n + 10
        if isinstance(v, float):
            n = n + 100
        if isinstance(v, bool):
            n = n + 1000
    return n


msg: dict[str, object] = {"a": 1, "b": "two", "c": 2.5, "d": True, "e": None, "f": [1]}
print(total(msg))

xs: list[object] = [1, "x", None, 2.5, True, [1], {"k": 1}]
print(xs)
print(str(xs[0]), str(xs[1]), str(xs[2]), str(xs[3]), str(xs[4]))


class Holder:
    v: object = 1
    w: object = "seed"


print(Holder.v, Holder.w, Holder().v)


def narrow(v: object) -> str:
    if isinstance(v, int):
        return str(v + 1)
    if isinstance(v, str):
        return v.upper()
    return "other"


print(narrow(1), narrow("ab"), narrow(None))

d: dict[str, object] = json.loads('{"a": [1, {"b": 2.5}], "c": null}')
print(d)
print(json.dumps(d))
