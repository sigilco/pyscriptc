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


def _span_to(node: ast.AST, sub: ast.AST) -> ast.AST:
    """Copy a call's source span onto a synthesized expression, like the sorted block does."""

    for item in ast.walk(sub):
        item.lineno, item.col_offset = node.lineno, node.col_offset
        item.end_lineno, item.end_col_offset = node.end_lineno, node.end_col_offset
    return sub


def _pin(lo: Any, value: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Store a call argument in a fresh local so later uses read the name."""

    temp = lo._temp(hint)
    lo.locals[temp] = value["type"]
    lo.prelude.append({"kind": "assign", "target": temp, "value": value, "span": span})
    return {"kind": "name", "id": temp, "type": value["type"], "span": span}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


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
    for arg in args:
        if not _printable(lo, arg["type"]):
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


def _minmax(lo: Any, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
    """min/max: two scalars stay a builtin call; the iterable form folds into sorted().

    A stable sort picks the first of equal keys, which is CPython's min/max
    tie-break; max is `sorted(reverse=True)[0]`. An empty sequence raises
    ValueError before the sort, matching CPython's message.
    """
    key = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", "**kwargs are not lowered")
        if kw.arg == "key":
            key = kw.value
        else:
            lo._reject_keywords(node)
    if key is None and len(node.args) == 2:
        left = lo._expr(node.args[0])
        right = lo._expr(node.args[1])
        if left["type"]["kind"] != right["type"]["kind"] or left["type"]["kind"] not in _SORT_ELEM:
            lo.fail(node, "PS2003", f"{name} takes two values of the same scalar type")
        return {
            "kind": "call",
            "callee": {"kind": "builtin", "name": name},
            "args": [left, right],
            "type": left["type"],
            "span": span,
        }
    if not node.args:
        lo.fail(node, "PS2003", f"{name} expects at least 1 argument(s)")
    if len(node.args) == 1:
        arg = lo._expr(node.args[0])
    else:
        elems = [lo._expr(item) for item in node.args]
        kind = elems[0]["type"]["kind"]
        if kind not in _SORT_ELEM or any(elem["type"]["kind"] != kind for elem in elems):
            lo.fail(node, "PS2003", f"{name} takes scalars of one type")
        arg = {"kind": "list", "elems": elems, "type": {"kind": "list", "elem": elems[0]["type"]}, "span": span}
    if arg["type"]["kind"] == "dict":
        seq = _pin(lo, arg, span, "mmseq")
        keys_call = ast.Call(
            func=ast.Attribute(value=ast.Name(id=seq["id"], ctx=ast.Load()), attr="keys", ctx=ast.Load()),
            args=[],
            keywords=[],
        )
        arg = lo._expr(_span_to(node, keys_call))
    if arg["type"]["kind"] != "list" or arg["type"]["elem"]["kind"] not in _SORT_ELEM:
        lo.fail(node, "PS2003", f"{name} takes a list of int, float, str, or bytes")
    if key is None:
        kind = arg["type"]["elem"]["kind"]
        symbol = "ps_" + name + "_" + {"int": "i64", "float": "f64", "str": "buf", "bytes": "buf"}[kind]
        return lo._abi_call("builtins", name, [arg], {"kind": kind}, symbol, span)
    seq = _pin(lo, arg, span, "mmseq")
    len_call = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }
    empty = {
        "kind": "compare",
        "op": "==",
        "left": len_call,
        "right": {"kind": "const", "value": "0", "type": {"kind": "int"}, "span": span},
        "type": {"kind": "bool"},
        "span": span,
    }
    lo.prelude.append(
        {
            "kind": "if",
            "cond": empty,
            "then": [
                {
                    "kind": "raise",
                    "exc": "ValueError",
                    "message": {
                        "kind": "const",
                        "value": f"{name}() iterable argument is empty",
                        "type": {"kind": "str"},
                        "span": span,
                    },
                    "span": span,
                }
            ],
            "else": [],
            "span": span,
        }
    )
    kws = [ast.keyword(arg="key", value=key)]
    if name == "max":
        kws.append(ast.keyword(arg="reverse", value=ast.Constant(value=True)))
    call = ast.Call(
        func=ast.Name(id="sorted", ctx=ast.Load()), args=[ast.Name(id=seq["id"], ctx=ast.Load())], keywords=kws
    )
    ordered = _sorted(lo, _span_to(node, call), span)
    return {
        "kind": "subscript",
        "value": ordered,
        "index": {"kind": "const", "value": "0", "type": {"kind": "int"}, "span": span},
        "type": arg["type"]["elem"],
        "span": span,
    }


def _sum(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """sum(xs) is a runtime fold; sum(xs, start) pins the fold and adds once.

    The `+` rides the normal binary lowering, so int + float promotes like
    CPython. `start` is keyword-or-positional like CPython.
    """
    start = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", "**kwargs are not lowered")
        if kw.arg == "start":
            start = kw.value
        else:
            lo._reject_keywords(node)
    if len(node.args) == 2:
        if start is not None:
            lo.fail(node, "PS2003", "sum got multiple values for argument 'start'")
        start = node.args[1]
    elif len(node.args) != 1:
        lo.fail(node, "PS2003", "sum expects 1 or 2 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list" or arg["type"]["elem"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS2003", "sum takes a list of int or float")
    kind = arg["type"]["elem"]["kind"]
    symbol = "ps_sum_i64" if kind == "int" else "ps_sum_f64"
    call = lo._abi_call("builtins", "sum", [arg], {"kind": kind}, symbol, span)
    if start is None:
        return call
    total = _pin(lo, call, span, "sum")
    add = ast.BinOp(left=ast.Name(id=total["id"], ctx=ast.Load()), op=ast.Add(), right=start)
    return lo._expr(_span_to(node, add))


_SORT_ELEM = {"int", "float", "str", "bytes"}


def _sorted(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sorted expects 1 argument(s)")
    reverse = False
    key = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", "**kwargs are not lowered")
        if kw.arg == "key":
            key = kw.value
        elif kw.arg == "reverse":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, bool):
                lo.fail(kw, "PS2003", "sorted reverse= takes a True or False literal")
            reverse = kw.value.value
        else:
            lo._reject_keywords(node)
    if key is not None:
        lam = key
        if not isinstance(lam, ast.Lambda):
            lo.fail(lam, "PS2003", "sorted key= takes an inline lambda")
        args = lam.args
        params = args.posonlyargs + args.args
        variadic = args.vararg or args.kwarg or args.kwonlyargs or args.defaults or any(args.kw_defaults)
        if len(params) != 1 or variadic:
            lo.fail(lam, "PS2003", "a sorted key= lambda takes one positional parameter")
        for sub in ast.walk(lam.body):
            if isinstance(sub, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                lo.fail(lam, "PS2003", "a sorted key= lambda is a plain expression")

        arg = lo._expr(node.args[0])
        kind = arg["type"]["kind"]
        if kind == "dict":
            elem = arg["type"]["key"]
            elems_expr = "{s}.keys()"
        elif kind == "list":
            elem = arg["type"]["elem"]
            elems_expr = "{s}"
        else:
            lo.fail(node.args[0], "PS2003", "sorted key= takes a list or dict")
            raise AssertionError("unreachable")
        if elem["kind"] not in _SORT_ELEM:
            lo.fail(node.args[0], "PS2003", "sorted key= iterates int, float, str, or bytes elements")

        w = params[0].arg
        fields = lam.body.elts if isinstance(lam.body, ast.Tuple) else [lam.body]

        saved_w = lo.locals.get(w)
        lo.locals[w] = elem
        probes = [lo._expr(field) for field in fields]
        if saved_w is None:
            del lo.locals[w]
        else:
            lo.locals[w] = saved_w
        if any(p["type"]["kind"] not in _SORT_ELEM for p in probes):
            lo.fail(lam, "PS2003", "a sorted key= lambda returns scalars or a tuple of scalars")

        def temp(hint: str) -> str:
            return lo._temp(hint).replace(".", "_")

        s = temp("seq")
        e = temp("elems")
        keys = [temp(f"k{i}") for i in range(len(fields))]
        n = temp("n")
        idx = temp("idx")
        i = temp("i")
        j = temp("j")
        v = temp("v")
        out = temp("out")

        lo.locals[s] = arg["type"]
        lo.prelude.append({"kind": "assign", "target": s, "value": arg, "span": span})
        lo.locals[e] = {"kind": "list", "elem": elem}
        for p, k in zip(probes, keys, strict=True):
            lo.locals[k] = {"kind": "list", "elem": p["type"]}
        for t in (n, i, j, v):
            lo.locals[t] = {"kind": "int"}
        lo.locals[idx] = {"kind": "list", "elem": {"kind": "int"}}
        lo.locals[out] = {"kind": "list", "elem": elem}

        # reverse=True flips the comparator; the insertion sort stays
        # stable, so equal keys keep their original order like CPython.
        cmp_op = ">" if reverse else "<"

        def less(field: int) -> str:
            jb = f"{idx}[{j} - 1]"
            lt = f"{keys[field]}[{v}] {cmp_op} {keys[field]}[{jb}]"
            if field == len(keys) - 1:
                return lt
            return f"{lt} or ({keys[field]}[{v}] == {keys[field]}[{jb}] and ({less(field + 1)}))"

        # The block reuses the lambda's own parameter name as the loop
        # variable; a pre-existing local is pinned and restored after.
        lines = [f"{e} = {elems_expr.format(s=s)}"]
        if saved_w is not None:
            save_w = temp("save")
            lo.locals[save_w] = elem
            lines.append(f"{save_w} = {w}")
        for k in keys:
            lines.append(f"{k} = []")
        lines.append(f"for {w} in {e}:")
        for pos, k in enumerate(keys):
            lines.append(f"    {k}.append({ast.unparse(fields[pos])})")
        if saved_w is not None:
            lines.append(f"{w} = {save_w}")
        lines += [
            f"{n} = len({keys[0]})",
            f"{idx} = []",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {idx}.append({i})",
            f"    {i} = {i} + 1",
            f"{i} = 1",
            f"while {i} < {n}:",
            f"    {v} = {idx}[{i}]",
            f"    {j} = {i}",
            f"    while {j} > 0 and ({less(0)}):",
            f"        {idx}[{j}] = {idx}[{j} - 1]",
            f"        {j} = {j} - 1",
            f"    {idx}[{j}] = {v}",
            f"    {i} = {i} + 1",
            f"{out} = []",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {out}.append({e}[{idx}[{i}]])",
            f"    {i} = {i} + 1",
        ]
        for stmt in ast.parse("\n".join(lines)).body:
            for sub in ast.walk(stmt):
                sub.lineno, sub.col_offset = node.lineno, node.col_offset
                sub.end_lineno, sub.end_col_offset = node.end_lineno, node.end_col_offset
            lo.prelude.extend(lo._body([stmt]))
        return {"kind": "name", "id": out, "type": {"kind": "list", "elem": elem}, "span": span}
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list" or arg["type"]["elem"]["kind"] not in _SORT_ELEM:
        lo.fail(node, "PS2003", "sorted takes a list of int, float, str, or bytes")
    folded = _const_sorted(arg, span)
    if folded is not None:
        if reverse:
            folded = {**folded, "elems": folded["elems"][::-1]}
        return folded
    kind = arg["type"]["elem"]["kind"]
    symbol = {"int": "ps_sorted_i64", "float": "ps_sorted_f64", "str": "ps_sorted_buf", "bytes": "ps_sorted_buf"}[kind]
    call = lo._abi_call("builtins", "sorted", [arg], arg["type"], symbol, span)
    if not reverse:
        return call
    seq = _pin(lo, call, span, "sort")
    lo.prelude.append(
        {
            "kind": "expr",
            "expr": lo._abi_call("builtins", "list.reverse", [seq], {"kind": "none"}, "ps_seq_reverse", span),
            "span": span,
        }
    )
    return seq


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


def _field(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "attr", "value": value, "name": name, "type": typ, "span": span}


def _sub(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "subscript", "value": value, "index": index, "type": typ, "span": span}


def _len_of(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }


def _set_field(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "setattr", "object": obj, "name": name, "value": value, "span": span}


def _incr(left: dict[str, Any], by: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "op": "+", "left": left, "right": _int(by, span), "type": {"kind": "int"}, "span": span}


def _elem_list(lo: Any, node: ast.Call, source: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Materialize a non-list iterable into a list through the same `for`
    lowering a comprehension uses (str, bytes, files, dict keys elsewhere);
    non-iterables fence inside `_for` with the usual message."""

    held = _pin(lo, source, span, hint)
    var = lo._temp(hint)
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[
            ast.comprehension(
                target=ast.Name(id=var, ctx=ast.Store()),
                iter=ast.Name(id=held["id"], ctx=ast.Load()),
                ifs=[],
                is_async=False,
            )
        ],
    )
    return lo._comp(_span_to(node, comp), span, None)


