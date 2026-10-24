"""Call lowering for the modules in `stdmore`.

The lowerer evaluates arguments and reports fences. This module decides which
calls are in the closed surface and which symbol implements them: per-module
handlers live in the sibling `_*.py` modules, and this file keeps only the
`(module, attr)` registry, the `builtin_call` table and the shared table
consults (`CALL_FENCES`, `FOR_ONLY`, `WITH_ONLY`, `fixed_spec`).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from pyscriptc.restlower._argparse import _parser
from pyscriptc.restlower._asyncio import (
    _asyncio_create_task,
    _asyncio_gather,
    _asyncio_run,
    _asyncio_sleep,
    _asyncio_wait_for,
)
from pyscriptc.restlower._asyncio_sync import (
    _prim_call,
    _to_thread,
)
from pyscriptc.restlower._builtins import (
    _abs,
    _base_repr,
    _chr,
    _divmod,
    _getattr,
    _hasattr,
    _isinstance,
    _ord,
    _pow_call,
    _repr,
    _round,
    _type_call,
)
from pyscriptc.restlower._collections import _counter, _defaultdict, _deque, _ordered
from pyscriptc.restlower._copy import _copy_call
from pyscriptc.restlower._csv import _csv_call
from pyscriptc.restlower._ctypes import _sizeof
from pyscriptc.restlower._datetime import _ints
from pyscriptc.restlower._dotenv import _dotenv_call
from pyscriptc.restlower._folds import _eval_fold, _hash_fold, _static_builtin_fold
from pyscriptc.restlower._functools import _reduce
from pyscriptc.restlower._hmac import _hmac, _hmac_compare
from pyscriptc.restlower._io import _input, _named_temp, _open, _print
from pyscriptc.restlower._iterators import (
    _enumerate_value,
    _filter,
    _iter,
    _map,
    _mk_iter,
    _next,
    _range_value,
    _reversed,
    _zip_value,
)
from pyscriptc.restlower._itertools import _itertools
from pyscriptc.restlower._jwt import _jwt_call
from pyscriptc.restlower._minmax import _minmax, _sorted, _sum
from pyscriptc.restlower._operator import _bisect, _join, _operator
from pyscriptc.restlower._orjson import _orjson_call
from pyscriptc.restlower._ospath import _os_open, _os_read, _relpath
from pyscriptc.restlower._pathlib import _buffer, _one
from pyscriptc.restlower._queue import _queue_call, _queue_method
from pyscriptc.restlower._random import _random_call
from pyscriptc.restlower._subprocess import _call as _sub_call
from pyscriptc.restlower._subprocess import _check_call as _sub_check_call
from pyscriptc.restlower._subprocess import _run as _sub_run
from pyscriptc.restlower._system import _as_file_value, _atexit_register, _exit, _getenv, _resource_files, _unicode_name
from pyscriptc.restlower._textwrap import _textwrap_call
from pyscriptc.restlower._threading import _lock_method, _thread_method, _threading_call
from pyscriptc.restlower._time import _clock, _sleep, _strftime
from pyscriptc.restlower._tomllib import _tomllib_call
from pyscriptc.restlower._urllib import _urllib_request_call
from pyscriptc.restlower._yaml import _yaml_call
from pyscriptc.stdmore import (
    CALL_FENCES,
    CLASS_METHODS,
    CTYPES,
    FOR_ONLY,
    OPERATORS,
    PATH_ATTRS,
    WITH_ONLY,
    fixed_spec,
    type_alias,
)

__all__ = [
    "_lock_method",
    "_mk_iter",
    "_queue_method",
    "_thread_method",
    "_static_builtin_fold",
    "alias_name",
    "builtin_call",
    "method_spec",
    "path_attr",
    "rest_call",
]

# A registered handler takes the lowerer, the call, its span, the expected type
# and the attribute name, whatever subset of those it actually needs.
Handler = Callable[[Any, ast.Call, dict[str, Any], "dict[str, Any] | None", str], dict[str, Any]]

_DATETIME_ROWS: dict[str, tuple[str, str, int, list[str]]] = {
    "date": ("ps_date_ymd", "Date", 3, ["year", "month", "day"]),
    "datetime": (
        "ps_dt_new",
        "DateTime",
        3,
        ["year", "month", "day", "hour", "minute", "second", "microsecond"],
    ),
    "time": ("ps_tod_new", "Time", 0, ["hour", "minute", "second", "microsecond"]),
    "timedelta": ("ps_delta_new", "TimeDelta", 0, ["days", "seconds", "microseconds"]),
}


def _datetime_row(attr: str) -> Handler:
    symbol, cls, minimum, names = _DATETIME_ROWS[attr]
    return lambda lo, node, span, expect, _attr: _ints(lo, node, span, "datetime", attr, symbol, cls, minimum, names)


def _namedtuple_only(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any, attr: str) -> dict[str, Any]:
    lo.fail(node, "PS2002", "collections.namedtuple is only lowered in a Name = namedtuple(...) assignment")
    raise AssertionError("unreachable")


# Consulted before the fence tables and before `fixed_spec`.
_EARLY: dict[tuple[str, str], Handler] = {
    ("sys", "exit"): lambda lo, node, span, expect, attr: _exit(lo, node, span),
    ("importlib.resources", "as_file"): lambda lo, node, span, expect, attr: _as_file_value(lo, node, span),
    # The 1-arg form returns the tagged `str | None`; the 2-arg form returns the default instead.
    ("os", "getenv"): lambda lo, node, span, expect, attr: _getenv(lo, node, span),
    ("time", "strftime"): lambda lo, node, span, expect, attr: _strftime(lo, node, span),
    ("tempfile", "NamedTemporaryFile"): lambda lo, node, span, expect, attr: _named_temp(lo, node, span),
    ("functools", "partial"): lambda lo, node, span, expect, attr: lo._partial_callable(node, span),
    ("asyncio", "run"): _asyncio_run,
    ("asyncio", "sleep"): _asyncio_sleep,
    ("asyncio", "gather"): _asyncio_gather,
    ("asyncio", "create_task"): _asyncio_create_task,
    ("asyncio", "wait_for"): _asyncio_wait_for,
    ("asyncio", "to_thread"): _to_thread,
    ("asyncio", "Lock"): _prim_call,
    ("asyncio", "Event"): _prim_call,
    ("asyncio", "Semaphore"): _prim_call,
    ("asyncio", "Queue"): _prim_call,
}

# Whole-module handlers, consulted before `fixed_spec`.
_MODULES_BEFORE_SPEC: dict[str, Handler] = {
    "itertools": lambda lo, node, span, expect, attr: _itertools(lo, node, span, attr),
    "threading": lambda lo, node, span, expect, attr: _threading_call(lo, node, span, attr),
    "queue": lambda lo, node, span, expect, attr: _queue_call(lo, node, span, expect, attr),
}

# Whole-module handlers, consulted after `fixed_spec`.
_MODULES_AFTER_SPEC: dict[str, Handler] = {
    "random": lambda lo, node, span, expect, attr: _random_call(lo, node, span, attr),
    "csv": lambda lo, node, span, expect, attr: _csv_call(lo, node, span, attr),
    "textwrap": lambda lo, node, span, expect, attr: _textwrap_call(lo, node, span, attr),
    "copy": lambda lo, node, span, expect, attr: _copy_call(lo, node, span, attr),
    "tomllib": lambda lo, node, span, expect, attr: _tomllib_call(lo, node, span, expect, attr),
    "dotenv": lambda lo, node, span, expect, attr: _dotenv_call(lo, node, span, expect, attr),
    "yaml": lambda lo, node, span, expect, attr: _yaml_call(lo, node, span, expect, attr),
    "urllib.request": lambda lo, node, span, expect, attr: _urllib_request_call(lo, node, span, expect, attr),
    "jwt": lambda lo, node, span, expect, attr: _jwt_call(lo, node, span, expect, attr),
    "orjson": lambda lo, node, span, expect, attr: _orjson_call(lo, node, span, expect, attr),
}

# Per-attribute handlers that run before keyword arguments are rejected.
_BEFORE_KEYWORDS: dict[tuple[str, str], Handler] = {
    **{("datetime", attr): _datetime_row(attr) for attr in _DATETIME_ROWS},
    ("collections", "OrderedDict"): lambda lo, node, span, expect, attr: _ordered(lo, node, span, expect),
    ("collections", "Counter"): lambda lo, node, span, expect, attr: _counter(lo, node, span, expect),
    ("collections", "defaultdict"): lambda lo, node, span, expect, attr: _defaultdict(lo, node, span, expect),
    ("collections", "namedtuple"): _namedtuple_only,
    ("subprocess", "run"): lambda lo, node, span, expect, attr: _sub_run(lo, node, span),
    ("subprocess", "call"): lambda lo, node, span, expect, attr: _sub_call(lo, node, span),
    ("subprocess", "check_call"): lambda lo, node, span, expect, attr: _sub_check_call(lo, node, span),
}

# Per-attribute handlers that run after keyword arguments are rejected.
_AFTER_KEYWORDS: dict[tuple[str, str], Handler] = {
    **{
        ("operator", attr): (lambda lo, node, span, expect, name: _operator(lo, node, span, name)) for attr in OPERATORS
    },
    ("bisect", "bisect_left"): lambda lo, node, span, expect, attr: _bisect(lo, node, span, attr),
    ("bisect", "bisect_right"): lambda lo, node, span, expect, attr: _bisect(lo, node, span, attr),
    ("time", "sleep"): lambda lo, node, span, expect, attr: _sleep(lo, node, span),
    ("time", "gmtime"): lambda lo, node, span, expect, attr: _clock(lo, node, span, attr),
    ("time", "localtime"): lambda lo, node, span, expect, attr: _clock(lo, node, span, attr),
    ("collections", "deque"): lambda lo, node, span, expect, attr: _deque(lo, node, span, expect),
    ("functools", "reduce"): lambda lo, node, span, expect, attr: _reduce(lo, node, span),
    ("posixpath", "join"): lambda lo, node, span, expect, attr: _join(lo, node, span),
    ("posixpath", "relpath"): lambda lo, node, span, expect, attr: _relpath(lo, node, span),
    ("os", "open"): lambda lo, node, span, expect, attr: _os_open(lo, node, span),
    ("os", "read"): lambda lo, node, span, expect, attr: _os_read(lo, node, span),
    ("argparse", "ArgumentParser"): lambda lo, node, span, expect, attr: _parser(lo, node, span),
    ("pathlib", "Path"): lambda lo, node, span, expect, attr: _one(
        lo, node, span, "pathlib", "Path", "ps_path_new", {"kind": "str"}, {"kind": "class", "name": "Path"}
    ),
    ("io", "StringIO"): lambda lo, node, span, expect, attr: _buffer(
        lo, node, span, "io", "StringIO", "ps_io_string", "str", "TextIO"
    ),
    ("io", "BytesIO"): lambda lo, node, span, expect, attr: _buffer(
        lo, node, span, "io", "BytesIO", "ps_io_bytes", "bytes", "BytesIO"
    ),
    ("ctypes", "sizeof"): lambda lo, node, span, expect, attr: _sizeof(lo, node, span),
    ("hmac", "digest"): lambda lo, node, span, expect, attr: _hmac(lo, node, span, attr),
    ("hmac", "new"): lambda lo, node, span, expect, attr: _hmac(lo, node, span, attr),
    ("hmac", "compare_digest"): lambda lo, node, span, expect, attr: _hmac_compare(lo, node, span),
    ("unicodedata", "name"): lambda lo, node, span, expect, attr: _unicode_name(lo, node, span),
    ("importlib.resources", "files"): lambda lo, node, span, expect, attr: _resource_files(lo, node, span),
    ("atexit", "register"): lambda lo, node, span, expect, attr: _atexit_register(lo, node, span),
}


def rest_call(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    expect: dict[str, Any] | None,
) -> dict[str, Any]:
    key = (module, attr)
    handler = _EARLY.get(key)
    if handler is not None:
        return handler(lo, node, span, expect, attr)
    message = CALL_FENCES.get(key)
    if message:
        lo.fail(node, "PS2001", message)
    if key in FOR_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as the iterator of a for")
    if key in WITH_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as a with context")
    if module == "ctypes" and attr in CTYPES:
        lo.fail(node, "PS2001", f"ctypes.{attr} is not callable in this subset")
    handler = _MODULES_BEFORE_SPEC.get(module)
    if handler is not None:
        return handler(lo, node, span, expect, attr)
    spec = fixed_spec(module, attr)
    if spec is not None:
        return lo._fixed_call(node, span, module, attr, spec)
    handler = _MODULES_AFTER_SPEC.get(module)
    if handler is not None:
        return handler(lo, node, span, expect, attr)
    handler = _BEFORE_KEYWORDS.get(key)
    if handler is not None:
        return handler(lo, node, span, expect, attr)
    lo._reject_keywords(node)
    handler = _AFTER_KEYWORDS.get(key)
    if handler is not None:
        return handler(lo, node, span, expect, attr)
    lo.fail(node, "PS2001", f"{module}.{attr} is not in the lowered subset of {module}")
    raise AssertionError("unreachable")


BuiltinHandler = Callable[[Any, ast.Call, dict[str, Any], str], "dict[str, Any] | None"]

# Builtins whose keyword arguments are rejected before the handler runs.
_NO_KEYWORDS = frozenset({"isinstance", "getattr", "hasattr", "abs", "ord", "chr", "round"})

_BUILTINS: dict[str, BuiltinHandler] = {
    "open": lambda lo, node, span, name: _open(lo, node, span),
    "print": lambda lo, node, span, name: _print(lo, node, span),
    "input": lambda lo, node, span, name: _input(lo, node, span),
    "isinstance": lambda lo, node, span, name: _isinstance(lo, node, span),
    "getattr": lambda lo, node, span, name: _getattr(lo, node, span),
    "hasattr": lambda lo, node, span, name: _hasattr(lo, node, span),
    "abs": lambda lo, node, span, name: _abs(lo, node, span),
    "min": _minmax,
    "max": _minmax,
    "sum": lambda lo, node, span, name: _sum(lo, node, span),
    "sorted": lambda lo, node, span, name: _sorted(lo, node, span),
    "divmod": lambda lo, node, span, name: _divmod(lo, node, span),
    "hex": _base_repr,
    "oct": _base_repr,
    "bin": _base_repr,
    "pow": lambda lo, node, span, name: _pow_call(lo, node, span),
    "repr": lambda lo, node, span, name: _repr(lo, node, span),
    "type": lambda lo, node, span, name: _type_call(lo, node, span),
    "ord": lambda lo, node, span, name: _ord(lo, node, span),
    "chr": lambda lo, node, span, name: _chr(lo, node, span),
    "round": lambda lo, node, span, name: _round(lo, node, span),
    "iter": lambda lo, node, span, name: _iter(lo, node, span),
    "next": lambda lo, node, span, name: _next(lo, node, span),
    "map": lambda lo, node, span, name: _map(lo, node, span),
    "filter": lambda lo, node, span, name: _filter(lo, node, span),
    "reversed": lambda lo, node, span, name: _reversed(lo, node, span),
    "range": lambda lo, node, span, name: _range_value(lo, node, span),
    "enumerate": lambda lo, node, span, name: _enumerate_value(lo, node, span),
    "zip": lambda lo, node, span, name: _zip_value(lo, node, span),
    "eval": lambda lo, node, span, name: _eval_fold(lo, node, span),
    "hash": lambda lo, node, span, name: _hash_fold(lo, node, span),
}


def builtin_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any] | None:
    assert isinstance(node.func, ast.Name)
    name = node.func.id
    handler = _BUILTINS.get(name)
    if handler is None:
        return None
    if node.keywords and name in _NO_KEYWORDS:
        lo._reject_keywords(node)
    return handler(lo, node, span, name)


def method_spec(class_name: str, name: str) -> dict[str, Any] | None:
    return CLASS_METHODS.get(class_name, {}).get(name)


def path_attr(name: str) -> tuple[dict[str, Any], str] | None:
    return PATH_ATTRS.get(name)


def alias_name(module: str, member: str) -> str | None:
    return type_alias(module, member)
