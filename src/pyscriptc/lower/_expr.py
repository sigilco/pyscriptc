"""Expression lowering for the lowerer.

Split out of `__init__.py` as E0 move 2. `Lowerer` composes this mixin in.
The area owns the `_expr` dispatch and its expression-form methods: names,
constants, unary/binary/compare (including chains and membership), list and
dict displays, subscripts and slices, attributes, calls, `and`/`or`,
conditionals, f-strings and `%` formatting.

Tables shared with the statement, call, and typing paths (`_CMP`, `_SCALAR`,
`_TRUTHY`, `_LIST_FORMS`, `_DICT_FORMS`, `_SET_FORMS`) live in `_core.py`;
`_INT_BIN`/`_FLOAT_BIN` are expression-only and stay here. The boundary calls
in both directions stay `self._method()` so the move is behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.errors import span_of
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _CMP,
    _SCALAR,
    _TRUTHY,
    _assign,
    _attr,
    _binary_int,
    _compare,
    _concat_str,
    _const_bool,
    _const_int,
    _const_list_concat,
    _const_none,
    _const_str_join,
    _len_call,
    _name,
    _node_text,
    _op_text,
    _plain_const_kind,
    _show,
    _static_int,
    _zero,
)
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    wave_function,
)
from pyscriptc.stdmore import COMPARE_CLASSES, member_callable, module_constant

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
_NUMERIC = {"int", "float"}


class ExprMixin:
    def _expr(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        value = self._expr_any(node, expect)
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
        made = {
            "kind": "call",
            "callee": {"kind": "class", "name": typ["name"]},
            "args": [],
            "type": typ,
            "span": span,
        }
        self.prelude.append(_assign(dest, made, span))
        box = _name(dest, typ, span)
        if value is not None:
            self.prelude.append(
                {
                    "kind": "setattr",
                    "object": box,
                    "name": "_tag",
                    "value": _const_bool(True, span),
                    "span": span,
                }
            )
            self.prelude.append(
                {
                    "kind": "setattr",
                    "object": _name(dest, typ, span),
                    "name": "_val",
                    "value": value,
                    "span": span,
                }
            )
        return box

    def _opt_truthy(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Truthiness of a tagged optional: tagged and its value truthy.

        Heap elements are truthy whenever they exist, so the tag alone decides.
        """
        typ = expr["type"]
        tag = _attr(expr, "_tag", {"kind": "bool"}, span)
        if typ["elem"]["kind"] in {"class", "exc"}:
            return tag
        val = _attr(expr, "_val", typ["elem"], span)
        res = self._temp("optcond")
        self.locals[res] = {"kind": "bool"}
        self.prelude.append(_assign(res, tag, span))
        retag = _attr(expr, "_tag", {"kind": "bool"}, span)
        inner = self._truthy(val, span)
        self.prelude.append(
            {"kind": "if", "cond": retag, "then": [_assign(res, inner, span)], "else": [], "span": span}
        )
        return _name(res, {"kind": "bool"}, span)

    def _expr_any(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        span = span_of(node, self.filename)
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
            return self._attribute(node, span)
        if isinstance(node, ast.List):
            return self._list(node, span, expect)
        if isinstance(node, ast.Set):
            return self._set_display(node, span, expect)
        if isinstance(node, ast.Dict):
            return self._dict(node, span, expect)
        if isinstance(node, ast.Tuple):
            return self._tuple_expr(node, span)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, span)
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
        elif node.id in self.locals:
            narrowed = self.opt_unwrap.get(node.id)
            if narrowed is not None:
                # Inside `if x is not None:` a read of x is its tagged value.
                return _attr(_name(node.id, narrowed, span), "_val", narrowed["elem"], span)
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
            return {"kind": "const", "value": self.module_name or "__main__", "type": {"kind": "str"}, "span": span}
        if node.id in self.type_aliases:
            self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
        if node.id in self.typevars:
            self.fail(node, "PS2002", f"{node.id} is a TypeVar and is not a value")
        binding = self.bindings.get(node.id)
        if binding is not None:
            if binding.queued:
                self._queued(node, f"{binding.module}.{binding.attr}" if binding.attr else binding.module)
            if binding.kind == "erased":
                self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
            if binding.kind == "const":
                if binding.module == "math":
                    value: Any = MATH_CONSTANTS[binding.attr]
                    typ: dict[str, Any] = {"kind": "float"}
                else:
                    found = module_constant(binding.module, binding.attr)
                    if found is None:
                        self.fail(node, "PS2001", f"{binding.module}.{binding.attr} is not a constant")
                    value, typ = found
                return {"kind": "const", "value": value, "type": typ, "span": span}
            if binding.kind in {"module", "pkg"}:
                self.fail(node, "PS2002", f"module {node.id} is not a value in the static subset")
            self.fail(node, "PS2002", f"{node.id} is a function and is not a value")
        if node.id in self.classes:
            self.fail(node, "PS2002", f"class {node.id} is not a value")
        if node.id in self.sigs:
            if node.id in self.generic_fns:
                self.fail(node, "PS2002", f"{node.id} is generic and is not a value")
            # A function referenced as a value: the closure wraps it with an
            # empty environment (the emitter adds a lazy env-first shim).
            params, ret = self.sigs[node.id]
            ftype = {"kind": "func", "params": [param["type"] for param in params], "return": ret}
            return {
                "kind": "closure",
                "name": self._ir_name(node.id),
                "env": [],
                "type": ftype,
                "span": span,
            }
        if node.id in EXCEPTIONS:
            self.fail(node, "PS2002", f"{node.id} is only lowered in raise and except")
        self.fail(node, "PS2002", f"{node.id} is not a local or parameter")
        raise AssertionError("unreachable")

    def _const(self, node: ast.Constant, span: dict[str, Any]) -> dict[str, Any]:
        value = node.value
        if isinstance(value, bool):
            return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}
        if isinstance(value, int):
            return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}
        if isinstance(value, float):
            return {"kind": "const", "value": repr(value), "type": {"kind": "float"}, "span": span}
        if isinstance(value, str):
            if any(ord(ch) > 255 for ch in value):
                self.fail(node, "PS2005", "string literals outside Latin-1 are not lowered")
            return {"kind": "const", "value": value, "type": {"kind": "str"}, "span": span}
        if isinstance(value, bytes):
            return {"kind": "const", "value": value.decode("latin1"), "type": {"kind": "bytes"}, "span": span}
        if value is None:
            return _const_none(span)
        self.fail(node, "PS2002", f"literal {type(value).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _unary(self, node: ast.UnaryOp, span: dict[str, Any]) -> dict[str, Any]:
        operand = self._expr(node.operand)
        kind = operand["type"]["kind"]
        if isinstance(node.op, ast.USub) and kind == "int":
            return {"kind": "unary", "op": "-", "operand": operand, "type": {"kind": "int"}, "span": span}
        if isinstance(node.op, ast.USub) and kind == "float":
            return {"kind": "unary", "op": "-", "operand": operand, "type": {"kind": "float"}, "span": span}
        if isinstance(node.op, ast.Not) and kind == "bool":
            return {"kind": "unary", "op": "not", "operand": operand, "type": {"kind": "bool"}, "span": span}
        if isinstance(node.op, ast.Not) and (kind in _TRUTHY or operand["type"].get("optional")):
            inner = self._truthy(operand, span)
            return {"kind": "unary", "op": "not", "operand": inner, "type": {"kind": "bool"}, "span": span}
        if isinstance(node.op, ast.USub) and kind == "class" and operand["type"].get("name") == "TimeDelta":
            return {"kind": "unary", "op": "-", "operand": operand, "type": operand["type"], "span": span}
        if isinstance(node.op, ast.UAdd) and kind in _NUMERIC:
            return operand
        if isinstance(node.op, ast.Invert) and kind == "int":
            return {"kind": "unary", "op": "~", "operand": operand, "type": {"kind": "int"}, "span": span}
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
                    left = {"kind": "cast", "op": "int_to_float", "value": left, "type": float_t, "span": span}
                if right_kind == "int":
                    right = {"kind": "cast", "op": "int_to_float", "value": right, "type": float_t, "span": span}
                return {
                    "kind": "binary",
                    "op": _FLOAT_BIN[op_type],
                    "left": left,
                    "right": right,
                    "type": float_t,
                    "span": span,
                }
        if left_kind == right_kind and left_kind in {"str", "bytes"} and isinstance(node.op, ast.Add):
            return {
                "kind": "binary",
                "op": "+",
                "left": left,
                "right": right,
                "type": {"kind": left_kind},
                "span": span,
            }
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
                left = {"kind": "cast", "op": "int_to_float", "value": left, "type": float_t, "span": span}
            if right_kind == "int":
                right = {"kind": "cast", "op": "int_to_float", "value": right, "type": float_t, "span": span}
            return {"kind": "binary", "op": "**", "left": left, "right": right, "type": float_t, "span": span}
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
                {
                    "kind": "if",
                    "cond": _compare("<", exp_n, _const_int(0, span), span),
                    "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                _assign(acc, _const_int(1, span), span),
                _assign(idx, _const_int(0, span), span),
                {
                    "kind": "while",
                    "cond": _compare("<", idx_n, exp_n, span),
                    "body": [
                        _assign(acc, _binary_int("*", acc_n, base_n, span), span),
                        _assign(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
                    ],
                    "span": span,
                },
            ]
        )
        return _name(acc, int_t, span)

    def _compare(self, node: ast.Compare, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return self._compare_chain(node, span)
        if isinstance(node.ops[0], (ast.In, ast.NotIn)):
            return self._contains(node, span, isinstance(node.ops[0], ast.NotIn))
        if isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            return self._is_none(node, span, isinstance(node.ops[0], ast.IsNot))
        op = _CMP.get(type(node.ops[0]))
        if op is None:
            self.fail(node, "PS2004", f"{_op_text(node.ops[0])} is not lowered")
        left = self._expr(node.left)
        right = self._expr(node.comparators[0])
        return self._compare_values(node, op, left, right, span)

    def _compare_values(
        self,
        node: ast.AST,
        op: str,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any]:
        """The two-operand compare on already-lowered values.

        Container `==`/`!=` synthesizes its loop here so container element
        types recurse through the same dispatch. `node` is only the
        fence-reporting anchor; recursion hands back the original compare.
        """
        if left["type"].get("ordered") or right["type"].get("ordered"):
            self.fail(node, "PS2004", "OrderedDict equality is not lowered")
        left_kind = left["type"]["kind"]
        right_kind = right["type"]["kind"]
        same_class = (
            left_kind == "class" and right_kind == "class" and left["type"].get("name") == right["type"].get("name")
        )
        if same_class and left["type"].get("name") in COMPARE_CLASSES:
            return _compare(op, left, right, span)
        if left["type"].get("optional") or right["type"].get("optional"):
            return self._opt_compare(node, op, left, right, span)
        if op in {"==", "!="}:
            if same_class:
                info = self.classes.get(str(left["type"]["name"]))
                if info is not None and "__eq__" in info["methods"]:
                    called = self._dunder_call(node, left, "__eq__", [right], span)
                    if called is not None:
                        if op == "!=":
                            return {
                                "kind": "unary",
                                "op": "not",
                                "operand": called,
                                "type": {"kind": "bool"},
                                "span": span,
                            }
                        return called
                if info is not None and info.get("tuple"):
                    return self._tuple_eq(node, left, right, span, negated=op == "!=")
                if info is not None:
                    # No __eq__ on a user class: CPython falls back to identity.
                    return _compare(op, left, right, span)
            if types_equal(left["type"], right["type"]):
                if left["type"]["kind"] == "list":
                    return self._seq_eq(node, left, right, span, negated=op == "!=")
                if left["type"]["kind"] == "dict":
                    return self._dict_eq(node, left, right, span, negated=op == "!=")
        if left_kind != right_kind or left_kind not in {
            "int",
            "float",
            "bool",
            "str",
            "bytes",
        }:
            if left_kind in _NUMERIC and right_kind in _NUMERIC:
                # int vs float promotes through sitofp, then fcmp.
                float_t = {"kind": "float"}
                if left_kind == "int":
                    left = {"kind": "cast", "op": "int_to_float", "value": left, "type": float_t, "span": span}
                else:
                    right = {"kind": "cast", "op": "int_to_float", "value": right, "type": float_t, "span": span}
                return _compare(op, left, right, span)
            self.fail(node, "PS2004", "comparisons need two values of the same scalar type")
        if left_kind == "bool" and op not in {"==", "!="}:
            self.fail(node, "PS2004", "bool ordering is not lowered")
        return _compare(op, left, right, span)

    def _identity(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        """`x is y` / `x is not y` where neither operand is the `None` literal.

        Heap objects (list, dict, set, class instances, exceptions) compare by
        pointer; str/bytes by backing-buffer pointer. int/float/bool carry no
        runtime address, so `is` lowers to value equality — matching CPython
        for singletons (True/False), constant-dedup'd literals, and the
        small-int cache, but answering True where CPython built two distinct
        objects. Different runtime kinds can never be the same object, so the
        compare folds to a constant.
        """
        left = self._expr(node.left)
        right = self._expr(node.comparators[0])
        lk = left["type"]["kind"]
        rk = right["type"]["kind"]
        if lk != rk:
            return _const_bool(negated, span)
        if lk in {"int", "float", "bool"}:
            return _compare("!=" if negated else "==", left, right, span)
        if lk == "none":
            return _const_bool(not negated, span)
        if lk in {"str", "bytes", "list", "dict", "class", "exc"}:
            return _compare("is not" if negated else "is", left, right, span)
        self.fail(node, "PS2004", "is is not lowered")
        raise AssertionError("unreachable")

    def _compare_chain(self, node: ast.Compare, span: dict[str, Any]) -> dict[str, Any]:
        """`a < b < c` is the existing compares joined by the existing `and`.

        Evaluate the left, then the first middle, and compare. Each later
        middle is stored once, inside the `and` branch, so a false link does
        not evaluate the rest. `is` stays fenced. A link the one-op compare
        rejects still fences.
        """
        if len(node.ops) != len(node.comparators) or len(node.ops) < 2:
            self.fail(node, "PS2004", "chained comparisons are not lowered")
        if any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            self.fail(node, "PS2004", "is is not lowered")
        bool_t = {"kind": "bool"}
        result = self._temp("chain")
        self.locals[result] = bool_t
        left_ref = self._chain_pin(node.left)
        prev_ref: ast.expr = self._chain_pin(node.comparators[0])
        first = self._one_link(node, node.ops[0], left_ref, prev_ref)
        self.prelude.append(_assign(result, first, span))
        for index in range(1, len(node.ops)):
            saved = self.prelude
            self.prelude = []
            try:
                operand = node.comparators[index]
                if index + 1 < len(node.ops):
                    right_ref: ast.expr = self._chain_pin(operand)
                else:
                    right_ref = operand
                link = self._one_link(node, node.ops[index], prev_ref, right_ref)
                if index + 1 < len(node.ops):
                    prev_ref = right_ref
            finally:
                inner = self.prelude
                self.prelude = saved
            inner.append(_assign(result, link, span))
            cond = self._truthy(_name(result, bool_t, span), span)
            self.prelude.append({"kind": "if", "cond": cond, "then": inner, "else": [], "span": span})
        return _name(result, bool_t, span)

    def _chain_pin(self, expr: ast.expr) -> ast.Name:
        value = self._expr(expr)
        temp = self._temp("cmp")
        self.locals[temp] = value["type"]
        pinned = ast.Name(id=temp, ctx=ast.Load())
        ast.copy_location(pinned, expr)
        self.prelude.append(_assign(temp, value, span_of(expr, self.filename)))
        return pinned

    def _one_link(self, origin: ast.Compare, op: ast.cmpop, left: ast.expr, right: ast.expr) -> dict[str, Any]:
        compare = ast.Compare(left=left, ops=[op], comparators=[right])
        ast.copy_location(compare, origin)
        return self._compare(compare, span_of(origin, self.filename))

    def _seq_eq(
        self,
        node: ast.AST,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
        *,
        negated: bool,
    ) -> dict[str, Any]:
        """`a == b` on two list or set values is length equality plus a loop.

        A set compares order-insensitively (`a[i] in b`); a list compares
        pairwise (`a[i] == b[i]`), recursing through `_compare_values` so
        nested containers work. Both operands are pinned so they evaluate
        once before the loop; a False `res` short-circuits the rest.
        """
        bool_t: dict[str, Any] = {"kind": "bool"}
        int_t: dict[str, Any] = {"kind": "int"}
        a = self._temp("eqa")
        b = self._temp("eqb")
        res = self._temp("eqr")
        idx = self._temp("eqi")
        self.locals[a] = left["type"]
        self.locals[b] = right["type"]
        self.locals[res] = bool_t
        self.locals[idx] = int_t
        a_n = _name(a, left["type"], span)
        b_n = _name(b, right["type"], span)
        res_n = _name(res, bool_t, span)
        idx_n = _name(idx, int_t, span)
        self.prelude.append(_assign(a, left, span))
        self.prelude.append(_assign(b, right, span))
        self.prelude.append(_assign(res, _compare("==", _len_call(a_n, span), _len_call(b_n, span), span), span))
        self.prelude.append(_assign(idx, _const_int(0, span), span))
        elem = left["type"]["elem"]
        sub_a = {"kind": "subscript", "value": a_n, "index": idx_n, "type": elem, "span": span}
        if left["type"].get("set"):
            pair: dict[str, Any] = {
                "kind": "contains",
                "hay": b_n,
                "needle": sub_a,
                "negated": False,
                "type": bool_t,
                "span": span,
            }
            inner: list[dict[str, Any]] = []
        else:
            sub_b = {"kind": "subscript", "value": b_n, "index": idx_n, "type": elem, "span": span}
            saved = self.prelude
            self.prelude = []
            try:
                pair = self._compare_values(node, "==", sub_a, sub_b, span)
            finally:
                inner = self.prelude
                self.prelude = saved
        then = [*inner, _assign(res, pair, span)]
        body = [
            {
                "kind": "if",
                "cond": self._truthy(res_n, span),
                "then": then,
                "else": [],
                "span": span,
            },
            _assign(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
        ]
        loop = {
            "kind": "while",
            "cond": _compare("<", idx_n, _len_call(a_n, span), span),
            "body": body,
            "span": span,
        }
        # `a is a` skips the loop: CPython's identity-implies-equal fast path
        # (a NaN element equals itself only under it).
        self.prelude.append(
            {
                "kind": "if",
                "cond": _compare("is not", a_n, b_n, span),
                "then": [
                    {
                        "kind": "if",
                        "cond": self._truthy(res_n, span),
                        "then": [loop],
                        "else": [],
                        "span": span,
                    }
                ],
                "else": [],
                "span": span,
            }
        )
        if negated:
            return {"kind": "unary", "op": "not", "operand": _name(res, bool_t, span), "type": bool_t, "span": span}
        return _name(res, bool_t, span)

    def _dict_eq(
        self,
        node: ast.AST,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
        *,
        negated: bool,
    ) -> dict[str, Any]:
        """`a == b` on two dicts: equal lengths, every `a` key in `b`, equal values.

        Iterates `a`'s key list (the `dict.keys` ABI call returns the runtime's
        insertion-ordered seq). The value lookup runs only while `res` is True,
        so a missing key never subscripts `b`.
        """
        bool_t: dict[str, Any] = {"kind": "bool"}
        int_t: dict[str, Any] = {"kind": "int"}
        key_t = left["type"]["key"]
        val_t = left["type"]["value"]
        a = self._temp("eqa")
        b = self._temp("eqb")
        ks = self._temp("eqk")
        res = self._temp("eqr")
        idx = self._temp("eqi")
        key = self._temp("eqq")
        self.locals[a] = left["type"]
        self.locals[b] = right["type"]
        self.locals[ks] = {"kind": "list", "elem": key_t}
        self.locals[res] = bool_t
        self.locals[idx] = int_t
        self.locals[key] = key_t
        a_n = _name(a, left["type"], span)
        b_n = _name(b, right["type"], span)
        ks_n = _name(ks, self.locals[ks], span)
        res_n = _name(res, bool_t, span)
        idx_n = _name(idx, int_t, span)
        key_n = _name(key, key_t, span)
        self.prelude.append(_assign(a, left, span))
        self.prelude.append(_assign(b, right, span))
        self.prelude.append(_assign(res, _compare("==", _len_call(a_n, span), _len_call(b_n, span), span), span))
        self.prelude.append(
            _assign(
                ks,
                self._abi_call("builtins", "dict.keys", [a_n], self.locals[ks], "ps_dict_keys", span),
                span,
            )
        )
        self.prelude.append(_assign(idx, _const_int(0, span), span))
        sub_a = {"kind": "subscript", "value": a_n, "index": key_n, "type": val_t, "span": span}
        sub_b = {"kind": "subscript", "value": b_n, "index": key_n, "type": val_t, "span": span}
        saved = self.prelude
        self.prelude = []
        try:
            pair = self._compare_values(node, "==", sub_a, sub_b, span)
        finally:
            inner = self.prelude
            self.prelude = saved
        value_cmp = {
            "kind": "if",
            "cond": self._truthy(res_n, span),
            "then": [*inner, _assign(res, pair, span)],
            "else": [],
            "span": span,
        }
        member_cmp = {
            "kind": "if",
            "cond": self._truthy(res_n, span),
            "then": [
                _assign(
                    res,
                    {
                        "kind": "contains",
                        "hay": b_n,
                        "needle": key_n,
                        "negated": False,
                        "type": bool_t,
                        "span": span,
                    },
                    span,
                ),
                value_cmp,
            ],
            "else": [],
            "span": span,
        }
        body = [
            _assign(
                key,
                {"kind": "subscript", "value": ks_n, "index": idx_n, "type": key_t, "span": span},
                span,
            ),
            member_cmp,
            _assign(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
        ]
        loop = {
            "kind": "while",
            "cond": _compare("<", idx_n, _len_call(ks_n, span), span),
            "body": body,
            "span": span,
        }
        self.prelude.append(
            {
                "kind": "if",
                "cond": _compare("is not", a_n, b_n, span),
                "then": [
                    {
                        "kind": "if",
                        "cond": self._truthy(res_n, span),
                        "then": [loop],
                        "else": [],
                        "span": span,
                    }
                ],
                "else": [],
                "span": span,
            }
        )
        if negated:
            return {"kind": "unary", "op": "not", "operand": _name(res, bool_t, span), "type": bool_t, "span": span}
        return _name(res, bool_t, span)

    def _tuple_eq(
        self,
        node: ast.AST,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
        *,
        negated: bool,
    ) -> dict[str, Any]:
        """`a == b` on a synthetic tuple class is fieldwise equality.

        Each field compare runs only while `res` is True, so the first
        mismatch short-circuits the rest; field types recurse through
        `_compare_values`, so a field that is itself a container still works.
        """
        bool_t: dict[str, Any] = {"kind": "bool"}
        name = str(left["type"]["name"])
        fields = self.classes[name]["fields"]
        a = self._temp("eqa")
        b = self._temp("eqb")
        res = self._temp("eqr")
        self.locals[a] = left["type"]
        self.locals[b] = right["type"]
        self.locals[res] = bool_t
        res_n = _name(res, bool_t, span)
        a_n = _name(a, left["type"], span)
        b_n = _name(b, right["type"], span)
        self.prelude.append(_assign(a, left, span))
        self.prelude.append(_assign(b, right, span))
        self.prelude.append(_assign(res, _const_bool(True, span), span))
        field_stmts: list[dict[str, Any]] = []
        for field in fields:
            f_a = {
                "kind": "attr",
                "value": a_n,
                "name": field["name"],
                "type": field["type"],
                "span": span,
            }
            f_b = {
                "kind": "attr",
                "value": b_n,
                "name": field["name"],
                "type": field["type"],
                "span": span,
            }
            saved = self.prelude
            self.prelude = []
            try:
                pair = self._compare_values(node, "==", f_a, f_b, span)
            finally:
                inner = self.prelude
                self.prelude = saved
            field_stmts.append(
                {
                    "kind": "if",
                    "cond": self._truthy(res_n, span),
                    "then": [*inner, _assign(res, pair, span)],
                    "else": [],
                    "span": span,
                }
            )
        self.prelude.append(
            {
                "kind": "if",
                "cond": _compare("is not", a_n, b_n, span),
                "then": field_stmts,
                "else": [],
                "span": span,
            }
        )
        if negated:
            return {"kind": "unary", "op": "not", "operand": _name(res, bool_t, span), "type": bool_t, "span": span}
        return _name(res, bool_t, span)

    def _contains(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        folded = self._const_tuple_membership(node, span, negated)
        if folded is not None:
            return folded
        if self._is_environ(node.comparators[0]):
            needle = self._expr(node.left)
            if needle["type"]["kind"] != "str":
                self.fail(node, "PS1003", "in on os.environ takes str")
            call = self._abi_call("os", "environ.contains", [needle], {"kind": "bool"}, "ps_env_has", span)
            if negated:
                return {"kind": "unary", "op": "not", "operand": call, "type": {"kind": "bool"}, "span": span}
            return call
        needle = self._expr(node.left)
        hay = self._expr(node.comparators[0])
        hay_kind = hay["type"]["kind"]
        if hay_kind in {"str", "bytes"}:
            if needle["type"]["kind"] != hay_kind:
                self.fail(node, "PS1003", f"in on {hay_kind} takes {hay_kind}")
        elif hay_kind == "list":
            if hay["type"]["elem"]["kind"] not in _SCALAR or not types_equal(needle["type"], hay["type"]["elem"]):
                self.fail(node, "PS1003", "in on a list takes an element of that list")
        elif hay_kind == "dict":
            if not types_equal(needle["type"], hay["type"]["key"]):
                self.fail(node, "PS1003", "in on a dict takes a key")
        else:
            self.fail(node, "PS2002", "in is lowered for str, bytes, list, and dict")
        if hay_kind == "list" and not hay["type"].get("set") and hay["type"]["elem"]["kind"] == "float":
            return self._float_list_membership(needle, hay, span, negated)
        return {
            "kind": "contains",
            "hay": hay,
            "needle": needle,
            "negated": negated,
            "type": {"kind": "bool"},
            "span": span,
        }

    def _float_list_membership(
        self, needle: dict[str, Any], hay: dict[str, Any], span: dict[str, Any], negated: bool
    ) -> dict[str, Any]:
        """Float list membership is ==, so NaN is not a member of a list of NaNs.

        Int and str lists stay on the contains helper. A set stays there too.
        """
        int_t = {"kind": "int"}
        float_t = {"kind": "float"}
        bool_t = {"kind": "bool"}
        held = self._temp("fneedle")
        src = self._temp("fseq")
        idx = self._temp("fidx")
        length = self._temp("flen")
        found = self._temp("fhas")
        item = self._temp("felt")
        self.locals[held] = float_t
        self.locals[src] = hay["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[found] = bool_t
        self.locals[item] = float_t
        self.prelude.extend(
            [
                _assign(held, needle, span),
                _assign(src, hay, span),
                _assign(length, _len_call(_name(src, hay["type"], span), span), span),
                _assign(idx, _const_int(0, span), span),
                _assign(found, _const_bool(False, span), span),
                {
                    "kind": "while",
                    "cond": _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    "body": [
                        _assign(
                            item,
                            {
                                "kind": "subscript",
                                "value": _name(src, hay["type"], span),
                                "index": _name(idx, int_t, span),
                                "type": float_t,
                                "span": span,
                            },
                            span,
                        ),
                        {
                            "kind": "if",
                            "cond": _compare("==", _name(item, float_t, span), _name(held, float_t, span), span),
                            "then": [_assign(found, _const_bool(True, span), span)],
                            "else": [],
                            "span": span,
                        },
                        _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
                    ],
                    "span": span,
                },
            ]
        )
        result = _name(found, bool_t, span)
        if negated:
            return {"kind": "unary", "op": "not", "operand": result, "type": bool_t, "span": span}
        return result

    def _const_tuple_membership(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any] | None:
        """`value in (c0, c1, ...)` of one const type is `==` joined by `or`.

        The tuple is not a class. Empty is false. One element is one `==`.
        `not in` is `not` of that test. A name, a list, a mix, or a non-constant
        element stays on the ordinary path.
        """
        hay = node.comparators[0]
        if not isinstance(hay, ast.Tuple) or any(isinstance(elt, ast.Starred) for elt in hay.elts):
            return None
        kinds = [_plain_const_kind(elt) for elt in hay.elts]
        if any(kind is None for kind in kinds) or len(set(kinds)) > 1:
            return None
        needle = self._expr(node.left)
        if not hay.elts:
            self.prelude.append({"kind": "expr", "expr": needle, "span": span})
            return _const_bool(negated, span)
        elem = kinds[0]
        if needle["type"]["kind"] != elem:
            if len(hay.elts) < 2:
                self.fail(hay, "PS2002", "tuples need at least two fields")
            self.fail(node, "PS2002", "in is lowered for str, bytes, list, and dict")
        temp = self._temp("in")
        self.locals[temp] = needle["type"]
        self.prelude.append(_assign(temp, needle, span))
        compares: list[ast.expr] = []
        for elt in hay.elts:
            left = ast.Name(id=temp, ctx=ast.Load())
            ast.copy_location(left, node.left)
            compare = ast.Compare(left=left, ops=[ast.Eq()], comparators=[elt])
            ast.copy_location(compare, node)
            compares.append(compare)
        if len(compares) == 1:
            test = self._expr(compares[0])
        else:
            chain = ast.BoolOp(op=ast.Or(), values=compares)
            ast.copy_location(chain, node)
            test = self._boolop(chain, span)
        if negated:
            return {"kind": "unary", "op": "not", "operand": test, "type": {"kind": "bool"}, "span": span}
        return test

    def _list(self, node: ast.List, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            if expect is None or expect["kind"] != "list":
                self.fail(node, "PS1001", "an empty list needs a list annotation")
            return {"kind": "list", "elems": [], "type": expect, "span": span}
        elem_expect = expect["elem"] if expect and expect["kind"] == "list" else None
        elems = [self._expr(elt, elem_expect) for elt in node.elts]
        elem = elems[0]["type"]
        if elem["kind"] == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        for item in elems[1:]:
            if not types_equal(item["type"], elem):
                self.fail(node, "PS1003", "list elements have one type")
        typ = {"kind": "list", "elem": elem}
        if expect and expect["kind"] == "list":
            self._expect(node, typ, expect, "list")
        return {"kind": "list", "elems": elems, "type": typ, "span": span}

    def _dict(self, node: ast.Dict, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.keys or any(key is None for key in node.keys):
            if expect is None or expect["kind"] != "dict":
                self.fail(node, "PS1001", "an empty dict needs a dict annotation")
            if any(key is None for key in node.keys):
                self.fail(node, "PS2002", "dict unpacking is not lowered")
            return {"kind": "dict", "pairs": [], "type": expect, "span": span}
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
            self._expect(node, typ, expect, "dict")
        return {"kind": "dict", "pairs": pairs, "type": typ, "span": span}

    def _subscript(self, node: ast.Subscript, span: dict[str, Any]) -> dict[str, Any]:
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
        if kind == "class" and self.classes.get(value["type"]["name"], {}).get("tuple"):
            return self._tuple_subscript(node, value, span)
        if kind == "list" and value["type"].get("set"):
            self.fail(node, "PS2002", "a set has no subscript")
        if kind == "list":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a list index is int")
            result = value["type"]["elem"]
        elif kind == "dict":
            self._expect(node, index["type"], value["type"]["key"], "dict key")
            result = value["type"]["value"]
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
        return {"kind": "subscript", "value": value, "index": index, "type": result, "span": span}

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
            self.prelude.append({"kind": "assign", "target": t, "value": v, "span": span})
        getitem = {
            "kind": "subscript",
            "value": _name(d_t, dtyp, span),
            "index": _name(k_t, key_t, span),
            "type": val_t,
            "span": span,
        }
        then = [_assign(v_t, getitem, span)]
        if dtyp.get("counter"):
            other = [_assign(v_t, _zero(val_t, span), span)]
        else:
            other = [
                {
                    "kind": "setitem",
                    "container": _name(d_t, dtyp, span),
                    "index": _name(k_t, key_t, span),
                    "value": _zero(val_t, span),
                    "span": span,
                },
                _assign(v_t, getitem, span),
            ]
        cond = {
            "kind": "contains",
            "hay": _name(d_t, dtyp, span),
            "needle": _name(k_t, key_t, span),
            "negated": False,
            "type": {"kind": "bool"},
            "span": span,
        }
        self.prelude.append({"kind": "if", "cond": cond, "then": then, "else": other, "span": span})
        return _name(v_t, val_t, span)

    def _attribute(self, node: ast.Attribute, span: dict[str, Any]) -> dict[str, Any]:
        if (
            isinstance(node.value, ast.Call)
            and node.attr == "name"
            and self._is_mod(node.value, "threading", "current_thread")
        ):
            return self._abi_call(
                "threading", "current_thread.name", [], {"kind": "str"}, "ps_island_thread_name", span
            )
        resolved = self._resolve_module(node)
        if resolved is not None:
            dotted, module, member, status = resolved
            if status == "erased":
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
                return {"kind": "const", "value": MATH_CONSTANTS[member], "type": {"kind": "float"}, "span": span}
            constant = module_constant(module, member)
            if constant is not None:
                value, typ = constant
                return {"kind": "const", "value": value, "type": typ, "span": span}
            if module == "sys" and member == "argv":
                return self._abi_call("sys", "argv", [], {"kind": "list", "elem": {"kind": "str"}}, "ps_argv_get", span)
            if (module == "math" and member in MATH_FUNCTIONS) or wave_function(module, member) is not None:
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            if member in SPECIAL_FUNCTIONS.get(module, frozenset()) or member_callable(module, member):
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            self.fail(node, "PS2001", f"{dotted} is not in the lowered subset of {module}")
        class_name = self.class_aliases.get(node.value.id, node.value.id) if isinstance(node.value, ast.Name) else None
        if class_name is not None and class_name in self.classes:
            info = self.classes[class_name]
            if info.get("enum"):
                member = info["members"].get(node.attr)
                if member is None:
                    self.fail(node, "PS2001", f"{node.value.id}.{node.attr} is not an enum member")
                return {"kind": "const", "value": str(member), "type": {"kind": "int"}, "span": span}
        value = self._expr(node.value)
        return self._lookup_attr(value, node.attr, span, node)

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
            return {
                "kind": "call",
                "callee": {"kind": "value", "target": target},
                "args": args,
                "type": target["type"]["return"],
                "span": span,
            }
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
            zero = {"kind": "const", "value": "0.0", "type": {"kind": "float"}, "span": span}
            return _compare("!=", expr, zero, span)
        if kind in {"str", "bytes", "list", "dict"}:
            length = {
                "kind": "call",
                "callee": {"kind": "builtin", "name": "len"},
                "args": [expr],
                "type": {"kind": "int"},
                "span": span,
            }
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
        self.prelude.append(_assign(temp, first, span))
        for value_node in node.values[1:]:
            saved = self.prelude
            self.prelude = []
            right = self._bool_operand(value_node, as_cond)
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
                    self.prelude.append(_assign(bool_temp, self._truthy(_name(temp, typ, span), span), span))
                    temp = bool_temp
                    typ = {"kind": "bool"}
                inner.append(_assign(temp, self._truthy(right, span), span))
            else:
                inner.append(_assign(temp, right, span))
            cond = self._truthy(_name(temp, typ, span), span)
            if not op_and:
                cond = {"kind": "unary", "op": "not", "operand": cond, "type": {"kind": "bool"}, "span": span}
            self.prelude.append({"kind": "if", "cond": cond, "then": inner, "else": [], "span": span})
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
        try:
            body = self._expr(node.body)
            body_pre = self.prelude
            self.prelude = []
            orelse = self._expr(node.orelse)
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
            self.prelude.append({"kind": "if", "cond": cond, "then": then_body, "else": else_body, "span": span})
            return _const_none(span)
        temp = self._temp("ifexp")
        self.locals[temp] = typ
        then_body.append(_assign(temp, body, span))
        else_body.append(_assign(temp, orelse, span))
        self.prelude.append({"kind": "if", "cond": cond, "then": then_body, "else": else_body, "span": span})
        return _name(temp, typ, span)

    def _ifexp_branch(
        self, prelude: list[dict[str, Any]], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if value["type"]["kind"] != "none":
            return list(prelude)
        body = list(prelude)
        if value["kind"] != "const":
            body.append({"kind": "expr", "expr": value, "span": span})
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
        self.prelude.append(_assign(o, first, span))
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
            self.prelude.append(_assign(res, oname, span))
            if not op_and:
                cond = {"kind": "unary", "op": "not", "operand": cond, "type": bool_t, "span": span}
            self.prelude.append(
                {"kind": "if", "cond": cond, "then": [*right_pre, _assign(res, right, span)], "else": [], "span": span}
            )
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
            self.prelude.append(_assign(res, oname, span))
            self.prelude.append(
                {"kind": "if", "cond": cond, "then": [*then_body, _assign(res, boxed, span)], "else": [], "span": span}
            )
            return _name(res, o_typ, span)
        self.locals[res] = elem
        val = _attr(oname, "_val", elem, span)
        self.prelude.append(
            {
                "kind": "if",
                "cond": cond,
                "then": [_assign(res, val, span)],
                "else": [*right_pre, _assign(res, right, span)],
                "span": span,
            }
        )
        return _name(res, elem, span)

    def _opt_compare(
        self,
        node: ast.AST,
        op: str,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any]:
        """`==`/`!=` on a tagged optional: compare tags, then values.

        Both-untagged compares equal (None == None). Against a bare value the
        tag must be set and the value equal; a non-optional operand of another
        type can never equal it, so the compare folds to a constant.
        """
        bool_t = {"kind": "bool"}
        if op not in {"==", "!="}:
            self.fail(node, "PS2004", "ordering an optional is not lowered")
        if left["type"].get("optional") and right["type"].get("optional"):
            return self._opt_opt_eq(node, left, right, span, negated=op == "!=")
        side, other = (left, right) if left["type"].get("optional") else (right, left)
        if other["type"]["kind"] == "none":
            tag = _attr(side, "_tag", bool_t, span)
            if op == "!=":
                return tag
            return {"kind": "unary", "op": "not", "operand": tag, "type": bool_t, "span": span}
        if not types_equal(other["type"], side["type"]["elem"]):
            return _const_bool(op == "!=", span)
        o = self._temp("optcmp")
        self.locals[o] = side["type"]
        self.prelude.append(_assign(o, side, span))
        oname = _name(o, side["type"], span)
        tag = _attr(oname, "_tag", bool_t, span)
        val = _attr(oname, "_val", side["type"]["elem"], span)
        saved = self.prelude
        self.prelude = []
        inner = self._compare_values(node, "==", val, other, span)
        inner_pre = self.prelude
        self.prelude = saved
        res = self._temp("optcmp")
        self.locals[res] = bool_t
        self.prelude.append(_assign(res, tag, span))
        self.prelude.append(
            {
                "kind": "if",
                "cond": _attr(oname, "_tag", bool_t, span),
                "then": [*inner_pre, _assign(res, inner, span)],
                "else": [],
                "span": span,
            }
        )
        base = _name(res, bool_t, span)
        if op == "!=":
            return {"kind": "unary", "op": "not", "operand": base, "type": bool_t, "span": span}
        return base

    def _opt_opt_eq(
        self,
        node: ast.AST,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
        *,
        negated: bool,
    ) -> dict[str, Any]:
        bool_t = {"kind": "bool"}
        la = self._temp("optcmp")
        lb = self._temp("optcmp")
        self.locals[la] = left["type"]
        self.locals[lb] = right["type"]
        self.prelude.append(_assign(la, left, span))
        self.prelude.append(_assign(lb, right, span))
        aname = _name(la, left["type"], span)
        bname = _name(lb, right["type"], span)
        tag_a = _attr(aname, "_tag", bool_t, span)
        tag_b = _attr(bname, "_tag", bool_t, span)
        if types_equal(left["type"]["elem"], right["type"]["elem"]):
            saved = self.prelude
            self.prelude = []
            val_eq = self._compare_values(
                node,
                "==",
                _attr(aname, "_val", left["type"]["elem"], span),
                _attr(bname, "_val", right["type"]["elem"], span),
                span,
            )
            val_pre = self.prelude
            self.prelude = saved
        else:
            # `int | None` vs `str | None` can still tie on both-None; the
            # value arms never equal each other.
            val_eq = _const_bool(False, span)
            val_pre = []
        res = self._temp("optcmp")
        self.locals[res] = bool_t
        then_b = [
            _assign(res, tag_b, span),
            {"kind": "if", "cond": tag_b, "then": [*val_pre, _assign(res, val_eq, span)], "else": [], "span": span},
        ]
        else_b = [_assign(res, {"kind": "unary", "op": "not", "operand": tag_b, "type": bool_t, "span": span}, span)]
        self.prelude.append({"kind": "if", "cond": tag_a, "then": then_b, "else": else_b, "span": span})
        base = _name(res, bool_t, span)
        if negated:
            return {"kind": "unary", "op": "not", "operand": base, "type": bool_t, "span": span}
        return base

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
        fmt = {"kind": "const", "value": "".join(template), "type": {"kind": "str"}, "span": span}
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
            # `{p!r}`/`{p!s}` stringify eagerly: the format engine cannot spell
            # a class, and `!r` on the spelled str would double-quote it.
            called = self._dunder_call(value, arg, "__repr__" if conv in "ra" else "__str__", [], arg["span"])
            if called is None:
                self.fail(value, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
            args.append(called)
            converted = True
        if not converted:
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
            called = self._dunder_call(node, arg, "__str__", [], arg["span"])
            if called is None:
                called = self._dunder_call(node, arg, "__repr__", [], arg["span"])
            if called is None:
                self.fail(node, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
            return called
        elif kind not in {"int", "float", "str", "bool", "bytes"}:
            self.fail(node, "PS2004", f"f-string formatting of {_show(arg['type'])} is not lowered")
        return arg

    def _as_str(self, expr: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        span = expr["span"]
        if kind == "str":
            return expr
        if kind in {"int", "float"}:
            return {
                "kind": "call",
                "callee": {"kind": "builtin", "name": "str"},
                "args": [expr],
                "type": {"kind": "str"},
                "span": span,
            }
        if kind == "class":
            called = self._dunder_call(node, expr, "__str__", [], span)
            if called is None:
                called = self._dunder_call(node, expr, "__repr__", [], span)
            if called is not None:
                return called
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
        self.prelude.append(_assign(temp, expr, span))
        return _name(temp, {"kind": "int"}, span)