def _mk_iter(lo: Any, seq: dict[str, Any], node: ast.AST, span: dict[str, Any]) -> dict[str, Any]:
    """Wrap a materialized `list[T]` in an iterator record {seq, idx}: the
    cursor `next()`/`for`/`list()` advance. R24's eager model — the elements
    are computed up front; the record only tracks position."""

    it_t = lo._iter_type(seq["type"]["elem"], node)
    itn = lo._temp("it")
    lo.locals[itn] = it_t
    made = {
        "kind": "call",
        "callee": {"kind": "class", "name": it_t["name"]},
        "args": [],
        "type": it_t,
        "span": span,
    }
    lo.prelude.extend(
        [
            _asg(itn, made, span),
            _set_field(_nm(itn, it_t, span), "seq", seq, span),
            _set_field(_nm(itn, it_t, span), "idx", _int(0, span), span),
        ]
    )
    return _nm(itn, it_t, span)


def _iter(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`it = iter(xs)`: an iterator record {seq, idx} over the source. Eager
    model: dict/str/bytes/file sources materialize first, lists are shared,
    and `iter(it)` is the same cursor."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "iter expects 1 argument(s)")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] == "class" and lo.classes.get(src_t["name"], {}).get("iterator"):
        return source
    if src_t["kind"] == "list":
        seq = source
    elif src_t["kind"] == "dict":
        key_t = src_t["key"]
        if key_t["kind"] not in {"int", "str"}:
            lo.fail(node.args[0], "PS2002", "dict keys are int or str")
        seq = lo._abi_call("builtins", "dict.keys", [source], {"kind": "list", "elem": key_t}, "ps_dict_keys", span)
    else:
        seq = _elem_list(lo, node, source, span, "ivs")
    return _mk_iter(lo, seq, node, span)


def _next(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`next(it[, d])`: reads seq[idx] and advances the cursor; raises
    StopIteration once spent, or yields `d` for the 2-arg form."""

    lo._reject_keywords(node)
    if not 1 <= len(node.args) <= 2:
        lo.fail(node, "PS2003", "next takes an iterator and an optional default")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] != "class" or not lo.classes.get(src_t["name"], {}).get("iterator"):
        lo.fail(node.args[0], "PS2002", "next takes an iterator value")
    elem = src_t["elem"]
    itn = _pin(lo, source, span, "it")
    int_t = {"kind": "int"}
    seq_t = {"kind": "list", "elem": elem}
    seq_r = _field(itn, "seq", seq_t, span)
    idx_r = _field(itn, "idx", int_t, span)
    item = _sub(seq_r, idx_r, elem, span)
    advance = _set_field(itn, "idx", _incr(idx_r, 1, span), span)
    out = lo._temp("nx")
    lo.locals[out] = elem
    if len(node.args) == 1:
        lo.prelude.extend(
            [
                {
                    "kind": "if",
                    "cond": _cmp(">=", idx_r, _len_of(seq_r, span), span),
                    "then": [{"kind": "raise", "exc": "StopIteration", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                _asg(out, item, span),
                advance,
            ]
        )
        return _nm(out, elem, span)
    default = lo._expr(node.args[1])
    lo._expect(node.args[1], default["type"], elem, "next")
    lo.prelude.append(
        {
            "kind": "if",
            "cond": _cmp("<", idx_r, _len_of(seq_r, span), span),
            "then": [_asg(out, item, span), advance],
            "else": [_asg(out, default, span)],
            "span": span,
        }
    )
    return _nm(out, elem, span)


def _fn_ref(lo: Any, fn: ast.expr, span: dict[str, Any], who: str) -> ast.expr:
    """map/filter's callable argument: names and attributes call directly;
    any other expression lowering to a func value is pinned so the
    synthesized element call can name it."""

    if isinstance(fn, (ast.Name, ast.Attribute)):
        return fn
    value = lo._expr(fn)
    if value["type"]["kind"] != "func":
        lo.fail(fn, "PS2003", f"{who} takes a function name or an inline lambda")
    temp = lo._temp("fn")
    lo.locals[temp] = value["type"]
    lo.prelude.append(_asg(temp, value, span))
    return ast.Name(id=temp, ctx=ast.Load())


def _lambda_params(lo: Any, lam: ast.Lambda, want: int, who: str) -> list[str]:
    args = lam.args
    plain = args.posonlyargs + args.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or any(args.kw_defaults or []):
        lo.fail(lam, "PS2003", f"a {who} lambda takes plain positional parameters")
    if len(plain) != want:
        lo.fail(lam, "PS2003", f"a {who} lambda takes {want} parameter(s)")
    return [arg.arg for arg in plain]


def _map(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`map(f, xs...)` lowers to the comprehension `[f(x, ...) for x, ... in
    xs|zip(xs...)]` behind an iterator record — the elements materialize
    eagerly, and the result drains once, like CPython's `map`."""

    lo._reject_keywords(node)
    if len(node.args) < 2:
        lo.fail(node, "PS2003", "map takes a function and at least one iterable")
    fn = node.args[0]
    iters = list(node.args[1:])
    if isinstance(fn, ast.Lambda):
        names = _lambda_params(lo, fn, len(iters), "map")
        elt = fn.body
    else:
        names = [lo._temp("mv") for _ in iters]
        func = _fn_ref(lo, fn, span, "map")
        elt = ast.Call(func=func, args=[ast.Name(id=n, ctx=ast.Load()) for n in names], keywords=[])
    target = (
        ast.Name(id=names[0], ctx=ast.Store())
        if len(names) == 1
        else ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Store()) for n in names], ctx=ast.Store())
    )
    iter_node = (
        iters[0] if len(iters) == 1 else ast.Call(func=ast.Name(id="zip", ctx=ast.Load()), args=iters, keywords=[])
    )
    comp = ast.ListComp(
        elt=elt,
        generators=[ast.comprehension(target=target, iter=iter_node, ifs=[], is_async=False)],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _filter(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`filter(f, xs)` lowers to `[x for x in xs if f(x)]` behind an iterator
    record; `filter(None, xs)` keeps the truthy elements."""

    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "filter takes a function and an iterable")
    fn, xs = node.args
    if isinstance(fn, ast.Lambda):
        names = _lambda_params(lo, fn, 1, "filter")
        var = names[0]
        ifs = [fn.body]
    elif isinstance(fn, ast.Constant) and fn.value is None:
        var = lo._temp("fx")
        ifs = [ast.Name(id=var, ctx=ast.Load())]
    else:
        var = lo._temp("fx")
        func = _fn_ref(lo, fn, span, "filter")
        ifs = [ast.Call(func=func, args=[ast.Name(id=var, ctx=ast.Load())], keywords=[])]
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[ast.comprehension(target=ast.Name(id=var, ctx=ast.Store()), iter=xs, ifs=ifs, is_async=False)],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _reversed(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`reversed(xs)` is an iterator over the step--1 sequence slice of the
    materialized elements (dict reverses the key list)."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "reversed expects 1 argument(s)")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] == "list" and not src_t.get("set"):
        seq = source
    elif src_t["kind"] == "dict":
        key_t = src_t["key"]
        if key_t["kind"] not in {"int", "str"}:
            lo.fail(node.args[0], "PS2002", "dict keys are int or str")
        seq = lo._abi_call("builtins", "dict.keys", [source], {"kind": "list", "elem": key_t}, "ps_dict_keys", span)
    elif src_t["kind"] in {"str", "bytes"}:
        seq = _elem_list(lo, node, source, span, "rvs")
    else:
        lo.fail(node.args[0], "PS2002", "reversed takes a sequence")
        raise AssertionError("unreachable")
    elem = seq["type"]["elem"]
    if elem["kind"] == "bool":
        lo.fail(node, "PS2002", "list[bool] is not lowered")
    rev = lo._abi_call(
        "builtins",
        "reversed",
        [seq, _int(0, span), _int(0, span), _int(0, span), _int(0, span), _int(-1, span)],
        {"kind": "list", "elem": elem},
        "ps_seq_slice",
        span,
    )
    return _mk_iter(lo, rev, node, span)


def _range_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`r = range(n)` as a value: the materialized list of the same items the
    `for i in range(n)` form yields."""

    lo._reject_keywords(node)
    var = lo._temp("rv")
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[ast.comprehension(target=ast.Name(id=var, ctx=ast.Store()), iter=node, ifs=[], is_async=False)],
    )
    return lo._comp(_span_to(node, comp), span, None)


def _enumerate_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`e = enumerate(xs)` as a value: an iterator record over the
    materialized (index, item) pairs the for-loop form yields."""

    lo._reject_keywords(node)
    index = lo._temp("ei")
    var = lo._temp("ev")
    comp = ast.ListComp(
        elt=ast.Tuple(
            elts=[ast.Name(id=index, ctx=ast.Load()), ast.Name(id=var, ctx=ast.Load())],
            ctx=ast.Load(),
        ),
        generators=[
            ast.comprehension(
                target=ast.Tuple(
                    elts=[ast.Name(id=index, ctx=ast.Store()), ast.Name(id=var, ctx=ast.Store())],
                    ctx=ast.Store(),
                ),
                iter=node,
                ifs=[],
                is_async=False,
            )
        ],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _zip_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`z = zip(a, b)` as a value: an iterator record over the materialized
    pairs the for-loop form yields."""

    lo._reject_keywords(node)
    names = [lo._temp("zv") for _ in node.args]
    comp = ast.ListComp(
        elt=ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Load()) for n in names], ctx=ast.Load()),
        generators=[
            ast.comprehension(
                target=ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Store()) for n in names], ctx=ast.Store()),
                iter=node,
                ifs=[],
                is_async=False,
            )
        ],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


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


