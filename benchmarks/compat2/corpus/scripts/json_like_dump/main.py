def dump(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value + '"'
    if isinstance(value, list):
        return "[" + ",".join(dump(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(dump(k) + ":" + dump(v) for k, v in value.items()) + "}"
    return "null"


print(dump({"a": [1, 2, {"b": True}], "c": "x", "d": None}))
