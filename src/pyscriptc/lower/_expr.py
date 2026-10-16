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
    _percent_pieces,
    _plain_const_kind,
    _show,
    _static_int,
)
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    wave_function,
)
from pyscriptc.stdmore import COMPARE_CLASSES, member_callable, module_constant

_INT_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//"}
_FLOAT_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}


class ExprMixin:
    def _expr(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if isinstance(node, ast.ListComp):
            return self._listcomp(node, span, expect)
        if isinstance(node, ast.IfExp):
            return self._ifexp(node, span)
        self.fail(node, "PS2002", f"{type(node).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _name(self, node: ast.Name, span: dict[str, Any]) -> dict[str, Any]:
        if node.id in self.locals:
            return _name(node.id, self.locals[node.id], span)
        slot = self.global_slots.get(node.id)
        if slot is not None and slot.get("active"):
            return self._global_get(node.id, span)
        if node.id in self.constants:
            found = self.constants[node.id]
            if found.get("kind") == "tuple-const":
                return self._materialize_tuple(found["elems"], span)
            return {**found, "span": span}
        if node.id in self.type_aliases:
            self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
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
        if isinstance(node.op, ast.Not) and kind in _TRUTHY:
            inner = self._truthy(operand, span)
            return {"kind": "unary", "op": "not", "operand": inner, "type": {"kind": "bool"}, "span": span}
        if isinstance(node.op, ast.USub) and kind == "class" and operand["type"].get("name") == "TimeDelta":
            return {"kind": "unary", "op": "-", "operand": operand, "type": operand["type"], "span": span}
        self.fail(node, "PS2004", f"unary {type(node.op).__name__} on {kind} is not lowered")
        raise AssertionError("unreachable")

    def _binary(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        if isinstance(node.op, ast.Mod):
            return self._percent(node, span)
        left = self._expr(node.left)
        right = self._expr(node.right)
        left_kind = left["type"]["kind"]
        right_kind = right["type"]["kind"]
        if left_kind == "bool" or right_kind == "bool":
            self.fail(node, "PS2004", "bool arithmetic is not lowered (bool is a subclass of int in Python)")
        if isinstance(node.op, ast.Pow):
            return self._pow(node, left, right, span)
        op_type = type(node.op)
        if left_kind == "int" and right_kind == "int" and op_type in _INT_BIN:
            return _binary_int(_INT_BIN[op_type], left, right, span)
        if left_kind == "float" and right_kind == "float" and op_type in _FLOAT_BIN:
            return {
                "kind": "binary",
                "op": _FLOAT_BIN[op_type],
                "left": left,
                "right": right,
                "type": {"kind": "float"},
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
        self.fail(node, "PS2004", f"{op_type.__name__} on {left_kind} and {right_kind} is not lowered")
        raise AssertionError("unreachable")

    def _pow(
        self, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        """`base ** exp` is a loop of the existing int `*`.

        Both operands are ints. The base and the exponent are stored once, then
        the accumulator starts at 1 and is multiplied `exp` times. `0 ** 0` is
        that 1. A negative exponent stays fenced: a constant is refused here,
        and a computed negative raises ValueError instead of becoming a float.
        """
        if left["type"]["kind"] != "int" or right["type"]["kind"] != "int":
            self.fail(node, "PS2004", "** is only lowered for int")
        static = _static_int(right)
        if static is not None and static < 0:
            self.fail(node, "PS2004", "a negative exponent is not lowered")
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
            self.fail(node, "PS2004", f"{type(node.ops[0]).__name__} is not lowered")
        left = self._expr(node.left)
        right = self._expr(node.comparators[0])
        if left["type"].get("ordered") or right["type"].get("ordered"):
            self.fail(node, "PS2004", "OrderedDict equality is not lowered")
        if (
            left["type"]["kind"] == "class"
            and right["type"]["kind"] == "class"
            and left["type"].get("name") == right["type"].get("name")
            and left["type"].get("name") in COMPARE_CLASSES
        ):
            return _compare(op, left, right, span)
        if left["type"]["kind"] != right["type"]["kind"] or left["type"]["kind"] not in {
            "int",
            "float",
            "bool",
            "str",
            "bytes",
        }:
            self.fail(node, "PS2004", "comparisons need two values of the same scalar type")
        if left["type"]["kind"] == "bool" and op not in {"==", "!="}:
            self.fail(node, "PS2004", "bool ordering is not lowered")
        return _compare(op, left, right, span)

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

    def _contains(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        folded = self._const_tuple_membership(node, span, negated)
        if folded is not None:
            return folded
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
        self.fail(node, "PS2002", "this call is not lowered")
        raise AssertionError("unreachable")

    def _truthy(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        if kind == "bool":
            return expr
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

    def _joined(self, node: ast.JoinedStr, span: dict[str, Any]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(self._const(value, span_of(value, self.filename)))
                continue
            if isinstance(value, ast.FormattedValue):
                if value.conversion != -1 or value.format_spec is not None:
                    self.fail(value, "PS2002", "f-string format specs are not lowered")
                parts.append(self._as_str(self._expr(value.value), value))
                continue
            self.fail(value, "PS2002", "f-strings are not lowered")
        if self.function is None:
            folded = _const_str_join(parts, span)
            if folded is not None:
                return folded
        return _concat_str(parts, span)

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
        self.fail(node, "PS2002", f"formatting {_show(expr['type'])} is not lowered")
        raise AssertionError("unreachable")

    def _percent(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(node.left, ast.Constant) or not isinstance(node.left.value, str):
            self.fail(node, "PS2004", "a format string must be a literal")
        if any(ord(ch) > 255 for ch in node.left.value):
            self.fail(node, "PS2005", "string literals outside Latin-1 are not lowered")
        pieces = _percent_pieces(node.left.value)
        if pieces is None:
            self.fail(node, "PS2004", "% formatting is lowered for %s and %d")
        specs = [kind for kind, _text in pieces if kind != "lit"]
        if isinstance(node.right, ast.Tuple):
            values = list(node.right.elts)
        else:
            values = [node.right]
        if len(values) != len(specs):
            self.fail(node, "PS2003", "format string and values disagree")
        rendered: list[dict[str, Any]] = []
        index = 0
        for kind, text in pieces:
            if kind == "lit":
                if text:
                    rendered.append({"kind": "const", "value": text, "type": {"kind": "str"}, "span": span})
                continue
            expr = self._expr(values[index])
            index += 1
            if kind == "s":
                if expr["type"]["kind"] != "str":
                    self.fail(node, "PS1003", "%s takes a str")
                rendered.append(expr)
            else:
                if expr["type"]["kind"] != "int":
                    self.fail(node, "PS1003", "%d takes an int")
                rendered.append(self._as_str(expr, node))
        return _concat_str(rendered, span)

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
        """A positive step is the existing slice. A negative step stays fenced.

        Step 0 is the runtime's ValueError. The step expression is one operand,
        so it runs once.
        """
        if node is None:
            return _const_int(1, span)
        expr = self._expr(node, {"kind": "int"})
        if expr["type"]["kind"] != "int":
            self.fail(node, "PS1003", "a slice step is int")
        static = _static_int(expr)
        if static is not None and static < 0:
            self.fail(node, "PS2002", "a negative slice step is not lowered")
        return expr

    def _pin_slice_int(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Store a slice operand once. A module call emits its arguments twice."""
        if expr["kind"] in {"const", "name"}:
            return expr
        temp = self._temp("sl")
        self.locals[temp] = {"kind": "int"}
        self.prelude.append(_assign(temp, expr, span))
        return _name(temp, {"kind": "int"}, span)