_RAND_LEN = {"kind": "int"}


def _rand_len_of(arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [arg],
        "type": {"kind": "int"},
        "span": span,
    }


def _random_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """random lowers onto a single shared MT19937 in ps_rand.c.

    Seed parity with CPython is kept for int seeds (init_by_array on the
    little-endian limbs); seed() with no argument is nondeterministic.
    """
    lo._reject_keywords(node)
    if attr == "seed":
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "random.seed expects 0 or 1 argument(s)")
        if not node.args:
            return lo._abi_call("random", "seed", [], {"kind": "none"}, "ps_rand_seed_auto", span)
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "random.seed argument is int")
        return lo._abi_call("random", "seed", [arg], {"kind": "none"}, "ps_rand_seed", span)
    if attr == "random":
        if node.args:
            lo.fail(node, "PS2003", "random.random expects 0 argument(s)")
        return lo._abi_call("random", "random", [], {"kind": "float"}, "ps_rand_f64", span)
    if attr == "randint":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "random.randint expects 2 argument(s)")
        args = [lo._expr(arg) for arg in node.args]
        for arg in args:
            if arg["type"]["kind"] != "int":
                lo.fail(node, "PS1003", "random.randint arguments are int")
        return lo._abi_call("random", "randint", args, {"kind": "int"}, "ps_rand_range2", span)
    if attr == "randrange":
        if not 1 <= len(node.args) <= 3:
            lo.fail(node, "PS2003", "random.randrange expects 1 to 3 argument(s)")
        args = [lo._expr(arg) for arg in node.args]
        for arg in args:
            if arg["type"]["kind"] != "int":
                lo.fail(node, "PS1003", "random.randrange arguments are int")
        if len(args) == 1:
            args = [_const_int(0, span), args[0]]
        if len(args) == 2:
            args.append(_const_int(1, span))
        return lo._abi_call("random", "randrange", args, {"kind": "int"}, "ps_rand_range3", span)
    if attr == "uniform":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "random.uniform expects 2 argument(s)")
        args = [_as_float(lo, node, lo._expr(arg), span, "random.uniform") for arg in node.args]
        return lo._abi_call("random", "uniform", args, {"kind": "float"}, "ps_rand_uniform", span)
    if attr == "getrandbits":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.getrandbits expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "random.getrandbits argument is int")
        return lo._abi_call("random", "getrandbits", [arg], {"kind": "int"}, "ps_rand_bits", span)
    if attr == "choice":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.choice expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        kind = arg["type"]["kind"]
        if kind == "list":
            elem = arg["type"]["elem"]
        elif kind == "str":
            elem = {"kind": "str"}
        else:
            lo.fail(node, "PS1003", f"random.choice takes a list or str, not {kind}")
        length = _rand_len_of(arg, span)
        index = lo._abi_call("random", "choice", [length], {"kind": "int"}, "ps_rand_index", span)
        return {"kind": "subscript", "value": arg, "index": index, "type": elem, "span": span}
    if attr == "shuffle":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.shuffle expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != "list":
            lo.fail(node, "PS1003", "random.shuffle takes a list")
        return lo._abi_call("random", "shuffle", [arg], {"kind": "none"}, "ps_seq_shuffle", span)
    lo.fail(node, "PS2001", f"random.{attr} is not in the lowered subset of random")
    raise AssertionError("unreachable")


