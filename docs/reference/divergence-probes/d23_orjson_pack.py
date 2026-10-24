import orjson

def main() -> None:
    b: bytes = orjson.dumps({"k": "v", "n": 1})
    print(b)
    r: dict[str, object] = orjson.loads(b)
    print(r)
    s: bytes = orjson.dumps({"b": 1, "a": 2}, option=orjson.OPT_SORT_KEYS)
    print(s)

main()
