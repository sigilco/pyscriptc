"""Call lowering for the modules in `stdmore`.

The lowerer evaluates arguments and reports fences. This module decides which
calls are in the closed surface and which symbol implements them.
"""

from __future__ import annotations

import ast
from typing import Any

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
)
from pyscriptc.restlower._collections import _counter, _defaultdict, _deque, _ordered
from pyscriptc.restlower._copy import _copy_call
from pyscriptc.restlower._csv import _csv_call
from pyscriptc.restlower._datetime import _ints
from pyscriptc.restlower._functools import _reduce
from pyscriptc.restlower._hmac import _hmac, _hmac_compare
from pyscriptc.restlower._io import _input, _named_temp, _open, _print
from pyscriptc.restlower._iterators import (
    _elem_list,
    _enumerate_value,
    _filter,
    _fn_ref,
    _iter,
    _lambda_params,
    _map,
    _mk_iter,
    _next,
    _range_value,
    _reversed,
    _zip_value,
)
from pyscriptc.restlower._itertools import _itertools
from pyscriptc.restlower._minmax import _minmax, _sorted, _sum
from pyscriptc.restlower._operator import _bisect, _join, _operator
from pyscriptc.restlower._pathlib import _buffer, _one
from pyscriptc.restlower._random import _random_call
from pyscriptc.restlower._shared import (
    _BIN,
    _CMP,
    _as_float,
    _asg,
    _class,
    _cmp,
    _const_int,
    _const_text,
    _emit,
    _field,
    _incr,
    _int,
    _len_of,
    _nm,
    _pin,
    _same,
    _set_field,
    _span_to,
    _sub,
    _tname,
)
from pyscriptc.restlower._textwrap import _textwrap_call
from pyscriptc.restlower._time import _clock, _sleep
from pyscriptc.stdmore import (
    CALL_FENCES,
    CLASS_METHODS,
    CTYPES,
    FOR_ONLY,
    OPERATORS,
    PATH_ATTRS,
    SIZEOF,
    WITH_ONLY,
    fixed_spec,
    slot_size,
    type_alias,
)


