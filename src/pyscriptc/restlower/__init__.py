"""Call lowering for the modules in `stdmore`.

The lowerer evaluates arguments and reports fences. This module decides which
calls are in the closed surface and which symbol implements them.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._collections import _counter, _defaultdict, _deque, _ordered
from pyscriptc.restlower._copy import _copy_call
from pyscriptc.restlower._csv import _csv_call
from pyscriptc.restlower._datetime import _ints
from pyscriptc.restlower._functools import _reduce
from pyscriptc.restlower._hmac import _hmac, _hmac_compare
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

_OPEN = {"r": 1, "w": 2, "rb": 3, "wb": 4, "a": 5, "ab": 6}
# NamedTemporaryFile adds the read/write `+` modes; `open` keeps _OPEN only.
_TEMP_OPEN = {**_OPEN, "w+": 7, "w+b": 8}


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


_PRINT_KINDS = {"str", "int", "float", "bool", "bytes"}


def _printable(lo: Any, typ: dict[str, Any]) -> bool:
    """printable: a scalar or a tuple class whose fields are all printable."""

    if typ["kind"] in _PRINT_KINDS:
        return True
    if typ["kind"] == "class" and lo.classes.get(typ["name"], {}).get("tuple"):
        return all(_printable(lo, field["type"]) for field in lo.classes[typ["name"]]["fields"])
    return False


def _print(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`print` lowers to the builtin of the same name; the emitter writes stdout.

    Subset: positional values of the compiler's scalar set (str, int, float,
    bool, bytes), `sep=`/`end=` str keywords, and `file=` naming sys.stdout
    or sys.stderr; `flush=` stays fenced. The keyword exprs ride on the call
    dict so the emitter can splice them; `file` rides as a stream tag.
    """
    extras: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(kw, "PS2002", "**kwargs are not lowered")
        if kw.arg == "file":
            # `sys.stdout` (the default) or `sys.stderr` as the stream tag.
            resolved = lo._resolve_module(kw.value)
            if resolved is not None and resolved[1] == "sys" and resolved[2] in {"stdout", "stderr"}:
                extras["file"] = resolved[2]
                continue
            lo.fail(kw, "PS2002", "print file= takes sys.stdout or sys.stderr")
        if kw.arg not in {"sep", "end"}:
            lo.fail(
                kw,
                "PS2003",
                f"print keyword argument {kw.arg} is not lowered; print supports sep= and end= only",
            )
        value = lo._expr(kw.value)
        if value["type"]["kind"] != "str":
            lo.fail(kw, "PS1003", f"print {kw.arg}= takes a str")
        extras[kw.arg] = value
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        lo.fail(node, "PS2002", "starred calls are not lowered")
    args = [lo._expr(arg) for arg in node.args]
    for index, arg in enumerate(args):
        if arg["type"]["kind"] == "class" and lo.classes.get(arg["type"]["name"], {}).get("namedtuple"):
            # A namedtuple prints `P(x=1, y=2)` in CPython; the tuple-class
            # print here would show `(1, 2)`. Fence instead of diverging.
            lo.fail(node, "PS2003", "print of a namedtuple is not lowered; print its fields")
        if not _printable(lo, arg["type"]):
            if arg["type"]["kind"] == "class":
                called = lo._dunder_call(node, arg, "__str__", [], arg["span"]) or lo._dunder_call(
                    node, arg, "__repr__", [], arg["span"]
                )
                if called is not None:
                    args[index] = called
                    continue
            lo.fail(
                node,
                "PS2003",
                f"print is lowered for str, int, float, bool, and bytes values, not {arg['type']['kind']};"
                " wrap it in str(...)",
            )
    expr = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "print"},
        "args": args,
        "type": {"kind": "none"},
        "span": span,
    }
    expr.update(extras)
    return expr


