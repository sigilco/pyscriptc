"""Wave-call lowering, split verbatim from `lower._calls`.

`CallsMoreMixin` holds the wave-call family the queued-module dispatch
reaches for: the `math` rows (`_math_call`, `_math_gcd`, `_math_isclose`),
the fixed-spec wave call (`_fixed_call` with `_expect_wave`), the `json`
family (`_json_loads`, `_json_load`, `_json_dump`, `_json_dumps`,
`_json_dumps_flags`, `_json_type`) and the `struct` row (`_struct_call`).
`CallsMixin` composes it the way `StdlibDispatchMixin` composes
`StdlibBindMixin`, so every method stays reachable as `self._math_call(...)`
and friends. The module-level helpers the family needs (`_const_float_text`,
`_const_text`, `_capturing_groups`, `_struct_arg_kinds`, `_ASCII_SPACE`,
`_FLOAT_WORDS`) and the type-spelling helpers it reads (`_type_name`,
`_t_leaf`, `_t_list`, `_t_class`) moved with it, so nothing here
back-imports `lower._calls`. Every body below is the code that lived in
`lower._calls`, unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
import struct
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _const_none,
    _const_str,
    _mk_span,
    _mk_type,
    _node_type_kind,
    _show,
    _type_kind,
)
from pyscriptc.lower._stdlib_shared import _mod_call
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.stdlib_queue import math_function
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


class CallsMoreMixin:
    def _math_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        spec = math_function(attr)
        if spec is None:
            self.fail(node, "PS2001", f"math.{attr} is not in the lowered subset of math")
        if len(node.args) != len(spec["params"]):
            self.fail(node, "PS2003", f"math.{attr} expects {len(spec['params'])} argument(s)")
        args = []
        for arg_node, want in zip(node.args, spec["params"], strict=True):
            arg = self._expr(arg_node)
            if _type_kind(want) == "float" and _node_type_kind(arg) == "int":
                arg = node_to_dict(_ir.cast("int_to_float", dict_to_node(arg), _ir.t_leaf("float"), _mk_span(span)))
            if not types_equal(arg["type"], want):
                self.fail(node, "PS1003", f"math.{attr} argument is {_show(arg['type'])}, expected {_show(want)}")
            args.append(arg)
        return _mod_call("math", attr, args, spec["return"], span)

    def _math_int_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        """`factorial(n)` and `lcm(a, b)`: the int helpers lower like gcd does."""
        if len(node.args) != 2 and attr == "lcm":
            self.fail(node, "PS2003", "math.lcm expects 2 argument(s)")
        if len(node.args) != 1 and attr == "factorial":
            self.fail(node, "PS2003", "math.factorial expects 1 argument(s)")
        args = [self._expr(arg_node) for arg_node in node.args]
        for arg in args:
            if _node_type_kind(arg) != "int":
                self.fail(node, "PS1003", f"math.{attr} argument is {_show(arg['type'])}, expected int")
        return self._abi_call("math", attr, args, _t_leaf("int"), f"ps_math_{attr}", span)

    def _math_gcd(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.args) != 2:
            self.fail(node, "PS2003", "math.gcd expects 2 argument(s)")
        args = [self._expr(arg_node) for arg_node in node.args]
        for arg in args:
            if _node_type_kind(arg) != "int":
                self.fail(node, "PS1003", f"math.gcd argument is {_show(arg['type'])}, expected int")
        return self._abi_call("math", "gcd", args, _t_leaf("int"), "ps_math_gcd", span)

    def _math_isclose(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        """`math.isclose(a, b, *, rel_tol=1e-09, abs_tol=0.0)`: the tolerances are keyword-only."""

        if len(node.args) != 2:
            self.fail(node, "PS2003", "math.isclose expects 2 positional argument(s)")
        given: dict[str, ast.expr] = {}
        for keyword in node.keywords:
            if keyword.arg not in {"rel_tol", "abs_tol"} or keyword.arg in given:
                self.fail(node, "PS2003", "math.isclose takes only rel_tol and abs_tol keywords")
            given[keyword.arg] = keyword.value
        defaults = {"rel_tol": "1e-09", "abs_tol": "0.0"}
        args = []
        for arg_node in [*node.args, *(given.get(name) for name in defaults)]:
            if arg_node is None:
                args.append(None)
                continue
            arg = self._expr(arg_node)
            if _node_type_kind(arg) == "int":
                arg = node_to_dict(_ir.cast("int_to_float", dict_to_node(arg), _ir.t_leaf("float"), _mk_span(span)))
            if _node_type_kind(arg) != "float":
                self.fail(node, "PS1003", f"math.isclose argument is {_show(arg['type'])}, expected float")
            args.append(arg)
        for index, name in enumerate(defaults, start=2):
            if args[index] is None:
                args[index] = _const_float_text(defaults[name], span)
        return self._abi_call("math", "isclose", args, _t_leaf("bool"), "ps_math_isclose", span)

    def _fixed_call(
        self, node: ast.Call, span: dict[str, Any], module: str, attr: str, spec: dict[str, Any]
    ) -> dict[str, Any]:
        params: list[dict[str, Any]] = spec["params"]
        names: list[str] | None = spec.get("names")
        minimum = int(spec.get("min", len(params)))
        if names:
            named: list[dict[str, Any]] = []
            defaults: list[str] = spec.get("defaults", [])
            for index, want in enumerate(params):
                param: dict[str, Any] = {"name": names[index], "type": want}
                if index >= minimum:
                    default = defaults[index - minimum]
                    param["default"] = node_to_dict(_ir.const_node(_ir.P_STR, default, _mk_type(want), _mk_span(span)))
                named.append(param)
            args = self._fill_args(node, named, f"{module}.{attr}")
            expr = _mod_call(module, attr, args, spec["return"], span)
            if "symbol" in spec:
                expr["symbol"] = spec["symbol"]
            return expr
        if node.keywords:
            self.fail(node, "PS2003", "keyword arguments are not lowered")
        if len(node.args) < minimum or len(node.args) > len(params):
            self.fail(node, "PS2003", f"{module}.{attr} expects {minimum} argument(s)")
        args: list[dict[str, Any]] = []
        defaults: list[str] = spec.get("defaults", [])
        for index, want in enumerate(params):
            if index >= len(node.args):
                default = defaults[index - minimum]
                args.append(_const_str(str(default), span))
                continue
            arg = self._expr(node.args[index])
            self._expect_wave(node, arg["type"], want, f"{module}.{attr}")
            args.append(arg)
        expr = _mod_call(module, attr, args, spec["return"], span)
        if "symbol" in spec:
            self.mobile_symbol_guard(node, spec["symbol"])
            expr["symbol"] = spec["symbol"]
        return expr

    def _expect_wave(self, node: ast.AST, got: dict[str, Any], want: dict[str, Any], what: str) -> None:
        if _type_kind(want) == "buf":
            if _type_kind(got) not in {"str", "bytes"}:
                self.fail(node, "PS1003", f"{what} argument is {_show(got)}, expected str or bytes")
            return
        if not types_equal(got, want):
            self.fail(node, "PS1003", f"{what} argument is {_show(got)}, expected {_show(want)}")

    def _json_loads(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if len(node.args) != 1:
            self.fail(node, "PS2003", "json.loads expects 1 argument(s)")
        if expect is None:
            self.fail(node, "PS1001", "json.loads needs the result annotation")
        self._json_type(node, expect, "json.loads")
        arg = self._expr(node.args[0])
        if _node_type_kind(arg) != "str":
            self.fail(node, "PS1003", "json.loads argument is " + _show(arg["type"]) + ", expected str")
        return _mod_call("json", "loads", [arg], expect, span)

    def _json_load(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        """`json.load(fp)` reads the whole handle and reuses the loads path.

        The lowering reads the file through the TextIO/BytesIO row the same
        way `f.read()` lowers, then feeds the text to `ps_json_loads` with
        the result annotation. A CPython `JSONDecodeError` stays the runtime
        ValueError the loads path raises (divergence 10).
        """
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "json.load expects 1 argument(s)")
        if expect is None:
            self.fail(node, "PS1001", "json.load needs the result annotation")
        self._json_type(node, expect, "json.load")
        handle = self._expr(node.args[0])
        if _node_type_kind(handle) != "class" or _type_name(handle["type"]) not in {"TextIO", "BytesIO"}:
            self.fail(node, "PS1003", "json.load argument is a file handle (TextIO or BytesIO)")
        kind = _t_leaf("bytes") if handle["type"]["name"] == "BytesIO" else _t_leaf("str")
        text = self._abi_call("io", "read", [handle], kind, "ps_io_read", span)
        return _mod_call("json", "loads", [text], expect, span)

    def _json_dump(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        """`json.dump(obj, fp)` serializes the value and writes the handle.

        The value goes through the same annotation-checked `json.dumps` path
        (the closed `indent`/`sort_keys`/`ensure_ascii` keyword set), then the
        text is written through the TextIO/BytesIO row the `f.write()`
        lowering uses. The handle is the second positional argument, like
        CPython.
        """
        if len(node.args) != 2:
            self.fail(node, "PS2003", "json.dump expects 2 argument(s)")
        arg = self._expr(node.args[0])
        self._json_type(node, arg["type"], "json.dump")
        handle = self._expr(node.args[1])
        if _node_type_kind(handle) != "class" or _type_name(handle["type"]) not in {"TextIO", "BytesIO"}:
            self.fail(node, "PS1003", "json.dump argument is a file handle (TextIO or BytesIO)")
        text = _mod_call("json", "dumps", [arg], _t_leaf("str"), span)
        flags = self._json_dumps_flags(node)
        if flags:
            text["json_flags"] = flags
        write = self._abi_call("io", "write", [handle, text], _t_leaf("int"), "ps_io_write", span)
        self.prelude.append(node_to_dict(_ir.expr_stmt(dict_to_node(write), _mk_span(span))))
        return _const_none(span)

    def _json_dumps(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.args) != 1:
            self.fail(node, "PS2003", "json.dumps expects 1 argument(s)")
        arg = self._expr(node.args[0])
        self._json_type(node, arg["type"], "json.dumps")
        expr = _mod_call("json", "dumps", [arg], _t_leaf("str"), span)
        flags = self._json_dumps_flags(node)
        if flags:
            expr["json_flags"] = flags
        return expr

    def _json_dumps_flags(self, node: ast.Call) -> int:
        """The dumps options: bit 0 sorts the keys, bits 8-15 the indent.

        The closed set is `indent`, `sort_keys` and `ensure_ascii`; the
        other CPython options stay fenced.
        """
        flags = 0
        for keyword in node.keywords:
            if keyword.arg is None:
                self.fail(node, "PS2002", kwargs_call_note())
            name = keyword.arg
            if name == "indent":
                value = keyword.value
                if (
                    not isinstance(value, ast.Constant)
                    or not isinstance(value.value, int)
                    or isinstance(value.value, bool)
                    or value.value < 0
                    or value.value > 255
                ):
                    self.fail(node, "PS2003", "json.dumps indent is an int constant")
                flags |= value.value << 8
            elif name == "sort_keys":
                if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, bool):
                    self.fail(node, "PS2003", "json.dumps sort_keys is a bool constant")
                if keyword.value.value:
                    flags |= 1
            elif name == "ensure_ascii":
                if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, bool):
                    self.fail(node, "PS2003", "json.dumps ensure_ascii is a bool constant")
                if not keyword.value.value:
                    self.fail(node, "PS2003", "json.dumps ensure_ascii=False is not lowered")
            else:
                self.fail(node, "PS2003", f"json.dumps keyword {name} is not lowered")
        return flags

    def _json_type(self, node: ast.AST, typ: dict[str, Any], what: str) -> None:
        kind = _type_kind(typ)
        if kind in {"int", "bool", "str", "float"}:
            return
        if typ.get("optional"):
            self._json_type(node, typ["elem"], what)
            return
        if kind == "list":
            if _type_kind(typ["elem"]) == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            self._json_type(node, typ["elem"], what)
            return
        if kind == "dict":
            if _type_kind(typ["key"]) != "str":
                self.fail(node, "PS2001", "JSON object keys are str")
            if _type_kind(typ["value"]) == "bool":
                self.fail(node, "PS2002", "dict values of bool are not lowered")
            self._json_type(node, typ["value"], what)
            return
        self.fail(node, "PS2001", f"{what} does not lower {_show(typ)}")

    def _struct_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        if len(node.args) < 1:
            self.fail(node, "PS2003", f"struct.{attr} expects a format")
        fmt = _const_text(node.args[0])
        if fmt is None:
            self.fail(node, "PS2001", "struct format must be a string literal")
        kinds = _struct_arg_kinds(fmt)
        if kinds == "native":
            self.fail(node, "PS2001", "struct native alignment is not lowered")
        if not isinstance(kinds, list):
            self.fail(node, "PS2001", "bad struct format")
        if attr in {"calcsize", "unpack_from"}:
            return self._struct_sized_call(node, span, attr, fmt, kinds)
        format_arg = self._expr(node.args[0])
        if attr == "pack":
            if len(node.args) - 1 != len(kinds):
                self.fail(node, "PS2003", f"struct.pack expects {len(kinds)} value(s) for this format")
            args = [format_arg]
            for kind, arg_node in zip(kinds, node.args[1:], strict=True):
                arg = self._expr(arg_node)
                got = _node_type_kind(arg)
                if kind == "int" and got != "int":
                    self.fail(node, "PS1003", "struct.pack expected an int")
                if kind == "float" and got == "float":
                    pass
                elif kind == "float" and got == "int":
                    pass
                elif kind == "float":
                    self.fail(node, "PS1003", "struct.pack expected a float")
                if kind == "bytes" and got not in {"bytes", "str"}:
                    self.fail(node, "PS1003", "struct.pack expected bytes")
                args.append(arg)
            return _mod_call("struct", "pack", args, _t_leaf("bytes"), span)
        if len(node.args) != 2:
            self.fail(node, "PS2003", "struct.unpack expects 2 argument(s)")
        distinct = set(kinds)
        if len(distinct) > 1:
            self.fail(node, "PS2001", "struct.unpack result types are mixed")
        elem = "int" if not distinct else next(iter(distinct))
        buffer = self._expr(node.args[1])
        if _node_type_kind(buffer) not in {"bytes", "str"}:
            self.fail(node, "PS1003", "struct.unpack buffer is bytes")
        return _mod_call(
            "struct",
            "unpack",
            [format_arg, buffer],
            _t_list(_t_leaf(elem)),
            span,
        )

    def _struct_sized_call(
        self, node: ast.Call, span: dict[str, Any], attr: str, fmt: str, kinds: list[str]
    ) -> dict[str, Any]:
        """`struct.calcsize` (a constant) and `struct.unpack_from` over a literal format.

        Both need the byte size at lower time, so the format must open with an
        explicit `<`, `>`, `!` or `=` (the native `@` default aligns and is fenced).
        """

        if not fmt.lstrip(" \t\n\r").startswith(("<", ">", "!", "=")):
            self.fail(node, "PS2001", "struct native alignment is not lowered")
        try:
            size = struct.calcsize(fmt)
        except struct.error:
            self.fail(node, "PS2001", "bad struct format")
        if attr == "calcsize":
            if len(node.args) != 1:
                self.fail(node, "PS2003", "struct.calcsize expects 1 argument(s)")
            return self._expr(ast.copy_location(ast.Constant(value=size), node))
        if len(node.args) not in {2, 3}:
            self.fail(node, "PS2003", "struct.unpack_from expects 2 or 3 argument(s)")
        distinct = set(kinds)
        if len(distinct) > 1:
            self.fail(node, "PS2001", "struct.unpack result types are mixed")
        elem = "int" if not distinct else next(iter(distinct))
        buffer = self._expr(node.args[1])
        if _node_type_kind(buffer) not in {"bytes", "str"}:
            self.fail(node, "PS1003", "struct.unpack_from buffer is bytes")
        if len(node.args) == 3:
            offset = self._expr(node.args[2])
            if _node_type_kind(offset) != "int":
                self.fail(node, "PS1003", "struct.unpack_from offset is an int")
        else:
            offset = self._expr(ast.copy_location(ast.Constant(value=0), node))
        sized = self._expr(ast.copy_location(ast.Constant(value=size), node))
        return _mod_call(
            "struct",
            "unpack_from",
            [self._expr(node.args[0]), buffer, offset, sized],
            _t_list(_t_leaf(elem)),
            span,
        )


def _type_name(typ: dict[str, Any]) -> str:
    """The class name of a class-typed type record."""

    name: str = typ["name"]
    return name


def _t_leaf(kind: str) -> dict[str, Any]:
    """The leaf type dict one type record spells."""

    return node_to_dict(_ir.t_leaf(kind))


def _t_list(elem: dict[str, Any], set_: bool = False) -> dict[str, Any]:
    """The list type dict one type record spells."""

    return node_to_dict(_ir.t_list(dict_to_type(elem), set_))


def _t_class(name: str) -> dict[str, Any]:
    """The class type dict one type record spells."""

    return node_to_dict(_ir.t_class(name))


def _const_float_text(text: str, span: dict[str, Any]) -> dict[str, Any]:
    """The float constant node from its decimal text."""
    return node_to_dict(_ir.const_node(_ir.P_STR, text, _mk_type(_t_leaf("float")), _mk_span(span)))


def _const_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _capturing_groups(pattern: str) -> int:
    index = 0
    groups = 0
    size = len(pattern)
    while index < size:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index += 1
            if index < size and pattern[index] == "^":
                index += 1
            if index < size and pattern[index] == "]":
                index += 1
            while index < size and pattern[index] != "]":
                if pattern[index] == "\\" and index + 1 < size:
                    index += 2
                    continue
                index += 1
            if index < size:
                index += 1
            continue
        if char == "(":
            if index + 2 < size and pattern[index + 1] == "?" and pattern[index + 2] == ":":
                index += 3
                continue
            if index + 1 < size and pattern[index + 1] == "?":
                index += 1
                continue
            groups += 1
        index += 1
    return groups


def _struct_arg_kinds(fmt: str) -> list[str] | str:
    """Argument kinds for a struct format, or `native` / `bad`."""

    index = 0
    kinds: list[str] = []
    size = len(fmt)
    while index < size:
        char = fmt[index]
        if char in " \t\n\r<>!=":
            index += 1
            continue
        if char == "@":
            return "native"
        count = 1
        if char.isdigit():
            count = 0
            while index < size and fmt[index].isdigit():
                count = count * 10 + (ord(fmt[index]) - 48)
                index += 1
            if index >= size:
                return "bad"
            char = fmt[index]
        index += 1
        if char == "s":
            kinds.append("bytes")
            continue
        if char == "x":
            continue
        if char in "bBhHiIlLqQ":
            kinds.extend(["int"] * count)
            continue
        if char in "fd":
            kinds.extend(["float"] * count)
            continue
        return "bad"
    return kinds


_ASCII_SPACE = " \t\n\r\v\f"
_FLOAT_WORDS = {"inf": "inf", "infinity": "inf", "nan": "nan"}
