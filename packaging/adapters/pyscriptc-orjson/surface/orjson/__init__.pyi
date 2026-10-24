# The pyscriptc-orjson surface: the lowered orjson subset.
#
# This is the pack's entire Python-visible contract (pack-format spec
# section 3). It is byte-identical to the stub the compiler vendored at
# src/pyscriptc/_pyright_stubs/orjson/__init__.pyi, so the user-facing
# behaviour of the pack is unchanged by the migration.
#
# Everything orjson offers outside this file stays fenced at compile time
# (PS2001): the `default=` callable, `orjson.Fragment`, `orjson.JSONDecodeError`
# and the OPT_* flags outside the two below.
from typing import Any

__all__ = ["dumps", "loads", "OPT_SORT_KEYS", "OPT_INDENT_2"]

OPT_SORT_KEYS: int
OPT_INDENT_2: int

def dumps(obj: Any, option: int = ...) -> bytes: ...
def loads(__obj: str | bytes) -> Any: ...
