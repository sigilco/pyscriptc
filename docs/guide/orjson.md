---
title: Fast JSON with orjson
description: The orjson subset pyscriptc lowers and the option flags it accepts.
---

pyscriptc lowers two names of the `orjson` module (through the `pyscriptc-orjson` pack): `orjson.dumps` and `orjson.loads`.
Both go through the same C JSON writer and reader the `json` module uses.

```python
import orjson

data: bytes = orjson.dumps({"b": 2, "a": 1})
value: dict[str, int] = orjson.loads(data)
```

`dumps` returns `bytes`, as orjson does, with no space after the `,` and `:` separators.
`loads` reads a `str` or `bytes` into the annotated type, and needs the annotation (`PS1001` without one).
The value shapes are the `json` subset's shapes.

Two option flags lower, alone or or-ed together:

| Flag | Effect |
| --- | --- |
| `orjson.OPT_SORT_KEYS` | the dict keys sort |
| `orjson.OPT_INDENT_2` | every item goes on its own line with a two-space pad |

```python
data: bytes = orjson.dumps({"b": 2, "a": 1}, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
```

Everything else in orjson stays fenced: the `default=` callable, every other `OPT_*` flag, `orjson.Fragment` and `orjson.JSONDecodeError` (`PS2001`/`PS2003`).

## Where it differs from orjson

orjson is not vendored in the compat corpus, so the pack follows orjson's documented output rather than a byte-for-byte vendored oracle.
Compact output (`{"a":1}` with no spaces) and the two flags above match orjson; serialization of orjson's extended types (`numpy` arrays, `dataclass`, `UUID`, `datetime`) is not in the subset.
Errors surface as `ValueError` rather than `orjson.JSONDecodeError`.
