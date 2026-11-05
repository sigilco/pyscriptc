"""Compare and optional-equality lowering, split verbatim from `lower._expr`.

`ExprCompareMixin` holds the compare family: `_compare`, the two-operand
`_compare_values`, identity (`is` / `is not`), compare chains, the sequence,
dict and tuple equality loops, membership (`in` / `not in`), and the
optional-aware equality paths (`_opt_compare`, `_opt_opt_eq`). The
module-level node builders the family spells with (`_compare_node`,
`_unary_node`, `_cast_node`, `_contains_node`, `_subscript_node`,
`_assign_node`, `_attr_node`, `_if_node`, `_while_node`, `_expr_stmt_node`,
plus `_mk_span`/`_mk_type`/`dict_to_span_local` and the `_NUMERIC` table)
moved with it, so nothing here back-imports `lower._expr`. `ExprMixin`
composes it the way `CallsMixin` composes `CallAttrMixin`, so every method
stays reachable as `self._compare(...)` and friends. Every body below is the
code that lived in `lower._expr`, unchanged: the IR and diagnostics are
byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.errors import span_of
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _CMP,
    _SCALAR,
    _attr,
    _binary_int,
    _compare,
    _const_bool,
    _const_int,
    _len_call,
    _name,
    _op_text,
    _plain_const_kind,
)
from pyscriptc.stdmore import COMPARE_CLASSES
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


class ExprCompareMixin:
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
                            return _unary_node("not", called, {"kind": "bool"}, span)
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
                    left = _cast_node("int_to_float", left, float_t, span)
                else:
                    right = _cast_node("int_to_float", right, float_t, span)
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
        self.prelude.append(_assign_node(result, first, span))
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
            inner.append(_assign_node(result, link, span))
            cond = self._truthy(_name(result, bool_t, span), span)
            self.prelude.append(_if_node(cond, inner, [], span))
        return _name(result, bool_t, span)

    def _chain_pin(self, expr: ast.expr) -> ast.Name:
        value = self._expr(expr)
        temp = self._temp("cmp")
        self.locals[temp] = value["type"]
        pinned = ast.Name(id=temp, ctx=ast.Load())
        ast.copy_location(pinned, expr)
        self.prelude.append(_assign_node(temp, value, span_of(expr, self.filename)))
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
        self.prelude.append(_assign_node(a, left, span))
        self.prelude.append(_assign_node(b, right, span))
        self.prelude.append(_assign_node(res, _compare("==", _len_call(a_n, span), _len_call(b_n, span), span), span))
        self.prelude.append(_assign_node(idx, _const_int(0, span), span))
        elem = left["type"]["elem"]
        sub_a = _subscript_node(a_n, idx_n, elem, span)
        if left["type"].get("set"):
            pair: dict[str, Any] = _contains_node(b_n, sub_a, span, negated=False)
            inner: list[dict[str, Any]] = []
        else:
            sub_b = _subscript_node(b_n, idx_n, elem, span)
            saved = self.prelude
            self.prelude = []
            try:
                pair = self._compare_values(node, "==", sub_a, sub_b, span)
            finally:
                inner = self.prelude
                self.prelude = saved
        then = [*inner, _assign_node(res, pair, span)]
        body = [
            _if_node(self._truthy(res_n, span), then, [], span),
            _assign_node(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
        ]
        loop = _while_node(_compare_node("<", idx_n, _len_call(a_n, span), span), body, span)
        # `a is a` skips the loop: CPython's identity-implies-equal fast path
        # (a NaN element equals itself only under it).
        self.prelude.append(
            _if_node(
                _compare_node("is not", a_n, b_n, span),
                [_if_node(self._truthy(res_n, span), [loop], [], span)],
                [],
                span,
            )
        )
        if negated:
            return _unary_node("not", _name(res, bool_t, span), bool_t, span)
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
        self.prelude.append(_assign_node(a, left, span))
        self.prelude.append(_assign_node(b, right, span))
        self.prelude.append(_assign_node(res, _compare("==", _len_call(a_n, span), _len_call(b_n, span), span), span))
        self.prelude.append(
            _assign_node(
                ks,
                self._abi_call("builtins", "dict.keys", [a_n], self.locals[ks], "ps_dict_keys", span),
                span,
            )
        )
        self.prelude.append(_assign_node(idx, _const_int(0, span), span))
        sub_a = _subscript_node(a_n, key_n, val_t, span)
        sub_b = _subscript_node(b_n, key_n, val_t, span)
        saved = self.prelude
        self.prelude = []
        try:
            pair = self._compare_values(node, "==", sub_a, sub_b, span)
        finally:
            inner = self.prelude
            self.prelude = saved
        value_cmp = _if_node(self._truthy(res_n, span), [*inner, _assign_node(res, pair, span)], [], span)
        member_cmp = _if_node(
            self._truthy(res_n, span),
            [
                _assign_node(res, _contains_node(b_n, key_n, span, negated=False), span),
                value_cmp,
            ],
            [],
            span,
        )
        body = [
            _assign_node(key, _subscript_node(ks_n, idx_n, key_t, span), span),
            member_cmp,
            _assign_node(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
        ]
        loop = _while_node(_compare_node("<", idx_n, _len_call(ks_n, span), span), body, span)
        self.prelude.append(
            _if_node(
                _compare_node("is not", a_n, b_n, span),
                [_if_node(self._truthy(res_n, span), [loop], [], span)],
                [],
                span,
            )
        )
        if negated:
            return _unary_node("not", _name(res, bool_t, span), bool_t, span)
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
        self.prelude.append(_assign_node(a, left, span))
        self.prelude.append(_assign_node(b, right, span))
        self.prelude.append(_assign_node(res, _const_bool(True, span), span))
        field_stmts: list[dict[str, Any]] = []
        for field in fields:
            f_a = _attr_node(a_n, field["name"], field["type"], span)
            f_b = _attr_node(b_n, field["name"], field["type"], span)
            saved = self.prelude
            self.prelude = []
            try:
                pair = self._compare_values(node, "==", f_a, f_b, span)
            finally:
                inner = self.prelude
                self.prelude = saved
            field_stmts.append(_if_node(self._truthy(res_n, span), [*inner, _assign_node(res, pair, span)], [], span))
        self.prelude.append(_if_node(_compare_node("is not", a_n, b_n, span), field_stmts, [], span))
        if negated:
            return _unary_node("not", _name(res, bool_t, span), bool_t, span)
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
                return _unary_node("not", call, {"kind": "bool"}, span)
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
        return _contains_node(hay, needle, span, negated=negated)

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
                _assign_node(held, needle, span),
                _assign_node(src, hay, span),
                _assign_node(length, _len_call(_name(src, hay["type"], span), span), span),
                _assign_node(idx, _const_int(0, span), span),
                _assign_node(found, _const_bool(False, span), span),
                _while_node(
                    _compare_node("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    [
                        _assign_node(
                            item,
                            _subscript_node(_name(src, hay["type"], span), _name(idx, int_t, span), float_t, span),
                            span,
                        ),
                        _if_node(
                            _compare_node("==", _name(item, float_t, span), _name(held, float_t, span), span),
                            [_assign_node(found, _const_bool(True, span), span)],
                            [],
                            span,
                        ),
                        _assign_node(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
                    ],
                    span,
                ),
            ]
        )
        result = _name(found, bool_t, span)
        if negated:
            return _unary_node("not", result, bool_t, span)
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
            self.prelude.append(_expr_stmt_node(needle, span))
            return _const_bool(negated, span)
        elem = kinds[0]
        if needle["type"]["kind"] != elem:
            if len(hay.elts) < 2:
                self.fail(hay, "PS2002", "tuples need at least two fields")
            self.fail(node, "PS2002", "in is lowered for str, bytes, list, and dict")
        temp = self._temp("in")
        self.locals[temp] = needle["type"]
        self.prelude.append(_assign_node(temp, needle, span))
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
            return _unary_node("not", tag, bool_t, span)
        if not types_equal(other["type"], side["type"]["elem"]):
            return _const_bool(op == "!=", span)
        o = self._temp("optcmp")
        self.locals[o] = side["type"]
        self.prelude.append(_assign_node(o, side, span))
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
        self.prelude.append(_assign_node(res, tag, span))
        self.prelude.append(
            _if_node(_attr(oname, "_tag", bool_t, span), [*inner_pre, _assign_node(res, inner, span)], [], span)
        )
        base = _name(res, bool_t, span)
        if op == "!=":
            return _unary_node("not", base, bool_t, span)
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
        self.prelude.append(_assign_node(la, left, span))
        self.prelude.append(_assign_node(lb, right, span))
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
            _assign_node(res, tag_b, span),
            _if_node(tag_b, [*val_pre, _assign_node(res, val_eq, span)], [], span),
        ]
        else_b = [_assign_node(res, _unary_node("not", tag_b, bool_t, span), span)]
        self.prelude.append(_if_node(tag_a, then_b, else_b, span))
        base = _name(res, bool_t, span)
        if negated:
            return _unary_node("not", base, bool_t, span)
        return base


_NUMERIC = {"int", "float"}


def _mk_span(span: dict[str, Any]) -> Any:
    """The Span record the dict-IR span spells."""
    return dict_to_span_local(span)


def _mk_type(typ: dict[str, Any]) -> Any:
    """The T record the dict-IR type spells."""
    return dict_to_type(typ)


def dict_to_span_local(span: dict[str, Any]) -> Any:
    """The typed span one dict-IR span spells."""
    return _ir.Span(span["file"], span["line"], span["col"], span["endLine"], span["endCol"])


def _compare_node(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The compare node; its type is always bool."""
    return node_to_dict(_ir.compare(op, dict_to_node(left), dict_to_node(right), _ir.t_leaf("bool"), _mk_span(span)))