def _input(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`input()` reads one stdin line without the trailing newline.

    A prompt argument stays fenced; CPython writes it to stdout without a
    newline, which the simple stdin read here does not model. EOF raises
    RuntimeError in place of EOFError, which is not in the exception table.
    """
    if node.keywords:
        lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "input expects 0 argument(s)")
    return {
        "kind": "call",
        "callee": {"kind": "module", "module": "builtins", "name": "input"},
        "args": [],
        "symbol": "ps_input_line",
        "type": {"kind": "str"},
        "span": span,
    }


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


def _named_temp(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """tempfile.NamedTemporaryFile(mode, delete) maps onto the open handle
    machinery: it returns a TextIO/BytesIO class whose ps_io_close unlinks
    the file when `delete` is true. `name`, `dir`, and the other CPython
    keywords stay fenced.
    """
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "tempfile.NamedTemporaryFile expects 0 or 1 argument(s)")
    if node.args and any(kw.arg == "mode" for kw in node.keywords):
        lo.fail(node, "PS2003", "NamedTemporaryFile mode is given twice")
    for kw in node.keywords:
        if kw.arg not in {"mode", "delete"}:
            lo.fail(node, "PS2003", "tempfile.NamedTemporaryFile lowers mode and delete keyword arguments")
    keep = 0
    for kw in node.keywords:
        if kw.arg == "delete":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, bool):
                lo.fail(kw.value, "PS2002", "NamedTemporaryFile delete is a bool literal")
            keep = 0 if kw.value.value else 1
    mode = "w+b"
    positional = [*node.args, *[kw.value for kw in node.keywords if kw.arg == "mode"]]
    if positional:
        arg = positional[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) or arg.value not in _TEMP_OPEN:
            lo.fail(arg, "PS2002", "NamedTemporaryFile mode is one of r, w, rb, wb, a, ab, w+, w+b")
        mode = arg.value
    code = _TEMP_OPEN[mode]
    cls = "BytesIO" if "b" in mode else "TextIO"
    args = [
        {"kind": "const", "value": code, "type": {"kind": "int"}, "span": span},
        {"kind": "const", "value": keep, "type": {"kind": "int"}, "span": span},
    ]
    return lo._abi_call("tempfile", "NamedTemporaryFile", args, _class(cls), "ps_temp_named", span)


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


def _open(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    slots = lo._place_nodes(node, ["file", "mode"])
    if slots[0] is None:
        lo.fail(node, "PS2003", "open expects a path and an optional mode literal")
    path = lo._expr(slots[0])
    if path["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "open path is str")
    mode = "r"
    if slots[1] is not None:
        mode_node = slots[1]
        if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
            lo.fail(node, "PS2001", "open mode must be a string literal")
        mode = mode_node.value
    if mode not in _OPEN:
        lo.fail(node, "PS2001", "open mode is not lowered")
    cls = "BytesIO" if "b" in mode else "TextIO"
    code = _const_int(_OPEN[mode], span)
    return lo._abi_call("io", "open", [path, code], _class(cls), "ps_io_open", span)


def _isinstance(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "isinstance expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    want = lo._isinstance_type(node.args[1])
    if obj["type"].get("optional"):
        # `isinstance(x, T)` on `T | None` is the tag: the value is exactly
        # its element type or it is None.
        if lo._isinstance_holds(obj["type"]["elem"], want):
            return {"kind": "attr", "value": obj, "name": "_tag", "type": {"kind": "bool"}, "span": span}
        return {"kind": "const", "value": False, "type": {"kind": "bool"}, "span": span}
    flag = lo._isinstance_holds(obj["type"], want)
    return {"kind": "const", "value": flag, "type": {"kind": "bool"}, "span": span}


def _getattr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "getattr expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    name = _literal_name(lo, node)
    return lo._lookup_attr(obj, name, span, node)


def _hasattr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "hasattr expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    name = _literal_name(lo, node)
    return {"kind": "const", "value": lo._has_attr(obj, name), "type": {"kind": "bool"}, "span": span}


def _literal_name(lo: Any, node: ast.Call) -> str:
    arg = node.args[1]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.isidentifier():
        if any(ord(ch) > 127 for ch in arg.value):
            lo.fail(node, "PS2005", "non-ASCII string literals are not lowered")
        return arg.value
    lo.fail(node, "PS2001", "the attribute name is a string literal")
    raise AssertionError("unreachable")


def _abs(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "abs expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    kind = arg["type"]["kind"]
    if kind == "int":
        return lo._abi_call("builtins", "abs", [arg], {"kind": "int"}, "ps_abs_i64", span)
    if kind == "float":
        return lo._abi_call("builtins", "abs", [arg], {"kind": "float"}, "ps_abs_f64", span)
    lo.fail(node, "PS2003", "abs is lowered for int and float")
    raise AssertionError("unreachable")


def _divmod(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """divmod(a, b) is the `(a // b, a % b)` pair as a synthetic tuple_2.

    Each operand pins once because it appears in both halves; the halves ride
    the existing `//` and `%` lowering, so int, float, and mixed pairs all
    work, and bool or text operands keep their binop fences.
    """

    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "divmod expects 2 argument(s)")
    pinned = [_pin(lo, lo._expr(arg), span, "dv") for arg in node.args]
    left = ast.Name(id=pinned[0]["id"], ctx=ast.Load())
    right = ast.Name(id=pinned[1]["id"], ctx=ast.Load())
    elems = []
    for op in (ast.FloorDiv(), ast.Mod()):
        elems.append(lo._expr(_span_to(node, ast.BinOp(left=left, op=op, right=right))))
    return lo._materialize_tuple(elems, span)


_BASE_SYMS = {"hex": "ps_hex_i64", "oct": "ps_oct_i64", "bin": "ps_bin_i64"}


def _base_repr(lo: Any, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
    """hex/oct/bin of an int: CPython's `0x`/`0o`/`0b` spellings, sign first."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"{name} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "int":
        lo.fail(node, "PS1003", f"{name} argument is int")
    return lo._abi_call("builtins", name, [arg], {"kind": "str"}, _BASE_SYMS[name], span)


def _pow_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """pow(x, y) is `x ** y`; pow(x, y, m) is `x ** y % m` for ints.

    A negative exponent with a modulus keeps the `**` ValueError instead of
    computing CPython's modular inverse.
    """

    lo._reject_keywords(node)
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "pow expects 2 or 3 argument(s)")
    base = lo._pow(node, lo._expr(node.args[0]), lo._expr(node.args[1]), span)
    if len(node.args) == 2:
        return base
    mod = lo._expr(node.args[2])
    if base["type"]["kind"] != "int" or mod["type"]["kind"] != "int":
        lo.fail(node, "PS2003", "pow with a modulus takes int arguments")
    return {"kind": "binary", "op": "%", "left": base, "right": mod, "type": {"kind": "int"}, "span": span}


