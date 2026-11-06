"""Expression lowering for the lowerer.

Split out of `__init__.py` as E0 move 2. `Lowerer` composes this mixin in.
The area owns the `_expr` dispatch and its expression-form methods: names,
constants, unary/binary, list and dict displays, subscripts and slices,
attributes, calls, `and`/`or`, conditionals, f-strings and `%` formatting.
The compare family (`_compare`, chains and membership, the optional equality
paths) and the node builders it spells with moved verbatim to
`lower._expr_compare`, which composes here as `ExprCompareMixin`.

Tables shared with the statement, call, and typing paths (`_CMP`, `_SCALAR`,
`_TRUTHY`, `_LIST_FORMS`, `_DICT_FORMS`, `_SET_FORMS`) live in `_core.py`;
`_INT_BIN`/`_FLOAT_BIN` are expression-only and stay here. The boundary calls
in both directions stay `self._method()` so the move is behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import type_ir
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _TRUTHY,
    _any_contains,
    _assign,
    _attr,
    _binary_int,
    _compare,
    _concat_str,
    _const_int,
    _const_list_concat,
    _const_none,
    _const_str_join,
    _name,
    _node_text,
    _op_text,
    _show,
    _static_int,
    _zero,
)
from pyscriptc.lower._expr_compare import (
    _NUMERIC,
    ExprCompareMixin,
    _assign_node,
    _attr_node,
    _cast_node,
    _compare_node,
    _contains_node,
    _expr_stmt_node,
    _if_node,
    _mk_span,
    _mk_type,
    _subscript_node,
    _unary_node,
    _while_node,
)
from pyscriptc.reflection import inspect_note, typing_note
from pyscriptc.restlower._argparse import path_attribute
from pyscriptc.sigrec import type_to_dict
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    wave_function,
)
from pyscriptc.stdmore import class_constant, member_callable, module_constant
from pyscriptc.tirserde import dict_to_node, node_to_dict

_INT_BIN = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.BitOr: "|",
    ast.BitAnd: "&",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
}
_FLOAT_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%", ast.FloorDiv: "//"}


def _const_node(value: Any, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The const node one dict-IR const spells."""
    kind = typ["kind"]
    if kind == "bool":
        ptag, svalue = _ir.P_BOOL, "true" if value else "false"
    elif kind == "none":
        ptag, svalue = _ir.P_NONE, ""
    else:
        ptag = _PTAGS[kind]
        svalue = value
    return node_to_dict(_ir.const_node(ptag, svalue, _mk_type(typ), _mk_span(span)))


_PTAGS = {"str": _ir.P_STR, "int": _ir.P_INT, "float": _ir.P_FLOAT, "bytes": _ir.P_BYTES}