def rest_call(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    expect: dict[str, Any] | None,
) -> dict[str, Any]:
    if module == "sys" and attr == "exit":
        return _exit(lo, node, span)
    if module == "importlib.resources" and attr == "as_file":
        return _as_file_value(lo, node, span)
    if module == "os" and attr == "getenv":
        # The 1-arg form returns the tagged `str | None`; the 2-arg form
        # returns the default instead.
        return _getenv(lo, node, span)
    if module == "tempfile" and attr == "NamedTemporaryFile":
        return _named_temp(lo, node, span)
    if module == "functools" and attr == "partial":
        return lo._partial_callable(node, span)
    message = CALL_FENCES.get((module, attr))
    if message:
        lo.fail(node, "PS2001", message)
    if (module, attr) in FOR_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as the iterator of a for")
    if (module, attr) in WITH_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as a with context")
    if module == "ctypes" and attr in CTYPES:
        lo.fail(node, "PS2001", f"ctypes.{attr} is not callable in this subset")
    if module == "itertools":
        return _itertools(lo, node, span, attr)
    spec = fixed_spec(module, attr)
    if spec is not None:
        return lo._fixed_call(node, span, module, attr, spec)
    if module == "random":
        return _random_call(lo, node, span, attr)
    if module == "csv":
        return _csv_call(lo, node, span, attr)
    if module == "textwrap":
        return _textwrap_call(lo, node, span, attr)
    if module == "copy":
        return _copy_call(lo, node, span, attr)
    if module == "datetime" and attr == "date":
        return _ints(lo, node, span, "datetime", "date", "ps_date_ymd", "Date", 3, ["year", "month", "day"])
    if module == "datetime" and attr == "datetime":
        return _ints(
            lo,
            node,
            span,
            "datetime",
            "datetime",
            "ps_dt_new",
            "DateTime",
            3,
            ["year", "month", "day", "hour", "minute", "second", "microsecond"],
        )
    if module == "datetime" and attr == "time":
        return _ints(
            lo, node, span, "datetime", "time", "ps_tod_new", "Time", 0, ["hour", "minute", "second", "microsecond"]
        )
    if module == "datetime" and attr == "timedelta":
        return _ints(
            lo,
            node,
            span,
            "datetime",
            "timedelta",
            "ps_delta_new",
            "TimeDelta",
            0,
            ["days", "seconds", "microseconds"],
        )
    if module == "collections" and attr == "OrderedDict":
        return _ordered(lo, node, span, expect)
    if module == "collections" and attr == "Counter":
        return _counter(lo, node, span, expect)
    if module == "collections" and attr == "defaultdict":
        return _defaultdict(lo, node, span, expect)
    if module == "collections" and attr == "namedtuple":
        lo.fail(node, "PS2002", "collections.namedtuple is only lowered in a Name = namedtuple(...) assignment")
    lo._reject_keywords(node)
    if module == "operator" and attr in OPERATORS:
        return _operator(lo, node, span, attr)
    if module == "bisect" and attr in {"bisect_left", "bisect_right"}:
        return _bisect(lo, node, span, attr)
    if module == "time" and attr == "sleep":
        return _sleep(lo, node, span)
    if module == "time" and attr in {"gmtime", "localtime"}:
        return _clock(lo, node, span, attr)
    if module == "collections" and attr == "deque":
        return _deque(lo, node, span, expect)
    if module == "functools" and attr == "reduce":
        return _reduce(lo, node, span)
    if module == "posixpath" and attr == "join":
        return _join(lo, node, span)
    if module == "pathlib" and attr == "Path":
        return _one(
            lo, node, span, "pathlib", "Path", "ps_path_new", {"kind": "str"}, {"kind": "class", "name": "Path"}
        )
    if module == "io" and attr == "StringIO":
        return _buffer(lo, node, span, "io", "StringIO", "ps_io_string", "str", "TextIO")
    if module == "io" and attr == "BytesIO":
        return _buffer(lo, node, span, "io", "BytesIO", "ps_io_bytes", "bytes", "BytesIO")
    if module == "ctypes" and attr == "sizeof":
        return _sizeof(lo, node, span)
    if module == "hmac" and attr in {"digest", "new"}:
        return _hmac(lo, node, span, attr)
    if module == "hmac" and attr == "compare_digest":
        return _hmac_compare(lo, node, span)
    if module == "unicodedata" and attr == "name":
        return _unicode_name(lo, node, span)
    if module == "importlib.resources" and attr == "files":
        return _resource_files(lo, node, span)
    if module == "atexit" and attr == "register":
        return _atexit_register(lo, node, span)
    lo.fail(node, "PS2001", f"{module}.{attr} is not in the lowered subset of {module}")
    raise AssertionError("unreachable")


def builtin_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any] | None:
    assert isinstance(node.func, ast.Name)
    name = node.func.id
    if name == "open":
        return _open(lo, node, span)
    if name == "print":
        return _print(lo, node, span)
    if name == "input":
        return _input(lo, node, span)
    if node.keywords and name in {
        "isinstance",
        "getattr",
        "hasattr",
        "abs",
        "ord",
        "chr",
        "round",
    }:
        lo._reject_keywords(node)
    if name == "isinstance":
        return _isinstance(lo, node, span)
    if name == "getattr":
        return _getattr(lo, node, span)
    if name == "hasattr":
        return _hasattr(lo, node, span)
    if name == "abs":
        return _abs(lo, node, span)
    if name in {"min", "max"}:
        return _minmax(lo, node, span, name)
    if name == "sum":
        return _sum(lo, node, span)
    if name == "sorted":
        return _sorted(lo, node, span)
    if name == "divmod":
        return _divmod(lo, node, span)
    if name in {"hex", "oct", "bin"}:
        return _base_repr(lo, node, span, name)
    if name == "pow":
        return _pow_call(lo, node, span)
    if name == "repr":
        return _repr(lo, node, span)
    if name == "ord":
        return _ord(lo, node, span)
    if name == "chr":
        return _chr(lo, node, span)
    if name == "round":
        return _round(lo, node, span)
    if name == "iter":
        return _iter(lo, node, span)
    if name == "next":
        return _next(lo, node, span)
    if name == "map":
        return _map(lo, node, span)
    if name == "filter":
        return _filter(lo, node, span)
    if name == "reversed":
        return _reversed(lo, node, span)
    if name == "range":
        return _range_value(lo, node, span)
    if name == "enumerate":
        return _enumerate_value(lo, node, span)
    if name == "zip":
        return _zip_value(lo, node, span)
    return None


