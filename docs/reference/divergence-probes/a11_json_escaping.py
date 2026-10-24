import json

d: dict[str, str] = {"k": "café"}
b: bytes = json.dumps(d).encode("utf-8")
print(b)