def _csv_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """csv.reader is eager: it returns list[list[str]] from a file or a
    list[str] of physical lines. csv.writer wraps the TextIO handle itself as
    the CsvWriter value and writes excel-dialect rows."""
    lo._reject_keywords(node)
    if attr == "reader":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.reader expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = arg["type"]
        rows: dict[str, Any] = {"kind": "list", "elem": {"kind": "list", "elem": {"kind": "str"}}}
        if typ["kind"] == "list" and typ["elem"]["kind"] == "str":
            return lo._abi_call("csv", "reader", [arg], rows, "ps_csv_read_lines", span)
        if typ["kind"] == "class" and typ.get("name") == "TextIO":
            return lo._abi_call("csv", "reader", [arg], rows, "ps_csv_read_io", span)
        lo.fail(node, "PS1003", "csv.reader takes a list[str] or a text file")
    if attr == "writer":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.writer expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = arg["type"]
        if typ["kind"] == "class" and typ.get("name") == "TextIO":
            return lo._abi_call("csv", "writer", [arg], _class("CsvWriter"), "ps_csv_writer", span)
        lo.fail(node, "PS1003", "csv.writer takes a text file")
    lo.fail(node, "PS2001", f"csv.{attr} is not in the lowered subset of csv")
    raise AssertionError("unreachable")


