# SPDX-License-Identifier: Apache-2.0
"""`json.loads` into the tagged dynamic value and its print (issue #1267).

`json.loads` under a `dict[str, object]` (or a bare `object`,
`list[object]`) annotation builds the tagged cells instead of checking a
shape, `json.dumps` walks them back, and `print` reads the arms the way
CPython's `str()` does: the str arm's text, every container's repr. The
scalars keep their CPython spellings (`True`, `None`, the shortest
round-trip float). Error cases ride the same `ValueError` the typed
loads path raises, so `except ValueError` catches a malformed document.
Both sides run this same program and the lane byte-compares stdout,
stderr and the exit code.
"""

import json


def describe(msg: dict[str, object]) -> int:
    total = 0
    for k in msg:
        v = msg.get(k)
        if isinstance(v, int):
            total = total + 1
        if isinstance(v, str):
            total = total + 10
        if isinstance(v, dict):
            total = total + 100
    return total


def main() -> None:
    msg: dict[str, object] = json.loads('{"id":1,"method":"x","params":{"a":1}}')
    print(msg)

    d: dict[str, object] = json.loads(
        '{"a": 1, "b": "two", "c": 2.5, "d": true, "e": null, "f": [1, "x", null], "g": {"h": 3}}'
    )
    print(d)
    print(json.dumps(d))
    print(json.dumps(d, sort_keys=True))
    print(json.dumps(d, indent=2))
    print(describe(d))

    built: dict[str, object] = {"x": 1, "y": "two", "z": None}
    print(built)
    print(json.dumps(built))

    flat: dict[str, object] = json.loads('{"k": 7}')
    v = flat.get("k")
    if isinstance(v, int):
        print("int", v)

    s: object = json.loads('"strval"')
    print(s)
    if isinstance(s, str):
        print("str arm")

    n: object = json.loads("null")
    print(n)
    print(json.dumps(n))

    f: object = json.loads("2.25")
    print(f)
    print(json.dumps(f))

    lst: object = json.loads('[1, "a", null, true]')
    print(lst)
    print(json.dumps(lst))

    t: object = json.loads("true")
    print(t)

    xs: list[object] = json.loads('[1, "a", null, true, {"k": 2}]')
    print(xs)
    print(json.dumps(xs))

    try:
        bad: dict[str, object] = json.loads('{"a": }')
        print("no error", bad)
    except ValueError:
        print("caught malformed")

    try:
        trail: object = json.loads("{} {}")
        print("no error", trail)
    except ValueError:
        print("caught trailing")

    try:
        deep: object = json.loads('{"a": [1, {"b": ]}]}')
        print("no error", deep)
    except ValueError:
        print("caught nested")


main()
