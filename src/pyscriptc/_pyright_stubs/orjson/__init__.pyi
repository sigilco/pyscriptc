# Vendored stub for the lowered orjson surface (the `orjson` package).
# The curated pyscriptc-orjson package lowers dumps to bytes and loads (issue #153, the closed subset in
# restlower/_orjson.py); every other orjson name stays fenced at the call site.
from typing import Any

__all__ = ["dumps", "loads", "OPT_SORT_KEYS", "OPT_INDENT_2"]

OPT_SORT_KEYS: int
OPT_INDENT_2: int

def dumps(obj: Any, option: int = ...) -> bytes: ...
def loads(__obj: str | bytes) -> Any: ...