def _textwrap_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """textwrap.fill/wrap/shorten take text plus an optional width (default 70);
    dedent and indent ride the fixed table."""
    if attr in {"fill", "wrap", "shorten"}:
        slots = lo._place_nodes(node, ["text", "width"])
        if slots[0] is None:
            lo.fail(node, "PS2003", f"textwrap.{attr} expects a text argument")
        assert slots[0] is not None
        text = lo._expr(slots[0])
        if text["type"]["kind"] != "str":
            lo.fail(node, "PS1003", f"textwrap.{attr} text is str")
        if slots[1] is None:
            if attr == "shorten":
                lo.fail(node, "PS2003", "textwrap.shorten expects a width argument")
            width = _const_int(70, span)
        else:
            width = lo._expr(slots[1])
            if width["type"]["kind"] != "int":
                lo.fail(node, "PS1003", f"textwrap.{attr} width is int")
        if attr == "wrap":
            return lo._abi_call(
                "textwrap",
                attr,
                [text, width],
                {"kind": "list", "elem": {"kind": "str"}},
                "ps_wrap_wrap",
                span,
            )
        symbol = "ps_wrap_fill" if attr == "fill" else "ps_wrap_shorten"
        return lo._abi_call("textwrap", attr, [text, width], {"kind": "str"}, symbol, span)
    lo.fail(node, "PS2001", f"textwrap.{attr} is not in the lowered subset of textwrap")
    raise AssertionError("unreachable")


_COPY_SCALARS = {"int", "float", "bool", "str", "bytes"}


def _copyable(typ: dict[str, Any]) -> bool:
    kind = typ["kind"]
    if kind in {"int", "float", "str", "bytes"}:
        return True
    if kind == "list":
        return _copyable(typ["elem"])
    if kind == "dict":
        return _copyable(typ["value"])
    return False


def _copy_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """copy.copy/deepcopy: scalars are returned as-is; shallow copies of
    list/dict memcpy the container; deepcopy walks a static type table the
    emitter writes next to the call (ps_copy_deep)."""
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"copy.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    typ = arg["type"]
    kind = typ["kind"]
    if kind in _COPY_SCALARS:
        return arg
    if attr == "copy":
        if kind == "list":
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_seq", span)
        if kind == "dict":
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_map", span)
    else:
        if kind in {"list", "dict"} and _copyable(typ):
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_deep", span)
    lo.fail(node, "PS2001", f"copy.{attr} is not lowered for {kind}")
    raise AssertionError("unreachable")


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
