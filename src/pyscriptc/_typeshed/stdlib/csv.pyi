import sys
from _csv import (
    QUOTE_ALL as QUOTE_ALL,
    QUOTE_MINIMAL as QUOTE_MINIMAL,
    QUOTE_NONE as QUOTE_NONE,
    QUOTE_NONNUMERIC as QUOTE_NONNUMERIC,
    Error as Error,
    __version__ as __version__,
    _DialectLike,
    _QuotingType,
    field_size_limit as field_size_limit,
    get_dialect as get_dialect,
    list_dialects as list_dialects,
    reader as reader,
    register_dialect as register_dialect,
    unregister_dialect as unregister_dialect,
    writer as writer,
)

if sys.version_info >= (3, 12):
    from _csv import QUOTE_NOTNULL as QUOTE_NOTNULL, QUOTE_STRINGS as QUOTE_STRINGS
from _csv import Reader, Writer
from _typeshed import SupportsWrite
from collections.abc import Iterable
from types import GenericAlias
from typing import Any

__all__ = [
    "QUOTE_MINIMAL",
    "QUOTE_ALL",
    "QUOTE_NONNUMERIC",
    "QUOTE_NONE",
    "Error",
    "Dialect",
    "excel",
    "excel_tab",
    "field_size_limit",
    "reader",
    "writer",
    "register_dialect",
    "get_dialect",
    "list_dialects",
    "Sniffer",
    "unregister_dialect",
    "DictReader",
    "DictWriter",
    "unix_dialect",
]
if sys.version_info >= (3, 12):
    __all__ += ["QUOTE_STRINGS", "QUOTE_NOTNULL"]
if sys.version_info < (3, 13):
    __all__ += ["__doc__", "__version__"]

class Dialect:
    delimiter: str
    quotechar: str | None
    escapechar: str | None
    doublequote: bool
    skipinitialspace: bool
    lineterminator: str
    quoting: _QuotingType
    strict: bool
    def __init__(self) -> None: ...

class excel(Dialect): ...
class excel_tab(excel): ...
class unix_dialect(Dialect): ...

def DictReader(f: Iterable[str], *, delimiter: str = ",") -> list[dict[str, str]]: ...

class DictWriter:
    fieldnames: list[str]
    def __init__(
        self,
        f: SupportsWrite[str],
        fieldnames: list[str],
        *,
        delimiter: str = ",",
    ) -> None: ...
    def writeheader(self) -> None: ...
    def writerow(self, rowdict: dict[str, str]) -> None: ...
    if sys.version_info >= (3, 12):
        def __class_getitem__(cls, item: Any, /) -> GenericAlias: ...

class Sniffer:
    preferred: list[str]
    def sniff(self, sample: str, delimiters: str | None = None) -> type[Dialect]: ...
    def has_header(self, sample: str) -> bool: ...