def _getenv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo._reject_keywords(node)
    args = [lo._expr(arg) for arg in node.args]
    for arg in args:
        if arg["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "os.getenv takes str arguments")
    if len(args) == 1:
        return lo._opt_env_get(node, args[0], span)
    if len(args) == 2:
        return lo._abi_call("os", "getenv", args, {"kind": "str"}, "ps_os_getenv_or", span)
    lo.fail(node, "PS2003", "os.getenv expects 1 or 2 argument(s)")
    raise AssertionError("unreachable")


def _exit(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sys.exit expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "int":
        lo.fail(node, "PS1003", "sys.exit argument is an int")
    return lo._abi_call("sys", "exit", [arg], {"kind": "none"}, "exit", span)


def _sizeof(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "ctypes.sizeof expects 1 argument(s)")
    name = _ctype(lo, node.args[0])
    if name is None:
        lo.fail(node, "PS2001", "ctypes.sizeof argument is not a lowered ctype")
    return {"kind": "const", "value": str(SIZEOF[name]), "type": {"kind": "int"}, "span": span}


def _ctype(lo: Any, node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        if resolved is not None and resolved[1] == "ctypes" and resolved[2] in SIZEOF:
            return str(resolved[2])
    if isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        if binding is not None and binding.module == "ctypes" and binding.attr in SIZEOF:
            return str(binding.attr)
    return None


def method_spec(class_name: str, name: str) -> dict[str, Any] | None:
    return CLASS_METHODS.get(class_name, {}).get(name)


def path_attr(name: str) -> tuple[dict[str, Any], str] | None:
    return PATH_ATTRS.get(name)


def _unicode_name(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if not 1 <= len(node.args) <= 2:
        lo.fail(node, "PS2003", "unicodedata.name expects 1 or 2 argument(s)")
    char = lo._expr(node.args[0])
    if char["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "unicodedata.name argument is str")
    if len(node.args) == 2:
        default = lo._expr(node.args[1])
        if default["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "unicodedata.name default is str")
        has = _const_int(1, span)
    else:
        default = _const_text("", "str", span)
        has = _const_int(0, span)
    return lo._abi_call("unicodedata", "name", [char, has, default], {"kind": "str"}, "ps_unicode_name", span)


def _resource_search() -> str:
    import os
    import sys

    found: list[str] = []
    seen: set[str] = set()
    for entry in sys.path:
        if not entry:
            entry = os.getcwd()
        if not os.path.isdir(entry):
            continue
        path = os.path.abspath(entry)
        if path in seen:
            continue
        seen.add(path)
        found.append(path)
    return "\n".join(found)


def _as_file_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """as_file of a directory resource is that path. __enter__ returns it; __exit__ is a no-op."""

    if len(node.args) != 1 or node.keywords:
        lo.fail(node, "PS2003", "as_file expects 1 argument(s)")
    opened = lo._expr(node.args[0])
    if opened["type"]["kind"] != "class" or opened["type"].get("name") != "Path":
        lo.fail(node, "PS1003", "as_file takes a resource path")
    return opened


def _resource_files(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "importlib.resources.files expects 1 argument(s)")
    package = lo._expr(node.args[0])
    if package["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "importlib.resources.files package is str")
    search = _const_text(_resource_search(), "str", span)
    return lo._abi_call(
        "importlib.resources",
        "files",
        [package, search],
        {"kind": "class", "name": "Path"},
        "ps_res_files",
        span,
    )


def _atexit_register(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    name = node.args[0].id
    sig = lo.sigs.get(name)
    if sig is None:
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    params, _ret = sig
    if params:
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    target = _const_text(lo._ir_name(name), "str", span)
    return lo._abi_call("atexit", "register", [target], {"kind": "none"}, "", span)


def alias_name(module: str, member: str) -> str | None:
    return type_alias(module, member)
