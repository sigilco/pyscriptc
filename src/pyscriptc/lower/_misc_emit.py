"""Small emit helpers, split verbatim from `lower.__init__`.

`MiscEmitMixin` holds the small statement and expression emit helpers that
do not belong to a larger family: subscript and attribute stores, the bool
condition coercions, set displays, comprehensions, tuple constants, the
`is None` compare, the global-slot read-name probe, tuple materialization,
and the temp counter. `Lowerer` composes it, so every method stays reachable
as `self._setitem(...)` and friends. Every body below is the code that lived
in `lower.__init__`, unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.errors import span_of
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _TRUTHY,
    _assign,
    _attr,
    _const_str,
    _dotted,
    _is_none_expr,
    _name,
    _node_kind,
    _node_type_kind,
    _tuple_assign,
    _type_kind,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict
from pyscriptc.tirserde import dict_to_span as dict_to_span_local


def _expr_node(expr: dict[str, Any], span: dict[str, Any]) -> Any:
    """The expression statement node."""

    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _setitem_node(container: dict[str, Any], index: dict[str, Any], value: dict[str, Any], span: dict[str, Any]) -> Any:
    """The container store node."""

    return node_to_dict(
        _ir.setitem_stmt(dict_to_node(container), dict_to_node(index), dict_to_node(value), _mk_span(span))
    )


def _setattr_node(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> Any:
    """The attribute write node."""

    return node_to_dict(_ir.setattr_stmt(dict_to_node(obj), name, dict_to_node(value), _mk_span(span)))


def _list_empty_node(typ: dict[str, Any], span: dict[str, Any]) -> Any:
    """The empty list display node."""

    return node_to_dict(_ir.list_lit([], _mk_type(typ), _mk_span(span)))


def _const_flag_node(value: bool, span: dict[str, Any]) -> Any:
    """The int constant node one slice-bound `"True"`/`"False"` const spells."""

    return node_to_dict(_ir.const_node(_ir.P_INT, str(value), _ir.t_leaf("int"), _mk_span(span)))


def _mk_span(span: dict[str, Any]) -> Any:
    """The Span record the dict-IR span spells."""

    return dict_to_span_local(span)


def _mk_type(typ: dict[str, Any]) -> Any:
    """The T record the dict-IR type spells."""

    return dict_to_type(typ)


class MiscEmitMixin:
    def _setitem(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Subscript)
        if isinstance(target.slice, ast.Slice):
            # `xs[a:b] = ys` splices a list, growing or shrinking it; a step
            # stays fenced with the slice-read message.
            slc = target.slice
            container = self._expr(target.value)
            kind = _node_type_kind(container)
            if kind == "list" and container["type"].get("set"):
                self.fail(target, "PS2002", "a set has no item assignment")
            if kind != "list":
                self.fail(target, "PS2002", "slice assignment targets a list")
            if slc.step is not None:
                self.fail(target, "PS2002", "slices are not lowered")
            value = self._expr(stmt.value)
            if _node_type_kind(value) != "list" or not types_equal(value["type"]["elem"], container["type"]["elem"]):
                self.fail(stmt, "PS1003", "slice assignment takes a list of the same element type")
            start, has_start = self._slice_bound(slc.lower, span)
            stop, has_stop = self._slice_bound(slc.upper, span)
            flags = [
                _const_flag_node(has_start, span),
                _const_flag_node(has_stop, span),
            ]
            call = self._abi_call(
                "builtins",
                "list.setslice",
                [container, start, stop, *flags, value],
                {"kind": "none"},
                "ps_seq_splice",
                span,
            )
            return _expr_node(call, span)
        container = self._expr(target.value)
        index = self._expr(target.slice)
        kind = _node_type_kind(container)
        if kind == "list" and container["type"].get("set"):
            self.fail(target, "PS2002", "a set has no item assignment")
        if kind == "list":
            if _node_type_kind(index) != "int":
                self.fail(target, "PS1003", "a list index is int")
            value = self._expr(stmt.value, container["type"]["elem"])
            self._expect(stmt, value["type"], container["type"]["elem"], "list item")
        elif kind == "dict":
            self._expect(target, index["type"], container["type"]["key"], "dict key")
            value = self._expr(stmt.value, container["type"]["value"])
        else:
            self.fail(target, "PS2002", "item assignment targets a list or dict")
        return _setitem_node(container, index, value, span)

    def _setattr(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        return self._setattr_target(target, stmt.value, stmt, span)

    def _setattr_target(
        self, target: ast.Attribute, value_node: ast.expr, report: ast.AST, span: dict[str, Any]
    ) -> dict[str, Any]:
        obj = self._expr(target.value)
        if _node_type_kind(obj) != "class":
            self.fail(target, "PS2001", f"attribute assignment is not lowered ({_dotted(target)})")
        if obj["type"].get("optional") and _type_kind(obj["type"].get("elem", {})) == "class":
            # `x.f = v` on a `T | None` cell: CPython raises AttributeError
            # when the tag is unset; a set tag writes through `_val`.
            obj = self._opt_payload(obj, target.attr, span)
        class_name = obj["type"]["name"]
        # boxed:begin
        # An island handle is opaque, and the seam carries values one way:
        # a compiled attribute write would have to carry the value back into
        # the island, so it fences instead of reaching a native field table.
        if obj["type"].get("island"):
            self.fail(
                target,
                "PS2001",
                f"{class_name} is an island handle: mutate it on the island, or read the result back",
            )
        # boxed:end
        info = self.classes[class_name]
        if info.get("namedtuple"):
            self.fail(target, "PS2002", f"{class_name}.{target.attr} is read-only on a namedtuple")
        field = next((item for item in info["fields"] if item["name"] == target.attr), None)
        if field is None:
            self.fail(target, "PS2001", f"{class_name}.{target.attr} is not a field")
        value = self._expr(value_node, field["type"])
        self._expect(report, value["type"], field["type"], target.attr)
        return _setattr_node(obj, target.attr, value, span)

    def _opt_payload(self, obj: dict[str, Any], attr: str, span: dict[str, Any]) -> dict[str, Any]:
        """The `_val` of a tagged optional cell, guarded like CPython's miss.

        `x.f` on a `T | None` cell raises AttributeError when the tag is
        unset; a set tag yields the payload the field lookup reads. A
        non-name object pins to a temp so the guard and the read see one
        evaluation.
        """
        typ = obj["type"]
        if _node_kind(obj) != "name":
            temp = self._temp("optattr")
            self.locals[temp] = typ
            self.prelude.append(_assign(temp, obj, span))
            obj = _name(temp, typ, span)
        tag = _attr(obj, "_tag", {"kind": "bool"}, span)
        cond = node_to_dict(_ir.unary("not", dict_to_node(tag), _ir.t_leaf("bool"), _mk_span(span)))
        message = f"'NoneType' object has no attribute '{attr}'"
        raised = node_to_dict(
            _ir.raise_stmt("AttributeError", _mk_span(span), -1, dict_to_node(_const_str(message, span)))
        )
        self.prelude.append(node_to_dict(_ir.if_stmt(cond, [dict_to_node(raised)], [], _mk_span(span))))
        return _attr(obj, "_val", typ["elem"], span)

    def _bool_cond_of(self, node: ast.expr, want: dict[str, Any]) -> dict[str, Any]:
        """A condition whose expected type rides down (the `__getattr__` hook)."""
        if isinstance(node, ast.BoolOp):
            return self._bool_cond(node)
        cond = self._expr(node, want)
        kind = _node_type_kind(cond)
        if kind == "bool":
            return cond
        if kind in _TRUTHY or cond["type"].get("optional"):
            return self._truthy(cond, span_of(node, self.filename))
        self.fail(node, "PS2004", "condition must be a bool, not a truthy value")
        raise AssertionError("unreachable")

    def _set_display(self, node: ast.Set, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            self.fail(node, "PS1001", "an empty set needs a set annotation")
        if any(isinstance(elt, ast.Starred) for elt in node.elts):
            self.fail(node, "PS2002", "set unpacking is not lowered")
        elem_expect = expect["elem"] if expect and expect.get("set") else None
        first = self._expr(node.elts[0], elem_expect)
        elem = first["type"]
        if _type_kind(elem) not in {"int", "str"}:
            self.fail(node, "PS2002", "set elements are int or str")
        typ = node_to_dict(_ir.t_list(dict_to_type(elem), set_=True))
        if expect and expect.get("set"):
            self._expect(node, typ, expect, "set")
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, _list_empty_node(typ, span), span))
        self.prelude.extend(self._set_insert_stmts(_name(dest, typ, span), typ, first, span))
        for elt in node.elts[1:]:
            value = self._expr(elt, elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", "set elements have one type")
            self.prelude.extend(self._set_insert_stmts(_name(dest, typ, span), typ, value, span))
        return _name(dest, typ, span)

    def _comp(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        span: dict[str, Any],
        expect: dict[str, Any] | None,
        pred: tuple[str, bool] | None = None,
    ) -> dict[str, Any]:
        # GeneratorExp lowers like ListComp: the elements materialize
        # eagerly into a list (the eager model documented for R24). A `pred`
        # pair (`all`/`any` over the comprehension) swaps the materialized
        # list for updates of the named bool accumulator.
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            kind = "list"
        elif isinstance(node, ast.SetComp):
            kind = "set"
        else:
            kind = "dict"
        if pred is not None and kind != "list":
            self.fail(node, "PS2002", "all/any over a comprehension lowers a list or generator")
        if any(gen.is_async for gen in node.generators):
            self.fail(node, "PS2002", "async comprehensions are not lowered")
        for gen in node.generators:
            if isinstance(gen.target, (ast.Tuple, ast.List)):
                for elt in gen.target.elts:
                    if not isinstance(elt, ast.Name):
                        self.fail(elt, "PS2002", "for unpacks flat names")
            elif not isinstance(gen.target, ast.Name):
                self.fail(node, "PS2002", "for targets a single name")
        dest = self._temp("comp")
        saved = (
            self.comp_elt,
            self.comp_key,
            self.comp_kind,
            self.comp_gens,
            self.comp_ifs,
            self.comp_dest,
            self.comp_init,
            self.comp_expect,
            self.comp_ready,
            self.comp_pred,
        )
        self.comp_elt = node.value if isinstance(node, ast.DictComp) else node.elt
        self.comp_key = node.key if isinstance(node, ast.DictComp) else None
        self.comp_kind = kind
        self.comp_gens = node.generators[1:]
        self.comp_ifs = node.generators[0].ifs
        self.comp_dest = dest
        self.comp_init = None
        self.comp_expect = expect
        self.comp_ready = False
        self.comp_pred = pred
        first = node.generators[0]
        loop = ast.For(target=first.target, iter=first.iter, body=[ast.Pass()], orelse=[], type_comment=None)
        ast.copy_location(loop, node)
        try:
            lowered = self._for(loop, span)
        finally:
            init = self.comp_init
            (
                self.comp_elt,
                self.comp_key,
                self.comp_kind,
                self.comp_gens,
                self.comp_ifs,
                self.comp_dest,
                self.comp_init,
                self.comp_expect,
                self.comp_ready,
                self.comp_pred,
            ) = saved
        if init is None:
            self.fail(node, "PS2002", f"{kind} comprehension is not lowered")
        self.prelude.append(init)
        self.prelude.extend(lowered)
        if pred is not None:
            return _name(pred[0], {"kind": "bool"}, span)
        return _name(dest, self.locals[dest], span)

    def _store_tuple_constant(self, name: str, node: ast.Tuple, stmt: ast.AST) -> None:
        if len(node.elts) < 2:
            self.fail(node, "PS2002", "tuples need at least two fields")
        elems: list[dict[str, Any]] = []
        for elt in node.elts:
            value = self._module_fold(elt)
            if value is None:
                value = self._expr(elt)
            if _node_kind(value) != "const":
                self.fail(stmt, "PS2002", "a module assignment must be a constant")
            elems.append(value)
        self._tuple_type([elem["type"] for elem in elems], node)
        self._store_constant(name, {"kind": "tuple-const", "elems": elems}, stmt)

    def _is_none(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        left, right = node.left, node.comparators[0]
        # A process slot's `is None` is the unset flag. Every other None literal
        # is the existing == / != compare against none.
        if _is_none_expr(right):
            name = self._slot_read_name(left)
            if name is not None:
                return self._slot_is_unset(name, span, negated)
            return self._compare_none(node, self._expr(left), negated, span, none_on_left=False)
        if _is_none_expr(left):
            name = self._slot_read_name(right)
            if name is not None:
                return self._slot_is_unset(name, span, negated)
            return self._compare_none(node, self._expr(right), negated, span, none_on_left=True)
        return self._identity(node, span, negated)

    def _slot_read_name(self, node: ast.expr) -> str | None:
        if not isinstance(node, ast.Name) or node.id in self.locals:
            return None
        slot = self.global_slots.get(node.id)
        if slot is None or not slot.get("active"):
            return None
        return node.id

    def _materialize_tuple(
        self, elems: list[dict[str, Any]], span: dict[str, Any], node: ast.AST | None = None
    ) -> dict[str, Any]:
        typ = self._tuple_type([elem["type"] for elem in elems], node or ast.Pass())
        temp = self._temp("tuple")
        self.locals[temp] = typ
        self.prelude.extend(_tuple_assign(temp, typ, elems, span))
        return _name(temp, typ, span)

    def _temp(self, hint: str) -> str:
        self.temp_n += 1
        return f".ps_{hint}_{self.temp_n}"
