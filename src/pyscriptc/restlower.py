"""Call lowering for the modules in `stdmore`.

The lowerer evaluates arguments and reports fences. This module decides which
calls are in the closed surface and which symbol implements them.
"""

from __future__ import annotations

import ast
from typing import Any

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

_CMP = {"eq": ast.Eq, "ne": ast.NotEq, "lt": ast.Lt, "le": ast.LtE, "gt": ast.Gt, "ge": ast.GtE}
_BIN = {"add": ast.Add, "sub": ast.Sub, "mul": ast.Mult, "floordiv": ast.FloorDiv}
_OPEN = {"r": 1, "w": 2, "rb": 3, "wb": 4, "a": 5, "ab": 6}


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
    message = CALL_FENCES.get((module, attr))
    if message:
        lo.fail(node, "PS2001", message)
    if (module, attr) in FOR_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as the iterator of a for")
    if (module, attr) in WITH_ONLY:
        lo.fail(node, "PS2002", f"{module}.{attr} is only lowered as a with context")
    if module == "ctypes" and attr in CTYPES:
        lo.fail(node, "PS2001", f"ctypes.{attr} is not callable in this subset")
    spec = fixed_spec(module, attr)
    if spec is not None:
        return lo._fixed_call(node, span, module, attr, spec)
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
    if node.keywords and name in {
        "isinstance",
        "getattr",
        "hasattr",
        "abs",
        "min",
        "max",
        "sum",
        "sorted",
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
    if name == "ord":
        return _ord(lo, node, span)
    if name == "chr":
        return _chr(lo, node, span)
    if name == "round":
        return _round(lo, node, span)
    return None


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _const_text(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span}


def _class(name: str) -> dict[str, Any]:
    return {"kind": "class", "name": name}


def _ints(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    symbol: str,
    cls: str,
    minimum: int,
    names: list[str],
) -> dict[str, Any]:
    params: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        param: dict[str, Any] = {"name": name, "type": {"kind": "int"}}
        if index >= minimum:
            param["default"] = _const_int(0, span)
        params.append(param)
    args = lo._fill_args(node, params, f"{module}.{attr}")
    return lo._abi_call(module, attr, args, _class(cls), symbol, span)


def _bisect(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", f"bisect.{attr} expects 2 argument(s)")
    seq = lo._expr(node.args[0])
    key = lo._expr(node.args[1])
    if seq["type"]["kind"] != "list" or seq["type"]["elem"]["kind"] != "int":
        lo.fail(node, "PS1003", f"bisect.{attr} takes a list[int]")
    if key["type"]["kind"] != "int":
        lo.fail(node, "PS1003", f"bisect.{attr} key is int")
    symbol = "ps_bisect_left" if attr == "bisect_left" else "ps_bisect_right"
    return lo._abi_call("bisect", attr, [seq, key], {"kind": "int"}, symbol, span)


def _as_float(lo: Any, node: ast.AST, arg: dict[str, Any], span: dict[str, Any], what: str) -> dict[str, Any]:
    if arg["type"]["kind"] == "int":
        return {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
    if arg["type"]["kind"] != "float":
        lo.fail(node, "PS1003", f"{what} argument is {arg['type']['kind']}, expected float")
    return arg


def _exit(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sys.exit expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "int":
        lo.fail(node, "PS1003", "sys.exit argument is an int")
    return lo._abi_call("sys", "exit", [arg], {"kind": "none"}, "exit", span)


def _sleep(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "time.sleep expects 1 argument(s)")
    arg = _as_float(lo, node, lo._expr(node.args[0]), span, "time.sleep")
    return lo._abi_call("time", "sleep", [arg], {"kind": "none"}, "ps_time_sleep", span)


def _clock(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"time.{attr} expects 0 or 1 argument(s)")
    if node.args:
        arg = _as_float(lo, node, lo._expr(node.args[0]), span, f"time.{attr}")
    else:
        arg = lo._abi_call("time", "time", [], {"kind": "float"}, "ps_time_time", span)
    symbol = "ps_time_gm" if attr == "gmtime" else "ps_time_local"
    return lo._abi_call("time", attr, [arg], _class("StructTime"), symbol, span)


def _deque(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "deque expects 0 or 1 argument(s)")
    if not node.args:
        if expect is None or expect.get("name") != "Deque" or "elem" not in expect:
            lo.fail(node, "PS1001", "deque() needs a deque annotation")
        elem = expect["elem"]
        if elem["kind"] == "bool":
            lo.fail(node, "PS2002", "list[bool] is not lowered")
        size = _const_int(slot_size(elem), span)
        return lo._abi_call("collections", "deque", [size], expect, "ps_deque_empty", span)
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list":
        lo.fail(node, "PS1003", "deque() takes a list")
    elem = arg["type"]["elem"]
    if elem["kind"] == "bool":
        lo.fail(node, "PS2002", "list[bool] is not lowered")
    typ = {"kind": "class", "name": "Deque", "elem": elem}
    if expect is not None and expect.get("name") == "Deque":
        lo._expect(node, typ, expect, "deque")
    size = _const_int(slot_size(elem), span)
    return lo._abi_call("collections", "deque", [size, arg], typ, "ps_deque_from", span)


def _ordered(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    """`OrderedDict()` with no arguments is the empty dict the compiler already builds.

    A positional iterable and keyword arguments stay fenced. The `ordered` mark
    is not part of the type key, so an annotated `dict` assignment still matches.
    """
    if node.args:
        lo.fail(node, "PS2001", "an OrderedDict iterable is not lowered")
    if node.keywords:
        lo.fail(node, "PS2001", "OrderedDict keyword arguments are not lowered")
    if expect is not None and expect["kind"] != "dict":
        lo.fail(node, "PS1001", "OrderedDict needs a dict annotation")
    base = expect if expect is not None else {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "int"}}
    return {"kind": "dict", "pairs": [], "type": {**base, "ordered": True}, "span": span}


def _reduce(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`functools.reduce(operator.add, xs)` is a loop of the existing int `+`.

    A direct function name stays the existing call. `operator.add` has no
    function value, so that spelling stores the list once and adds each later
    element. An initializer on `operator.add` stays fenced. An empty list
    raises ValueError; this subset has no TypeError.
    """
    lo._reject_keywords(node)
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "functools.reduce expects 2 or 3 argument(s)")
    fn = node.args[0]
    if _is_operator_add(lo, fn):
        if len(node.args) == 3:
            lo.fail(node, "PS2002", "functools.reduce initializer is not lowered")
        return _reduce_add(lo, node, span)
    if not isinstance(fn, ast.Name) or fn.id not in lo.sigs:
        lo.fail(node, "PS2001", "functools.reduce takes a direct function name")
    params, ret = lo.sigs[fn.id]
    if len(params) != 2 or not _same(params[0]["type"], params[1]["type"]) or not _same(params[0]["type"], ret):
        lo.fail(node, "PS2001", "functools.reduce function is (T, T) -> T")
    seq = lo._expr(node.args[1])
    if seq["type"]["kind"] != "list" or not _same(seq["type"]["elem"], ret):
        lo.fail(node, "PS1003", "functools.reduce takes a list of the function's type")
    args = [seq]
    if len(node.args) == 3:
        init = lo._expr(node.args[2], ret)
        lo._expect(node, init["type"], ret, "functools.reduce initializer")
        args.append(init)
    expr = lo._abi_call("functools", "reduce", args, ret, "", span)
    expr.pop("symbol", None)
    expr["fn"] = fn.id
    return expr


def _reduce_add(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    seq = lo._expr(node.args[1])
    if seq["type"]["kind"] != "list" or seq["type"].get("set") or seq["type"]["elem"]["kind"] != "int":
        lo.fail(node, "PS1003", "functools.reduce takes a list of int")
    int_t = {"kind": "int"}
    list_t = seq["type"]
    src = lo._temp("reds")
    acc = lo._temp("reda")
    idx = lo._temp("redi")
    length = lo._temp("redn")
    lo.locals[src] = list_t
    lo.locals[acc] = int_t
    lo.locals[idx] = int_t
    lo.locals[length] = int_t
    src_n = _nm(src, list_t, span)
    acc_n = _nm(acc, int_t, span)
    idx_n = _nm(idx, int_t, span)
    length_n = _nm(length, int_t, span)
    item = {
        "kind": "subscript",
        "value": src_n,
        "index": idx_n,
        "type": int_t,
        "span": span,
    }
    lo.prelude.extend(
        [
            _asg(src, seq, span),
            _asg(
                length,
                {
                    "kind": "call",
                    "callee": {"kind": "builtin", "name": "len"},
                    "args": [src_n],
                    "type": int_t,
                    "span": span,
                },
                span,
            ),
            {
                "kind": "if",
                "cond": _cmp("==", length_n, _int(0, span), span),
                "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                "else": [],
                "span": span,
            },
            _asg(
                acc,
                {"kind": "subscript", "value": src_n, "index": _int(0, span), "type": int_t, "span": span},
                span,
            ),
            _asg(idx, _int(1, span), span),
            {
                "kind": "while",
                "cond": _cmp("<", idx_n, length_n, span),
                "body": [
                    _asg(
                        acc,
                        {
                            "kind": "binary",
                            "op": "+",
                            "left": acc_n,
                            "right": item,
                            "type": int_t,
                            "span": span,
                        },
                        span,
                    ),
                    _asg(
                        idx,
                        {
                            "kind": "binary",
                            "op": "+",
                            "left": idx_n,
                            "right": _int(1, span),
                            "type": int_t,
                            "span": span,
                        },
                        span,
                    ),
                ],
                "span": span,
            },
        ]
    )
    return _nm(acc, int_t, span)


def _is_operator_add(lo: Any, node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        return resolved is not None and resolved[1] == "operator" and resolved[2] == "add"
    if isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        return binding is not None and binding.module == "operator" and binding.attr == "add"
    return False


def _nm(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _asg(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _cmp(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": {"kind": "bool"}, "span": span}


def _join(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if not 1 <= len(node.args) <= 8:
        lo.fail(node, "PS2003", "posixpath.join expects 1 to 8 argument(s)")
    args = []
    for arg_node in node.args:
        arg = lo._expr(arg_node)
        if arg["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "posixpath.join arguments are str")
        args.append(arg)
    return lo._abi_call("posixpath", "join", args, {"kind": "str"}, "ps_path_join", span)


def _one(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    symbol: str,
    want: dict[str, Any],
    ret: dict[str, Any],
) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"{module}.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    lo._expect(node, arg["type"], want, f"{module}.{attr}")
    return lo._abi_call(module, attr, [arg], ret, symbol, span)


def _buffer(
    lo: Any, node: ast.Call, span: dict[str, Any], module: str, attr: str, symbol: str, kind: str, cls: str
) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"{module}.{attr} expects 0 or 1 argument(s)")
    if node.args:
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != kind:
            lo.fail(node, "PS1003", f"{module}.{attr} argument is {kind}")
    else:
        arg = _const_text("", kind, span)
    return lo._abi_call(module, attr, [arg], _class(cls), symbol, span)


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


def _operator(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if attr == "neg":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "operator.neg expects 1 argument(s)")
        fake = ast.UnaryOp(op=ast.USub(), operand=node.args[0])
        ast.copy_location(fake, node)
        return lo._unary(fake, span)
    if attr in _CMP:
        if len(node.args) != 2:
            lo.fail(node, "PS2003", f"operator.{attr} expects 2 argument(s)")
        fake = ast.Compare(left=node.args[0], ops=[_CMP[attr]()], comparators=[node.args[1]])
        ast.copy_location(fake, node)
        return lo._compare(fake, span)
    if attr in _BIN:
        if len(node.args) != 2:
            lo.fail(node, "PS2003", f"operator.{attr} expects 2 argument(s)")
        fake = ast.BinOp(left=node.args[0], op=_BIN[attr](), right=node.args[1])
        ast.copy_location(fake, node)
        return lo._binary(fake, span)
    if attr == "concat":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "operator.concat expects 2 argument(s)")
        left = lo._expr(node.args[0])
        right = lo._expr(node.args[1])
        if left["type"]["kind"] == right["type"]["kind"] and left["type"]["kind"] in {"str", "bytes"}:
            return {
                "kind": "binary",
                "op": "+",
                "left": left,
                "right": right,
                "type": left["type"],
                "span": span,
            }
        lo.fail(node, "PS2004", "operator.concat takes str or bytes")
    lo.fail(node, "PS2001", f"operator.{attr} is not in the lowered subset of operator")
    raise AssertionError("unreachable")


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


def _minmax(lo: Any, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", f"{name} expects 2 argument(s)")
    left = lo._expr(node.args[0])
    right = lo._expr(node.args[1])
    if left["type"]["kind"] != right["type"]["kind"] or left["type"]["kind"] not in {"int", "float", "str", "bytes"}:
        lo.fail(node, "PS2003", f"{name} takes two values of the same scalar type")
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": name},
        "args": [left, right],
        "type": left["type"],
        "span": span,
    }


def _sum(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sum expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list" or arg["type"]["elem"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS2003", "sum takes a list of int or float")
    kind = arg["type"]["elem"]["kind"]
    symbol = "ps_sum_i64" if kind == "int" else "ps_sum_f64"
    return lo._abi_call("builtins", "sum", [arg], {"kind": kind}, symbol, span)


def _sorted(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sorted expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list" or arg["type"]["elem"]["kind"] not in {"int", "float", "str", "bytes"}:
        lo.fail(node, "PS2003", "sorted takes a list of int, float, str, or bytes")
    folded = _const_sorted(arg, span)
    if folded is not None:
        return folded
    kind = arg["type"]["elem"]["kind"]
    symbol = {"int": "ps_sorted_i64", "float": "ps_sorted_f64", "str": "ps_sorted_buf", "bytes": "ps_sorted_buf"}[kind]
    return lo._abi_call("builtins", "sorted", [arg], arg["type"], symbol, span)


def _const_sorted(arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """sorted() of a constant list is that list. Int constants sort as integers, not their digits."""

    if arg.get("kind") != "list":
        return None
    elems = arg["elems"]
    if any(item.get("kind") != "const" for item in elems):
        return None
    kind = arg["type"]["elem"]["kind"]
    if kind == "int":
        ordered = sorted(elems, key=lambda item: int(item["value"]))
    elif kind in {"str", "bytes"}:
        ordered = sorted(elems, key=lambda item: item["value"])
    else:
        return None
    return {"kind": "list", "elems": ordered, "type": arg["type"], "span": span}


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


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from pyscriptc.ir import types_equal

    return types_equal(a, b)


def method_spec(class_name: str, name: str) -> dict[str, Any] | None:
    return CLASS_METHODS.get(class_name, {}).get(name)


def path_attr(name: str) -> tuple[dict[str, Any], str] | None:
    return PATH_ATTRS.get(name)


_HMAC_ALGS = {"md5": 1, "sha1": 2, "sha256": 3, "sha384": 4, "sha512": 5}


def _hmac_alg(lo: Any, node: ast.expr) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _HMAC_ALGS.get(node.value.lower())
    member = ""
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        if resolved is not None and resolved[1] == "hashlib":
            member = resolved[2]
    elif isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        if binding is not None and binding.kind == "func" and binding.module == "hashlib":
            member = binding.attr
    if not member:
        return None
    return _HMAC_ALGS.get(member)


def _hmac(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if len(node.args) != 3:
        lo.fail(node, "PS2003", f"hmac.{attr} expects 3 argument(s)")
    key = lo._expr(node.args[0])
    msg = lo._expr(node.args[1])
    if key["type"]["kind"] != "bytes" or msg["type"]["kind"] != "bytes":
        lo.fail(node, "PS1003", f"hmac.{attr} key and message are bytes")
    alg = _hmac_alg(lo, node.args[2])
    if alg is None:
        lo.fail(node, "PS2001", "hmac algorithm is a literal md5, sha1, sha256, sha384, or sha512")
    alg_expr = _const_int(alg, span)
    if attr == "digest":
        return lo._abi_call("hmac", "digest", [key, msg, alg_expr], {"kind": "bytes"}, "ps_hmac_digest", span)
    return lo._abi_call("hmac", "new", [key, msg, alg_expr], {"kind": "class", "name": "Hmac"}, "ps_hmac_new", span)


def _hmac_compare(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "hmac.compare_digest expects 2 argument(s)")
    left = lo._expr(node.args[0])
    right = lo._expr(node.args[1])
    kind = left["type"]["kind"]
    if kind not in {"str", "bytes"} or right["type"]["kind"] != kind:
        lo.fail(node, "PS1003", "hmac.compare_digest takes two str or two bytes")
    return lo._abi_call("hmac", "compare_digest", [left, right], {"kind": "bool"}, "ps_hmac_compare", span)


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