def _repr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """repr(x) is `'%r' % x` — the format engine already owns the spellings."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "repr expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    kind = arg["type"]["kind"]
    style = "percent"
    if kind == "dict":
        value_kinds = {"int", "float", "str", "bytes"}
        if arg["type"]["key"]["kind"] not in {"int", "str"} or arg["type"]["value"]["kind"] not in value_kinds:
            lo.fail(node, "PS2002", "repr needs scalar dict keys and values")
        style = "map"
    elif kind == "class":
        called = lo._dunder_call(node, arg, "__repr__", [], span)
        if called is not None:
            return called
        lo.fail(node, "PS2004", f"repr of {kind} is not lowered")
    elif kind not in {"int", "float", "str", "bool", "bytes"}:
        lo.fail(node, "PS2004", f"repr of {kind} is not lowered")
    fmt = {"kind": "const", "value": "%r", "type": {"kind": "str"}, "span": span}
    expr = lo._abi_call("builtins", "format", [fmt, arg], {"kind": "str"}, "ps_str_format", span)
    expr["format_style"] = style
    return expr


def _ord(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "ord expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "ord argument is str")
    return lo._abi_call("builtins", "ord", [arg], {"kind": "int"}, "ps_ord", span)


def _chr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "chr expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "int":
        lo.fail(node, "PS1003", "chr argument is int")
    return lo._abi_call("builtins", "chr", [arg], {"kind": "str"}, "ps_chr", span)


def _round(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) not in {1, 2}:
        lo.fail(node, "PS2003", "round expects 1 or 2 argument(s)")
    arg = lo._expr(node.args[0])
    if len(node.args) == 1 and arg["type"]["kind"] == "int":
        return arg
    if arg["type"]["kind"] != "float":
        lo.fail(node, "PS1003", "round argument is float")
    if len(node.args) == 1:
        return lo._abi_call("builtins", "round", [arg], {"kind": "int"}, "ps_round_i64", span)
    digits = lo._expr(node.args[1])
    if digits["type"]["kind"] != "int":
        lo.fail(node, "PS1003", "round ndigits is int")
    return lo._abi_call("builtins", "round", [arg, digits], {"kind": "float"}, "ps_round_nd", span)


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