def _const_of(value: Any, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    """The const node with the leaf type `kind` spells."""
    return _const_node(value, {"kind": kind}, span)


def _binary_node(
    op: str, left: dict[str, Any], right: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The binary operation node."""
    return node_to_dict(_ir.binary(op, dict_to_node(left), dict_to_node(right), _mk_type(typ), _mk_span(span)))


def _call_node(
    callee: dict[str, Any],
    args: list[dict[str, Any]],
    typ: dict[str, Any],
    span: dict[str, Any],
    *,
    format_style: str = "",
    format_kwnames: int = -1,
) -> dict[str, Any]:
    """The call node one dict-IR call spells."""
    return node_to_dict(
        _ir.call(
            dict_to_node(callee),
            [dict_to_node(arg) for arg in args],
            _mk_type(typ),
            _mk_span(span),
            "",
            format_style,
            format_kwnames,
        )
    )


def _callee_node(callee: dict[str, Any]) -> Any:
    """The callee record one dict-IR callee spells."""
    return dict_to_node(callee)


def _builtin_call(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The call to a builtin."""
    return _call_node({"kind": "builtin", "name": name}, args, typ, span)


def _class_call(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The call to a class constructor."""
    return _call_node({"kind": "class", "name": name}, args, typ, span)


def _value_call(
    target: dict[str, Any], args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The indirect call through a callable value."""
    return _call_node({"kind": "value", "target": target}, args, typ, span)


def _closure_node(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The closure node with the empty environment."""
    return node_to_dict(_ir.closure(name, [], _mk_type(typ), _mk_span(span)))


def _setattr_node(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The attribute write node."""
    return node_to_dict(_ir.setattr_stmt(dict_to_node(obj), name, dict_to_node(value), _mk_span(span)))


def _setitem_node(
    container: dict[str, Any], index: dict[str, Any], value: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The container store node."""
    return node_to_dict(
        _ir.setitem_stmt(dict_to_node(container), dict_to_node(index), dict_to_node(value), _mk_span(span))
    )


def _name_node(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The local read node."""
    return node_to_dict(_ir.name_expr(name, _mk_type(typ), _mk_span(span)))


def _list_node(elems: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The list display node."""
    return node_to_dict(_ir.list_lit([dict_to_node(elem) for elem in elems], _mk_type(typ), _mk_span(span)))


def _dict_node(pairs: list[Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict display node; a pair is the `{"key": .., "value": ..}` dict."""
    return node_to_dict(
        _ir.dict_lit(
            [_ir.Pair(dict_to_node(pair["key"]), dict_to_node(pair["value"])) for pair in pairs],
            _mk_type(typ),
            _mk_span(span),
        )
    )


def _raise_node(exc: str, span: dict[str, Any]) -> dict[str, Any]:
    """The bare raise node with no message."""
    return node_to_dict(_ir.raise_stmt(exc, _mk_span(span), -1))


class ExprMixin(ExprCompareMixin):
    def _expr(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        value = self._expr_any(node, expect)
        # boxed:begin
        if expect is not None and not expect.get("island") and value["type"].get("island"):
            return self._island_unmarshal(value, expect, span_of(node, self.filename), node)
        # boxed:end
        if expect is not None and expect.get("optional") and not value["type"].get("optional"):
            # `T | None` takes a bare T or None by boxing it: heap value with a
            # `_tag` flag and a `_val` field, allocated zeroed (tag false).
            if value["type"]["kind"] == "none" or types_equal(value["type"], expect["elem"]):
                return self._opt_box(
                    None if value["type"]["kind"] == "none" else value, expect, span_of(node, self.filename)
                )
        return value

    def _opt_box(self, value: dict[str, Any] | None, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Box `value` into the `opt_*` heap cell, or emit the tagged-empty one.

        The cell is calloc'd, so `_tag` starts false (None); a wrapped value
        flips the tag and stores `_val`.
        """
        if value is not None and value["type"]["kind"] == "none":
            value = None
        dest = self._temp("opt")
        self.locals[dest] = typ
        made = _class_call(typ["name"], [], typ, span)
        self.prelude.append(_assign_node(dest, made, span))
        box = _name_node(dest, typ, span)
        if value is not None:
            self.prelude.append(_setattr_node(box, "_tag", _const_of(True, "bool", span), span))
            self.prelude.append(_setattr_node(_name_node(dest, typ, span), "_val", value, span))
        return box

    def _opt_truthy(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Truthiness of a tagged optional: tagged and its value truthy.

        Heap elements are truthy whenever they exist, so the tag alone decides.
        """
        typ = expr["type"]
        tag = _attr_node(expr, "_tag", {"kind": "bool"}, span)
        if typ["elem"]["kind"] in {"class", "exc"}:
            return tag
        val = _attr_node(expr, "_val", typ["elem"], span)
        res = self._temp("optcond")
        self.locals[res] = {"kind": "bool"}
        self.prelude.append(_assign_node(res, tag, span))
        retag = _attr_node(expr, "_tag", {"kind": "bool"}, span)
        inner = self._truthy(val, span)
        self.prelude.append(_if_node(retag, [_assign_node(res, inner, span)], [], span))
        return _name_node(res, {"kind": "bool"}, span)

    def _expr_any(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        span = span_of(node, self.filename)
        if expect is None and self.inf_peek:
            # The inference peek has no annotation to read an expected type
            # from. An unannotated empty display is ambiguous, never a
            # guess: the peek refuses it and the site keeps its fence with
            # the hint. A non-empty display lowers without an expectation
            # and its element type is the answer.
            expect = self.peek_infer
            self.peek_infer = None
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return self._name(node, span)
        if isinstance(node, ast.Constant):
            return self._const(node, span)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node, span)
        if isinstance(node, ast.BinOp):
            return self._binary(node, span)
        if isinstance(node, ast.Compare):
            return self._compare(node, span)
        if isinstance(node, ast.Call):
            return self._call(node, span, expect)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, span, expect)
        if isinstance(node, ast.List):
            return self._list(node, span, expect)
        if isinstance(node, ast.Set):
            return self._set_display(node, span, expect)
        if isinstance(node, ast.Dict):
            return self._dict(node, span, expect)
        if isinstance(node, ast.Tuple):
            return self._tuple_expr(node, span)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, span, expect)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, span)
        if isinstance(node, ast.JoinedStr):
            return self._joined(node, span)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # Eager model: generator expressions materialize into a list at
            # the point of evaluation, like the iterator builtins do.
            return self._comp(node, span, expect)
        if isinstance(node, ast.IfExp):
            return self._ifexp(node, span)
        if isinstance(node, ast.Lambda):
            return self._lambda(node, span, expect)
        self.fail(node, "PS2002", f"{_node_text(node)} is not lowered")
        raise AssertionError("unreachable")

    def _name(self, node: ast.Name, span: dict[str, Any]) -> dict[str, Any]:
        if node.id in self.dead_names:
            # `except E as e` deletes e at handler end (CPython's `del e`).
            # CPython raises NameError there; the subset fences instead.
            self.fail(node, "PS2002", f"{node.id} is deleted by the except handler that bound it")
        if node.id in self.consumed_handles:
            # A consuming call already took this handle: the value is a dead
            # pointer, and reading it would use a freed handle (#224).
            self.fail(
                node,
                "PS4010",
                f"{node.id} was consumed by an ffi call and is dead; open the resource again or use the "
                "handle the consuming call returned",
            )
        elif node.id in self.locals:
            narrowed = self.opt_unwrap.get(node.id)
            if narrowed is not None:
                # Inside `if x is not None:` a read of x is its tagged value.
                return _attr_node(_name(node.id, narrowed, span), "_val", narrowed["elem"], span)
            return _name(node.id, self.locals[node.id], span)
        elif node.id in self.captures:
            return _name(node.id, self.captures[node.id], span)
        slot = self.global_slots.get(node.id)
        if slot is not None and slot.get("active"):
            return self._global_get(node.id, span)
        if node.id in self.constants:
            found = self.constants[node.id]
            if found.get("kind") == "tuple-const":
                return self._materialize_tuple(found["elems"], span)
            return {**found, "span": span}
        if node.id == "__name__":
            return _const_of(self.module_name or "__main__", "str", span)
        if node.id in self.type_aliases:
            note = typing_note(node.id)
            if note is not None:
                self.fail(node, "PS2002", note)
            self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
        if node.id in self.typevars:
            self.fail(node, "PS2002", f"{node.id} is a TypeVar and is not a value")
        binding = self.bindings.get(node.id)
        if binding is not None:
            if binding.queued:
                self._queued(node, f"{binding.module}.{binding.attr}" if binding.attr else binding.module)
            if binding.kind == "erased":
                note = typing_note(node.id)
                if note is not None:
                    self.fail(node, "PS2002", note)
                self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
            if binding.kind == "handle":
                # A handle name is a type, not a value: `Conn` in an
                # annotation is the handle type; as a value it is opaque.
                self.fail(node, "PS4014", f"{node.id} is a handle type, not a value")
            if binding.kind == "const":
                manifest = self._ffi_module(binding.module)
                if manifest is not None:
                    found_const = manifest.const(binding.attr)
                    if found_const is None:
                        self.fail(node, "PS2001", f"{binding.module}.{binding.attr} is not a constant")
                    return _const_node(
                        found_const.value,
                        type_ir(found_const.type, manifest.handle_names),
                        span,
                    )
                if binding.module == "math":
                    value: Any = MATH_CONSTANTS[binding.attr]
                    typ: dict[str, Any] = {"kind": "float"}
                else:
                    found = module_constant(binding.module, binding.attr)
                    if found is None:
                        self.fail(node, "PS2001", f"{binding.module}.{binding.attr} is not a constant")
                    value, typ = found
                return _const_node(value, typ, span)
            if binding.kind in {"module", "pkg", "island", "island_member"}:
                self.fail(node, "PS2002", f"module {node.id} is not a value in the static subset")
            self.fail(node, "PS2002", f"{node.id} is a function and is not a value")
        # boxed:begin
        # An island class taken as a value is a handle to the class object
        # inside the embedded interpreter (issue #628): its attributes read
        # through `ps_island_getattr` and the class itself calls through
        # `ps_island_fn_invoke`, like any other island definition.
        if node.id in self.island_classes:
            return self._island_class_handle(node.id, span)
        # boxed:end
        if node.id in self.classes:
            self.fail(node, "PS2002", f"class {node.id} is not a value")
        if node.id in self.sigs:
            if node.id in self.generic_fns:
                self.fail(node, "PS2002", f"{node.id} is generic and is not a value")
            # boxed:begin
            # The boxed tier (issue #612, Decision E): no cross-seam
            # callables in this stage. A function value that names an
            # island function (threading.Thread(target=fn), a first-class
            # alias) would need the callable to cross the seam, so it
            # fences naming the function and the typed idiom.
            if node.id in self.island_fns:
                self.fail(
                    node,
                    "PS2002",
                    f"{node.id} runs on the island and cannot be taken as a value in compiled code: "
                    "pass a small typed wrapper that calls it instead",
                )
            # boxed:end
            # A function referenced as a value: the closure wraps it with an
            # empty environment (the emitter adds a lazy env-first shim).
            params, ret = self.sigs[node.id]
            ftype = {"kind": "func", "params": [param["type"] for param in params], "return": ret}
            return _closure_node(self._ir_name(node.id), ftype, span)
        if node.id in EXCEPTIONS:
            self.fail(node, "PS2002", f"{node.id} is only lowered in raise and except")
        self.fail(node, "PS2002", f"{node.id} is not a local or parameter")
        raise AssertionError("unreachable")

    def _const(self, node: ast.Constant, span: dict[str, Any]) -> dict[str, Any]:
        value = node.value
        if isinstance(value, bool):
            return _const_of(value, "bool", span)
        if isinstance(value, int):
            return _const_of(str(value), "int", span)
        if isinstance(value, float):
            return _const_of(repr(value), "float", span)
        if isinstance(value, str):
            # S2: str is a code-point sequence stored UTF-8, so any scalar
            # code point lowers; lone surrogates have no UTF-8 bytes and
            # stay fenced (the surrogate policy, divergences item 8).
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
                self.fail(node, "PS2005", "string literals with lone surrogates are not lowered")
            return _const_of(value, "str", span)
        if isinstance(value, bytes):
            return _const_of(value.decode("latin1"), "bytes", span)
        if value is None:
            return _const_none(span)
        self.fail(node, "PS2002", f"literal {type(value).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _unary(self, node: ast.UnaryOp, span: dict[str, Any]) -> dict[str, Any]:
        operand = self._expr(node.operand)
        kind = operand["type"]["kind"]
        if isinstance(node.op, ast.USub) and kind == "int":
            return _unary_node("-", operand, {"kind": "int"}, span)
        if isinstance(node.op, ast.USub) and kind == "float":
            return _unary_node("-", operand, {"kind": "float"}, span)
        if isinstance(node.op, ast.Not) and kind == "bool":
            return _unary_node("not", operand, {"kind": "bool"}, span)
        if isinstance(node.op, ast.Not) and (kind in _TRUTHY or operand["type"].get("optional")):
            inner = self._truthy(operand, span)
            return _unary_node("not", inner, {"kind": "bool"}, span)
        if isinstance(node.op, ast.USub) and kind == "class" and operand["type"].get("name") == "TimeDelta":
            return _unary_node("-", operand, operand["type"], span)
        if isinstance(node.op, ast.UAdd) and kind in _NUMERIC:
            return operand
        if isinstance(node.op, ast.Invert) and kind == "int":
            return _unary_node("~", operand, {"kind": "int"}, span)
        self.fail(node, "PS2004", f"unary {_op_text(node.op)} on {kind} is not lowered")
        raise AssertionError("unreachable")

    def _binary(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        if isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            return self._percent(node, span)
        left = self._expr(node.left)
        right = self._expr(node.right)
        left_kind = left["type"]["kind"]
        right_kind = right["type"]["kind"]
        if left_kind == "bool" or right_kind == "bool":
            self.fail(node, "PS2004", "bool arithmetic is not lowered (bool is a subclass of int in Python)")
        if isinstance(node.op, ast.Pow):
            return self._pow(node, left, right, span)
        if isinstance(node.op, ast.Mod) and (left_kind not in _NUMERIC or right_kind not in _NUMERIC):
            # Anything but a numeric `%` stays on the format-string path.
            return self._percent(node, span)
        if isinstance(node.op, ast.BitOr):
            which = "union"
        elif isinstance(node.op, ast.BitAnd):
            which = "inter"
        elif isinstance(node.op, ast.Sub):
            which = "diff"
        elif isinstance(node.op, ast.BitXor):
            which = "xor"
        else:
            which = ""
        if (
            which
            and left_kind == "list"
            and right_kind == "list"
            and left["type"].get("set")
            and right["type"].get("set")
            and types_equal(left["type"]["elem"], right["type"]["elem"])
        ):
            return self._set_algebra(span, left, right, which)
        op_type = type(node.op)
        if left_kind in _NUMERIC and right_kind in _NUMERIC:
            if left_kind == "int" and right_kind == "int" and op_type in _INT_BIN:
                return _binary_int(_INT_BIN[op_type], left, right, span)
            if op_type in _FLOAT_BIN:
                # A float operand — or `/` itself — lifts the int side through
                # sitofp, the same promotion `float(x)` emits.
                float_t = {"kind": "float"}
                if left_kind == "int":
                    left = _cast_node("int_to_float", left, float_t, span)
                if right_kind == "int":
                    right = _cast_node("int_to_float", right, float_t, span)
                return _binary_node(_FLOAT_BIN[op_type], left, right, float_t, span)
        if left_kind == right_kind and left_kind in {"str", "bytes"} and isinstance(node.op, ast.Add):
            return _binary_node("+", left, right, {"kind": left_kind}, span)
        if left_kind == "class" or right_kind == "class":
            return self._class_binary(node, left, right, span)
        joined = _const_list_concat(left, right, span)
        if joined is not None and isinstance(node.op, ast.Add):
            return joined
        if (
            isinstance(node.op, ast.Add)
            and left_kind == "list"
            and right_kind == "list"
            and not left["type"].get("set")
            and not right["type"].get("set")
            and types_equal(left["type"]["elem"], right["type"]["elem"])
        ):
            # `xs + ys` builds a fresh list; `xs += ys` reaches the same node
            # through the aug-assign rewrite, which makes += copy, not mutate.
            return self._abi_call(
                "builtins",
                "concat",
                [left, right],
                {"kind": "list", "elem": left["type"]["elem"]},
                "ps_seq_concat",
                span,
            )
        if isinstance(node.op, ast.Mult):
            if left_kind == "list" and right_kind == "int" and not left["type"].get("set"):
                return self._abi_call("builtins", "repeat", [left, right], left["type"], "ps_seq_repeat", span)
            if left_kind == "int" and right_kind == "list" and not right["type"].get("set"):
                return self._abi_call("builtins", "repeat", [right, left], right["type"], "ps_seq_repeat", span)
            for seq, count, seq_kind in ((left, right, left_kind), (right, left, right_kind)):
                if seq_kind in {"str", "bytes"} and count["type"]["kind"] == "int":
                    return self._abi_call("builtins", "str_repeat", [seq, count], seq["type"], "ps_str_repeat", span)
        self.fail(node, "PS2004", f"{_op_text(node.op)} on {left_kind} and {right_kind} is not lowered")
        raise AssertionError("unreachable")

    def _pow(
        self, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        """`base ** exp` is a loop of the existing int `*`, or a float `pow`.

        Both operands ints: the base and the exponent are stored once, then
        the accumulator starts at 1 and is multiplied `exp` times. `0 ** 0` is
        that 1. A float operand lifts the pair to `llvm.pow.f64`, and so does
        a constant negative exponent — `2 ** -1` is `0.5`, like CPython. A
        computed negative stays the old ValueError raise, since the result
        type is pinned int here and cannot become a float per operand.
        """
        left_kind = left["type"]["kind"]
        right_kind = right["type"]["kind"]
        if left_kind not in _NUMERIC or right_kind not in _NUMERIC:
            self.fail(node, "PS2004", "** is only lowered for int")
        float_t = {"kind": "float"}
        if left_kind == "float" or right_kind == "float" or (_static_int(right) or 0) < 0:
            if left_kind == "int":
                left = _cast_node("int_to_float", left, float_t, span)
            if right_kind == "int":
                right = _cast_node("int_to_float", right, float_t, span)
            return _binary_node("**", left, right, float_t, span)
        int_t = {"kind": "int"}
        base = self._temp("powb")
        exp = self._temp("powe")
        acc = self._temp("powa")
        idx = self._temp("powi")
        for name in (base, exp, acc, idx):
            self.locals[name] = int_t
        base_n = _name(base, int_t, span)
        exp_n = _name(exp, int_t, span)
        acc_n = _name(acc, int_t, span)
        idx_n = _name(idx, int_t, span)
        self.prelude.extend(
            [
                _assign(base, left, span),
                _assign(exp, right, span),
                _if_node(
                    _compare_node("<", exp_n, _const_int(0, span), span),
                    [_raise_node("ValueError", span)],
                    [],
                    span,
                ),
                _assign(acc, _const_int(1, span), span),
                _assign(idx, _const_int(0, span), span),
                _while_node(
                    _compare_node("<", idx_n, exp_n, span),
                    [
                        _assign(acc, _binary_int("*", acc_n, base_n, span), span),
                        _assign(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
                    ],
                    span,
                ),
            ]
        )
        return _name(acc, int_t, span)

    def _list(self, node: ast.List, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            if expect is None or expect["kind"] != "list":
                self.fail(node, "PS1001", "an empty list needs a list annotation")
            return _list_node([], expect, span)
        elem_expect = expect["elem"] if expect and expect["kind"] == "list" else None
        elems = [self._expr(elt, elem_expect) for elt in node.elts]
        elem = elems[0]["type"]
        for item in elems[1:]:
            if not types_equal(item["type"], elem):
                self.fail(node, "PS1003", "list elements have one type")
        typ = {"kind": "list", "elem": elem}
        if expect and expect["kind"] == "list":
            if _any_contains(expect) and not _any_contains(typ):
                # Issue #781: a concrete display satisfies an Any container
                # annotation; the display's element type is the answer.
                return _list_node(elems, typ, span)
            self._expect(node, typ, expect, "list")
        return _list_node(elems, typ, span)

    def _dict(self, node: ast.Dict, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.keys or any(key is None for key in node.keys):
            if expect is None or expect["kind"] != "dict":
                self.fail(node, "PS1001", "an empty dict needs a dict annotation")
            if any(key is None for key in node.keys):
                self.fail(node, "PS2002", "dict unpacking is not lowered")
            return _dict_node([], expect, span)
        key_expect = expect["key"] if expect and expect["kind"] == "dict" else None
        val_expect = expect["value"] if expect and expect["kind"] == "dict" else None
        pairs = []
        for key_node, val_node in zip(node.keys, node.values, strict=True):
            assert key_node is not None
            key = self._expr(key_node, key_expect)
            value = self._expr(val_node, val_expect)
            pairs.append({"key": key, "value": value})
        key_t = pairs[0]["key"]["type"]
        val_t = pairs[0]["value"]["type"]
        if key_t["kind"] not in {"int", "str"}:
            self.fail(node, "PS2002", "dict keys are int or str")
        if val_t["kind"] == "bool":
            self.fail(node, "PS2002", "dict values of bool are not lowered")
        for pair in pairs[1:]:
            if not types_equal(pair["key"]["type"], key_t) or not types_equal(pair["value"]["type"], val_t):
                self.fail(node, "PS1003", "dict entries have one key type and one value type")
        typ = {"kind": "dict", "key": key_t, "value": val_t}
        if expect and expect["kind"] == "dict":
            if _any_contains(expect) and not _any_contains(typ):
                # Issue #781: a concrete display satisfies an Any container
                # annotation; the display's type is the answer, and the
                # annotation's shape rode down as the element expectation.
                return _dict_node(pairs, typ, span)
            self._expect(node, typ, expect, "dict")
        return _dict_node(pairs, typ, span)

    def _subscript(
        self, node: ast.Subscript, span: dict[str, Any], expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if isinstance(node.slice, ast.Slice):
            return self._slice(node, span)
        if self._is_environ(node.value):
            index = self._expr(node.slice)
            if index["type"]["kind"] != "str":
                self.fail(node, "PS1003", "an os.environ key is str")
            return self._abi_call("os", "environ.getitem", [index], {"kind": "str"}, "ps_os_getenv", span)
        value = self._expr(node.value)
        index = self._expr(node.slice)
        kind = value["type"]["kind"]
        # boxed:begin
        if value["type"].get("island"):
            return self._island_getitem(node, value, index, span, expect)
        # boxed:end
        if kind == "class" and self.classes.get(value["type"]["name"], {}).get("tuple"):
            return self._tuple_subscript(node, value, span)
        if kind == "list" and value["type"].get("set"):
            self.fail(node, "PS2002", "a set has no subscript")
        if kind == "list":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a list index is int")
            result = self._resolve_any_read(node, value["type"]["elem"], expect)
        elif kind == "dict":
            self._expect(node, index["type"], value["type"]["key"], "dict key")
            result = self._resolve_any_read(node, value["type"]["value"], expect)
            if value["type"].get("counter") or value["type"].get("defaultdict"):
                return self._defaulted_subscript(node, value, index, span)
        elif kind == "str":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a str index is int")
            result = {"kind": "str"}
        elif kind == "bytes":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a bytes index is int")
            result = {"kind": "int"}
        elif kind == "class" and value["type"].get("name") == "Deque":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a deque index is int")
            if "elem" not in value["type"]:
                self.fail(node, "PS2002", "this deque has no element type")
            result = value["type"]["elem"]
        else:
            self.fail(node, "PS2002", "subscript is lowered for list, dict, str, and bytes")
        return _subscript_node(value, index, result, span)

    def _resolve_any_read(self, node: ast.expr, elem: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        """The value type of a read out of an Any container (issue #781).

        The container's shape is known, the element type is not: the `any`
        slot has to be proven at the read site from a sound local source.
        Two proofs are accepted: the read expression itself expects a type
        (the assignment target's annotation, a call's parameter table, the
        comparison operand), or the checker bridge's answer for the read
        expression. A read nothing proves fences with PS1107, the code that
        names the unresolved read, instead of guessing.
        """
        if elem["kind"] != "any":
            return elem
        if expect is not None and expect["kind"] != "any":
            self._any_proven[node.lineno, node.col_offset] = expect
            return expect
        proven = self._bridge_type(node)
        if proven is not None:
            self._any_proven[node.lineno, node.col_offset] = proven
            return proven
        self.fail(
            node,
            "PS1107",
            f"the element type of this read is unresolved ({_show(elem)} container); "
            "annotate the read site or use a concretely typed container",
        )
        raise AssertionError("unreachable")

    def _defaulted_subscript(
        self, node: ast.Subscript, value: dict[str, Any], index: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        """`c[k]` on a Counter or defaultdict: the missing-key read CPython performs.

        A Counter read yields 0 without inserting. A defaultdict read inserts a
        fresh default (the zero value of the annotation's value type) then
        returns it. Both pin the dict and key to prelude temps and guard the
        raw subscript with `k in d`, like `dict.setdefault`.
        """
        dtyp = value["type"]
        key_t, val_t = dtyp["key"], dtyp["value"]
        d_t = self._temp("dfd").replace(".", "_")
        k_t = self._temp("dfk").replace(".", "_")
        v_t = self._temp("dfv").replace(".", "_")
        self.locals[d_t] = dtyp
        self.locals[k_t] = key_t
        self.locals[v_t] = val_t
        for t, v in ((d_t, value), (k_t, index)):
            self.prelude.append(_assign_node(t, v, span))
        getitem = _subscript_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), val_t, span)
        then = [_assign_node(v_t, getitem, span)]
        if dtyp.get("counter"):
            other = [_assign_node(v_t, _zero(val_t, span), span)]
        else:
            other = [
                _setitem_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), _zero(val_t, span), span),
                _assign_node(v_t, getitem, span),
            ]
        cond = _contains_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), span, negated=False)
        self.prelude.append(_if_node(cond, then, other, span))
        return _name(v_t, val_t, span)

    def _attribute(
        self, node: ast.Attribute, span: dict[str, Any], expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if (
            isinstance(node.value, ast.Call)
            and node.attr == "name"
            and self._is_mod(node.value, "threading", "current_thread")
        ):
            return self._abi_call("threading", "current_thread.name", [], {"kind": "str"}, "ps_thread_name", span)
        resolved = self._resolve_module(node)
        if resolved is not None:
            dotted, module, member, status = resolved
            if status == "erased":
                note = typing_note(member)
                if note is not None:
                    self.fail(node, "PS2002", note)
                self.fail(node, "PS2002", f"{dotted} is a typing name and is not a value")
            if status == "pkg":
                item = self._pkg_item(node, module, member)
                if item["kind"] == "const":
                    value = item["value"]
                    if value.get("kind") == "tuple-const":
                        return self._materialize_tuple(value["elems"], span)
                    return {**value, "span": span}
                if item["kind"] == "class":
                    self.fail(node, "PS2002", f"class {dotted} is not a value")
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            if status == "queued":
                self._queued(node, dotted)
            if status == "value":
                self.fail(node, "PS2002", f"module {dotted} is not a value in the static subset")
            if module == "math" and member in MATH_CONSTANTS:
                return _const_of(MATH_CONSTANTS[member], "float", span)
            constant = module_constant(module, member)
            if constant is not None:
                value, typ = constant
                return _const_node(value, typ, span)
            if module == "sys" and member == "argv":
                return self._abi_call("sys", "argv", [], {"kind": "list", "elem": {"kind": "str"}}, "ps_argv_get", span)
            class_const = class_constant(module, member)
            if class_const is not None:
                typ, symbol = class_const
                return self._abi_call(module, member, [], type_to_dict(typ), symbol, span)
            if (module == "math" and member in MATH_FUNCTIONS) or wave_function(module, member) is not None:
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            if member in SPECIAL_FUNCTIONS.get(module, frozenset()) or member_callable(module, member):
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            manifest = self._ffi_module(module)
            if manifest is not None:
                found_const = manifest.const(member)
                if found_const is not None:
                    return _const_node(
                        found_const.value,
                        type_ir(found_const.type, manifest.handle_names),
                        span,
                    )
                if manifest.handle(member) is not None:
                    # A handle name is a type, not a value (issue #224).
                    self.fail(node, "PS4014", f"{dotted} is a handle type, not a value")
                if manifest.function(member) is not None:
                    self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
                self.fail(node, "PS2001", f"{dotted} is not in the FFI manifest of {module}")
            note = inspect_note(member) if module == "inspect" else None
            if note is not None:
                self.fail(node, "PS2001", note)
            self.fail(node, "PS2001", f"{dotted} is not in the lowered subset of {module}")
        class_name = self.class_aliases.get(node.value.id, node.value.id) if isinstance(node.value, ast.Name) else None
        if class_name is not None and class_name in self.classes:
            info = self.classes[class_name]
            if info.get("enum"):
                member = info["members"].get(node.attr)
                if member is None:
                    self.fail(node, "PS2001", f"{node.value.id}.{node.attr} is not an enum member")
                return _const_of(str(member), "int", span)
        value = self._expr(node.value)
        return path_attribute(self, value, node.attr, self._lookup_attr(value, node.attr, span, node, expect), span)

    def _call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(node.func, ast.Name):
            return self._call_name(node, span, expect)
        if isinstance(node.func, ast.Attribute):
            return self._call_attr(node, span, expect)
        target = self._expr(node.func)
        if target["type"]["kind"] == "func":
            # `d["f"](x)`, `make()(x)`: an indirect call through a callable
            # cell. Callable annotations carry bare types, no names.
            params = [{"name": f"_{index}", "type": typ} for index, typ in enumerate(target["type"]["params"])]
            args = self._fill_args(node, params, "call")
            return _value_call(target, args, target["type"]["return"], span)
        self.fail(node, "PS2002", "this call is not lowered")
        raise AssertionError("unreachable")

    def _truthy(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        if kind == "bool":
            return expr
        if expr["type"].get("optional"):
            return self._opt_truthy(expr, span)
        if kind == "int":
            return _compare("!=", expr, _const_int(0, span), span)
        if kind == "float":
            zero = _const_of("0.0", "float", span)
            return _compare("!=", expr, zero, span)
        if kind in {"str", "bytes", "list", "dict"}:
            length = _builtin_call("len", [expr], {"kind": "int"}, span)
            return _compare("!=", length, _const_int(0, span), span)
        self.fail(ast.Pass(), "PS2004", f"and/or on {kind} is not lowered")
        raise AssertionError("unreachable")

    def _mixed_cond_kind(self, kind: str) -> bool:
        return kind == "bool" or kind in _TRUTHY

    def _bool_operand(self, node: ast.expr, as_cond: bool) -> dict[str, Any]:
        if as_cond and isinstance(node, ast.BoolOp):
            return self._boolop(node, span_of(node, self.filename), as_cond=True)
        return self._expr(node)

    def _boolop(self, node: ast.BoolOp, span: dict[str, Any], *, as_cond: bool = False) -> dict[str, Any]:
        """Same-type `and`/`or` still yields the operand.

        A mixed `and`/`or` lowers as the truthiness of each side joined by bool
        `and`/`or`, so the result is always a bool. That diverges from CPython,
        where `a or b` returns the deciding operand with its own type; the IR
        has no tagged union for `T_a | T_b` (epic E3c). The right side is
        evaluated only when the left does not decide it.
        """
        op_and = isinstance(node.op, ast.And)
        first = self._bool_operand(node.values[0], as_cond)
        typ = first["type"]
        if typ.get("optional"):
            if len(node.values) != 2:
                self.fail(node, "PS2004", "and/or on an optional joins two values")
            return self._opt_boolop(node, first, op_and, span)
        if typ["kind"] not in _TRUTHY | {"bool", "float"}:
            self.fail(node, "PS2004", f"and/or on {typ['kind']} is not lowered")
        temp = self._temp("boolop")
        self.locals[temp] = typ
        self.prelude.append(_assign_node(temp, first, span))
        proven: set[str] = set()
        for index, value_node in enumerate(node.values[1:], start=1):
            when_true, when_false = self._narrow_names(node.values[index - 1])
            proven |= when_true if op_and else when_false
            saved = self.prelude
            self.prelude = []
            right = self._narrowed(proven, node, lambda value_node=value_node: self._bool_operand(value_node, as_cond))
            inner = self.prelude
            self.prelude = saved
            if not types_equal(right["type"], typ):
                left_ok = self._mixed_cond_kind(typ["kind"])
                right_ok = self._mixed_cond_kind(right["type"]["kind"])
                if not left_ok or not right_ok:
                    self.fail(node, "PS2004", "and/or across two types is not lowered")
                if typ["kind"] != "bool":
                    bool_temp = self._temp("cond")
                    self.locals[bool_temp] = {"kind": "bool"}
                    self.prelude.append(_assign_node(bool_temp, self._truthy(_name(temp, typ, span), span), span))
                    temp = bool_temp
                    typ = {"kind": "bool"}
                inner.append(_assign_node(temp, self._truthy(right, span), span))
            else:
                inner.append(_assign_node(temp, right, span))
            cond = self._truthy(_name(temp, typ, span), span)
            if not op_and:
                cond = _unary_node("not", cond, {"kind": "bool"}, span)
            self.prelude.append(_if_node(cond, inner, [], span))
        return _name(temp, typ, span)

    def _ifexp(self, node: ast.IfExp, span: dict[str, Any]) -> dict[str, Any]:
        """`a if cond else b` is the if statement BoolOp already uses.

        The condition is whatever `if` already accepts. Both operands lower to
        one type. Each operand's statements sit in its own branch, so the
        untaken call does not run. A nested conditional is the same desugar.
        """
        cond = self._bool_cond(node.test)
        saved = self.prelude
        self.prelude = []
        when_true, when_false = self._narrow_names(node.test)
        try:
            body = self._narrowed(when_true, node, lambda: self._expr(node.body))
            body_pre = self.prelude
            self.prelude = []
            orelse = self._narrowed(when_false, node, lambda: self._expr(node.orelse))
            orelse_pre = self.prelude
        finally:
            self.prelude = saved
        if not types_equal(body["type"], orelse["type"]):
            # `x if c else None` (or a bare T against an optional) unifies on
            # the optional: the other arm is boxed into the same tagged cell.
            opt_typ = None
            if body["type"].get("optional"):
                opt_typ = body["type"]
            elif orelse["type"].get("optional"):
                opt_typ = orelse["type"]
            elif body["type"]["kind"] == "none" and orelse["type"]["kind"] != "none":
                opt_typ = self._opt_type(orelse["type"], node)
            elif orelse["type"]["kind"] == "none" and body["type"]["kind"] != "none":
                opt_typ = self._opt_type(body["type"], node)
            coerced = False
            if opt_typ is not None:
                coerced = True
                for which in ("body", "orelse"):
                    side = body if which == "body" else orelse
                    if types_equal(side["type"], opt_typ):
                        continue
                    if side["type"]["kind"] == "none":
                        raw = None
                    elif types_equal(side["type"], opt_typ["elem"]):
                        raw = side
                    else:
                        coerced = False
                        break
                    saved = self.prelude
                    self.prelude = body_pre if which == "body" else orelse_pre
                    boxed = self._opt_box(raw, opt_typ, span)
                    pre = self.prelude
                    self.prelude = saved
                    if which == "body":
                        body, body_pre = boxed, pre
                    else:
                        orelse, orelse_pre = boxed, pre
            if not coerced:
                self.fail(node, "PS2004", "conditional expression across two types is not lowered")
        typ = body["type"]
        then_body = self._ifexp_branch(body_pre, body, span)
        else_body = self._ifexp_branch(orelse_pre, orelse, span)
        if typ["kind"] == "none":
            # None is not a register. The if still runs the taken operand.
            self.prelude.append(_if_node(cond, then_body, else_body, span))
            return _const_none(span)
        temp = self._temp("ifexp")
        self.locals[temp] = typ
        then_body.append(_assign_node(temp, body, span))
        else_body.append(_assign_node(temp, orelse, span))
        self.prelude.append(_if_node(cond, then_body, else_body, span))
        return _name(temp, typ, span)

    def _ifexp_branch(
        self, prelude: list[dict[str, Any]], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if value["type"]["kind"] != "none":
            return list(prelude)
        body = list(prelude)
        if value["kind"] != "const":
            body.append(_expr_stmt_node(value, span))
        return body

    def _opt_boolop(
        self, node: ast.BoolOp, first: dict[str, Any], op_and: bool, span: dict[str, Any]
    ) -> dict[str, Any]:
        """`x and y` / `x or y` on a tagged optional.

        `x or y` with `y: T` unwraps to `T` (the value or the default), matching
        CPython. `x and y` and `x or <optional>` keep the tagged type: the
        deciding operand is already a tagged cell.
        """
        o_typ = first["type"]
        elem = o_typ["elem"]
        bool_t = {"kind": "bool"}
        o = self._temp("opt")
        self.locals[o] = o_typ
        self.prelude.append(_assign_node(o, first, span))
        oname = _name(o, o_typ, span)
        cond = self._opt_truthy(oname, span)
        saved = self.prelude
        self.prelude = []
        right = self._bool_operand(node.values[1], as_cond=False)
        right_pre = self.prelude
        self.prelude = saved
        if right["type"].get("optional") or right["type"]["kind"] == "none":
            if right["type"].get("optional") and not types_equal(right["type"], o_typ):
                self.fail(node, "PS2004", "and/or across two types is not lowered")
            if right["type"]["kind"] == "none":
                saved = self.prelude
                self.prelude = right_pre
                right = self._opt_box(None, o_typ, span)
                right_pre = self.prelude
                self.prelude = saved
            res = self._temp("opt")
            self.locals[res] = o_typ
            self.prelude.append(_assign_node(res, oname, span))
            if not op_and:
                cond = _unary_node("not", cond, bool_t, span)
            self.prelude.append(_if_node(cond, [*right_pre, _assign_node(res, right, span)], [], span))
            return _name(res, o_typ, span)
        if not types_equal(right["type"], elem):
            self.fail(node, "PS2004", "and/or across two types is not lowered")
        res = self._temp("opt")
        if op_and:
            saved = self.prelude
            self.prelude = right_pre
            boxed = self._opt_box(right, o_typ, span)
            then_body = self.prelude
            self.prelude = saved
            self.locals[res] = o_typ
            self.prelude.append(_assign_node(res, oname, span))
            self.prelude.append(_if_node(cond, [*then_body, _assign_node(res, boxed, span)], [], span))
            return _name(res, o_typ, span)
        self.locals[res] = elem
        val = _attr(oname, "_val", elem, span)
        self.prelude.append(
            _if_node(cond, [_assign_node(res, val, span)], [*right_pre, _assign_node(res, right, span)], span)
        )
        return _name(res, elem, span)

    def _joined(self, node: ast.JoinedStr, span: dict[str, Any]) -> dict[str, Any]:
        if any(
            isinstance(value, ast.FormattedValue) and (value.conversion != -1 or value.format_spec is not None)
            for value in node.values
        ):
            return self._joined_formatted(node, span)
        parts: list[dict[str, Any]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(self._const(value, span_of(value, self.filename)))
                continue
            if isinstance(value, ast.FormattedValue):
                parts.append(self._as_str(self._expr(value.value), value))
                continue
            self.fail(value, "PS2002", "f-strings are not lowered")
        if self.function is None:
            folded = _const_str_join(parts, span)
            if folded is not None:
                return folded
        return _concat_str(parts, span)

    def _joined_formatted(self, node: ast.JoinedStr, span: dict[str, Any]) -> dict[str, Any]:
        # R11: rewrite the f-string as one `str.format`-grammar template plus
        # positional args, rendered by the R16 engine (`ps_str_format`, "dot"
        # style). Literal braces in the constants are re-escaped for the
        # runtime parse; `{x=}` debug fields keep their `x=` literal and arrive
        # as conversion 'r'.
        template: list[str] = []
        args: list[dict[str, Any]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                template.append(value.value.replace("{", "{{").replace("}", "}}"))
                continue
            if isinstance(value, ast.FormattedValue):
                template.append(self._fstring_field(value, args))
                continue
            self.fail(value, "PS2002", "f-strings are not lowered")
        fmt = _const_of("".join(template), "str", span)
        expr = self._abi_call("builtins", "format", [fmt, *args], {"kind": "str"}, "ps_str_format", span)
        expr["format_style"] = "dot"
        return expr

    def _fstring_field(self, value: ast.FormattedValue, args: list[dict[str, Any]]) -> str:
        arg = self._expr(value.value)
        converted = False
        if arg["type"]["kind"] == "class" and value.conversion != -1:
            conv = chr(value.conversion)
            if conv not in "sra":
                self.fail(value, "PS2002", "f-string conversion is not lowered")
            if arg["type"].get("name") == "Path":
                if conv == "s":
                    args.append(_builtin_call("str", [arg], {"kind": "str"}, arg["span"]))
                    converted = True
                else:
                    self.fail(value, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
            else:
                # `{p!r}`/`{p!s}` stringify eagerly: the format engine cannot spell
                # a class, and `!r` on the spelled str would double-quote it.
                called = self._dunder_call(value, arg, "__repr__" if conv in "ra" else "__str__", [], arg["span"])
                if called is None:
                    self.fail(value, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
                args.append(called)
                converted = True
        elif arg["type"]["kind"] == "exc" and value.conversion != -1:
            conv = chr(value.conversion)
            if conv not in "sra":
                self.fail(value, "PS2002", "f-string conversion is not lowered")
            if conv == "s":
                args.append(_builtin_call("str", [arg], {"kind": "str"}, arg["span"]))
                converted = True
            else:
                self.fail(value, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
        if not converted:
            if (
                arg["type"]["kind"] == "exc" or (arg["type"]["kind"] == "class" and arg["type"].get("name") == "Path")
            ) and value.format_spec is not None:
                self.fail(
                    value.format_spec, "PS2004", f"f-string format spec is not supported for {_show(arg['type'])}"
                )
            args.append(self._fstring_arg(arg, value))
        field = ["{", str(len(args) - 1)]
        if value.conversion != -1 and not converted:
            conv = chr(value.conversion)
            if conv not in "sra":
                self.fail(value, "PS2002", "f-string conversion is not lowered")
            field.append("!" + conv)
        if value.format_spec is not None:
            field.append(":")
            field.append(self._fstring_spec(value.format_spec, args))
        field.append("}")
        return "".join(field)

    def _fstring_spec(self, spec: ast.expr, args: list[dict[str, Any]]) -> str:
        parts = spec.values if isinstance(spec, ast.JoinedStr) else [spec]
        out: list[str] = []
        for part in parts:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
                continue
            if isinstance(part, ast.FormattedValue) and part.conversion == -1 and part.format_spec is None:
                args.append(self._fstring_arg(self._expr(part.value), part))
                out.append("{" + str(len(args) - 1) + "}")
                continue
            self.fail(spec, "PS2002", "nested f-string format fields are not lowered")
        return "".join(out)

    def _fstring_arg(self, arg: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        kind = arg["type"]["kind"]
        # boxed:begin
        # An island call in a placeholder (issue #733): the trampoline can
        # return the str the island produced directly, so the `want` rides
        # on the same call instead of the opaque handle.
        if arg["type"].get("island") and isinstance(arg.get("island_fn"), dict):
            want = {"kind": "str"}
            self._island_validate_expect(node, want)
            arg["type"] = want
            arg["island_fn"]["want"] = want
            return arg
        # boxed:end
        if kind == "dict":
            typ = arg["type"]
            if typ["key"]["kind"] not in {"int", "str"} or typ["value"]["kind"] not in {
                "int",
                "float",
                "str",
                "bytes",
            }:
                self.fail(node, "PS2002", "f-string formatting needs scalar dict keys and values")
        elif kind == "class":
            if arg["type"].get("name") == "Path":
                return _builtin_call("str", [arg], {"kind": "str"}, arg["span"])
            called = self._dunder_call(node, arg, "__str__", [], arg["span"])
            if called is None:
                called = self._dunder_call(node, arg, "__repr__", [], arg["span"])
            if called is None:
                self.fail(node, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
            return called
        elif kind == "exc":
            return _builtin_call("str", [arg], {"kind": "str"}, arg["span"])
        elif kind not in {"int", "float", "str", "bool", "bytes"}:
            self.fail(node, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
        return arg

    def _as_str(self, expr: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        span = expr["span"]
        if kind == "str":
            return expr
        if kind in {"int", "float"}:
            return _builtin_call("str", [expr], {"kind": "str"}, span)
        if kind == "class":
            if expr["type"].get("name") == "Path":
                return _builtin_call("str", [expr], {"kind": "str"}, span)
            called = self._dunder_call(node, expr, "__str__", [], span)
            if called is None:
                called = self._dunder_call(node, expr, "__repr__", [], span)
            if called is not None:
                return called
        if kind == "exc":
            return _builtin_call("str", [expr], {"kind": "str"}, span)
        # boxed:begin
        # An island call as the whole placeholder, `{digest(p)}` with no
        # conversion or spec (issue #733): same want-on-the-call rewrite as
        # `_fstring_arg`.
        if expr["type"].get("island") and isinstance(expr.get("island_fn"), dict):
            want = {"kind": "str"}
            self._island_validate_expect(node, want)
            expr["type"] = want
            expr["island_fn"]["want"] = want
            return expr
        # boxed:end
        self.fail(node, "PS2002", f"formatting {_show(expr['type'])} is not lowered")
        raise AssertionError("unreachable")

    def _percent(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        fmt = self._expr(node.left)
        if fmt["type"]["kind"] != "str":
            self.fail(node, "PS2004", "% formatting needs a str left operand")
        elts = list(node.right.elts) if isinstance(node.right, ast.Tuple) else [node.right]
        args = [fmt]
        style = "percent"
        for elt in elts:
            arg = self._expr(elt)
            kind = arg["type"]["kind"]
            if kind == "dict":
                t = arg["type"]
                if t["key"]["kind"] not in {"int", "str"} or t["value"]["kind"] not in {"int", "float", "str", "bytes"}:
                    self.fail(node, "PS2002", "% mapping needs scalar dict keys and values")
                if not isinstance(node.right, ast.Tuple):
                    style = "map"
            elif kind not in {"int", "float", "str", "bool", "bytes"}:
                self.fail(node, "PS2004", f"% formatting of {_show(arg['type'])} is not lowered")
            args.append(arg)
        expr = self._abi_call("builtins", "format", args, {"kind": "str"}, "ps_str_format", span)
        expr["format_style"] = style
        return expr

    def _slice(self, node: ast.Subscript, span: dict[str, Any]) -> dict[str, Any]:
        slc = node.slice
        assert isinstance(slc, ast.Slice)
        value = self._expr(node.value)
        kind = value["type"]["kind"]
        if kind == "list" and value["type"].get("set"):
            self.fail(node, "PS2002", "a set has no subscript")
        if kind not in {"str", "bytes", "list"}:
            self.fail(node, "PS2002", "slices are lowered for str, bytes, and list")
        start, has_start = self._slice_bound(slc.lower, span)
        stop, has_stop = self._slice_bound(slc.upper, span)
        step = self._slice_step(slc.step, span)
        start = self._pin_slice_int(start, span)
        stop = self._pin_slice_int(stop, span)
        step = self._pin_slice_int(step, span)
        symbol = "ps_seq_slice" if kind == "list" else "ps_str_slice"
        result = value["type"] if kind != "list" else value["type"]
        if kind == "str":
            result = {"kind": "str"}
        elif kind == "bytes":
            result = {"kind": "bytes"}
        return self._abi_call(
            "builtins",
            "slice",
            [value, start, stop, _const_int(has_start, span), _const_int(has_stop, span), step],
            result,
            symbol,
            span,
        )

    def _slice_bound(self, node: ast.expr | None, span: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if node is None:
            return _const_int(0, span), 0
        expr = self._expr(node, {"kind": "int"})
        if expr["type"]["kind"] != "int":
            self.fail(node, "PS1003", "a slice bound is int")
        return expr, 1

    def _slice_step(self, node: ast.expr | None, span: dict[str, Any]) -> dict[str, Any]:
        """Any nonzero step lowers; the runtime walks backwards for step < 0.

        Step 0 is the runtime's ValueError. The step expression is one operand,
        so it runs once.
        """
        if node is None:
            return _const_int(1, span)
        expr = self._expr(node, {"kind": "int"})
        if expr["type"]["kind"] != "int":
            self.fail(node, "PS1003", "a slice step is int")
        return expr

    def _pin_slice_int(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Store a slice operand once. A module call emits its arguments twice."""
        if expr["kind"] in {"const", "name"}:
            return expr
        temp = self._temp("sl")
        self.locals[temp] = {"kind": "int"}
        self.prelude.append(_assign_node(temp, expr, span))
        return _name(temp, {"kind": "int"}, span)
