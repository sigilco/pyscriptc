"""The json corpus program: the island module the compiler routes to the engine."""

import json


def dumps_sorted() -> str:
    payload = {"b": [1, 2, 3], "a": "x", "c": True}
    return json.dumps(payload, sort_keys=True)


def loads_int() -> int:
    return json.loads("[1, 2, 3]")[2]


def dumps_unicode() -> str:
    return json.dumps({"k": "café"}, ensure_ascii=False, sort_keys=True)