def _unary_node(op: str, operand: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The unary operation node."""
    return node_to_dict(_ir.unary(op, dict_to_node(operand), _mk_type(typ), _mk_span(span)))


def _cast_node(op: str, value: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The cast node."""
    return node_to_dict(_ir.cast(op, dict_to_node(value), _mk_type(typ), _mk_span(span)))


def _contains_node(
    hay: dict[str, Any], needle: dict[str, Any], span: dict[str, Any], *, negated: bool
) -> dict[str, Any]:
    """The membership test node."""
    return node_to_dict(
        _ir.contains(dict_to_node(hay), dict_to_node(needle), _ir.t_leaf("bool"), _mk_span(span), negated)
    )


def _subscript_node(
    value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The subscript read node."""
    return node_to_dict(_ir.subscript(dict_to_node(value), dict_to_node(index), _mk_type(typ), _mk_span(span)))


def _assign_node(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The assignment statement node."""
    return node_to_dict(_ir.assign(target, dict_to_node(value), _mk_span(span)))


def _attr_node(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The attribute read node."""
    return node_to_dict(_ir.attr(name, _mk_type(typ), dict_to_node(value), _mk_span(span)))


def _if_node(
    cond: dict[str, Any], then: list[dict[str, Any]], else_: list[dict[str, Any]], span: dict[str, Any]
) -> dict[str, Any]:
    """The if statement node."""
    return node_to_dict(
        _ir.if_stmt(
            dict_to_node(cond),
            [dict_to_node(item) for item in then],
            [dict_to_node(item) for item in else_],
            _mk_span(span),
        )
    )


def _while_node(cond: dict[str, Any], body: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    """The while statement node."""
    return node_to_dict(_ir.while_stmt(dict_to_node(cond), [dict_to_node(item) for item in body], _mk_span(span)))


def _expr_stmt_node(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The expression statement node."""
    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))
