# SPDX-License-Identifier: Apache-2.0
"""Protocol dispatch for the container, iterator and context families (O3b).

The decision of 2026-11-23 (`.agents/plans/decisions/2026-11-23-o3-protocol-dispatch.md`)
resolves a user-class dunder statically against the operand's class: the
operator lowers to a direct call of the first definer of the dunder in the
receiver's static class, or to the family fallback, or to a fence. There is
no vtable, no type word and no runtime table.

This module owns the families O3b names:

* `getitem`, `setitem`, `delitem`, `len` and `in` on a class operand.
* `iter`: the `__iter__` call and the loop, the `iter()` and `next()`
  builtins and the iteration consumers that adopt the shared resolver.
* `with`: `__enter__` and `__exit__` on a class operand.
* `len`: the negative-result check CPython performs.

Every family shares one resolver (`_iter_resolve`) so `for`, `in`, `iter()`
and `next()` agree on what an iterable is.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _SCALAR,
    _compare,
    _const_bool,
    _const_int,
    _const_str,
    _mk_span,
    _name,
    _node_type_kind,
    _raise_value,
    _show,
    _zero,
)
from pyscriptc.lower._expr_compare import _assign_node, _if_node, _unary_node, _while_node
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


class ProtocolsMixin:
    """The container, iterator and context protocol dispatch (O3b).

    The mixin is stateless: every helper takes the receiver's lowered type
    and returns a lowered node (or `None`, which means "this family does
    not resolve here" and the caller keeps its own fence).
    """

    # ------------------------------------------------------------------
    # container protocol: getitem, setitem, delitem, len, in
    # ------------------------------------------------------------------

    def _class_dunder(self, typ: dict[str, Any], name: str) -> str | None:
        """The definer of `name` for a class-typed value, or None.

        A class operand that is an island handle, an optional or a
        synthetic shape has no user dunder to resolve: the family keeps
        its existing behavior there.
        """
        if not typ or typ.get("kind") != "class":
            return None
        if typ.get("island") or typ.get("optional"):
            return None
        cname = str(typ.get("name"))
        if cname not in self.classes:  # type: ignore[attr-defined]
            return None
        return self._definer(cname, name)  # type: ignore[attr-defined]

    def _getitem_dispatch(
        self,
        node: ast.AST,
        value: dict[str, Any],
        index: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`x[i]` on a class operand: the `__getitem__` definer.

        The index expression is checked against the declared parameter
        (`PS1003`) and the result type is the definer's return type.
        """
        typ = value["type"]
        if self._class_dunder(typ, "__getitem__") is None:
            return None
        called = self._dunder_call(node, value, "__getitem__", [index], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        return called

    def _setitem_dispatch(
        self,
        node: ast.AST,
        value: dict[str, Any],
        index: dict[str, Any],
        item: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`x[i] = v` on a class operand: the `__setitem__` definer.

        The definer returns None; the call is a statement, so the value is
        discarded.
        """
        typ = value["type"]
        if self._class_dunder(typ, "__setitem__") is None:
            return None
        called = self._dunder_call(node, value, "__setitem__", [index, item], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        if called["type"]["kind"] != "none":
            self.fail(node, "PS1003", f"{typ['name']}.__setitem__ returns {_show(called['type'])}, not None")  # type: ignore[attr-defined]
        return called

    def _setitem_value_type(self, typ: dict[str, Any], name: str) -> dict[str, Any] | None:
        """The value type a `__setitem__` definer's second parameter takes.

        The setitem call lowers the assigned value against this type, so the
        value is checked where the write happens.
        """
        definer = self._class_dunder(typ, "__setitem__")
        if definer is None:
            return None
        sig = self.classes[definer]["methods"].get("__setitem__")  # type: ignore[attr-defined]
        if sig is None:
            return None
        params = sig[0]
        if len(params) != 3:
            return None
        return params[2]["type"]

    def _delitem_dispatch(
        self,
        node: ast.AST,
        value: dict[str, Any],
        index: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`del x[i]` on a class operand: the `__delitem__` definer."""
        typ = value["type"]
        if self._class_dunder(typ, "__delitem__") is None:
            return None
        called = self._dunder_call(node, value, "__delitem__", [index], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        if called["type"]["kind"] != "none":
            self.fail(node, "PS1003", f"{typ['name']}.__delitem__ returns {_show(called['type'])}, not None")  # type: ignore[attr-defined]
        return called

    def _len_dispatch(
        self,
        node: ast.AST,
        value: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`len(x)` on a class operand: the `__len__` definer and the
        negative-result check CPython performs.

        CPython raises `ValueError("__len__() should return >= 0")` for a
        negative result.
        """
        typ = value["type"]
        if self._class_dunder(typ, "__len__") is None:
            return None
        called = self._dunder_call(node, value, "__len__", [], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        return self._len_nonneg(node, called, span)

    def _len_nonneg(self, node: ast.AST, called: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """The `>= 0` guard on a `__len__` result."""
        int_t: dict[str, Any] = {"kind": "int"}
        pinned = self._pin_operand(called, span)  # type: ignore[attr-defined]
        out = self._temp("len")  # type: ignore[attr-defined]
        self.locals[out] = int_t  # type: ignore[attr-defined]
        self.prelude.append(  # type: ignore[attr-defined]
            _if_node(
                _compare("<", pinned, _const_int(0, span), span),
                [_raise_value(_const_str("__len__() should return >= 0", span), span)],
                [_assign_node(out, pinned, span)],
                span,
            )
        )
        return _name(out, int_t, span)

    def _contains_dispatch(
        self,
        node: ast.AST,
        needle: dict[str, Any],
        hay: dict[str, Any],
        span: dict[str, Any],
        negated: bool,
    ) -> dict[str, Any] | None:
        """`i in x` on a class operand: the `__contains__` definer.

        Without a `__contains__` definer the family falls through to the
        iteration resolver (the element-wise `==` scan). The legacy
        sequence protocol (iteration through `__getitem__`) is a recorded
        non-goal and stays refused.
        """
        typ = hay["type"]
        if self._class_dunder(typ, "__contains__") is None:
            return None
        called = self._dunder_call(node, hay, "__contains__", [needle], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        if negated:
            return _unary_node("not", called, {"kind": "bool"}, span)
        return called

    # ------------------------------------------------------------------
    # iterator protocol: the shared resolver
    # ------------------------------------------------------------------

    def _class_iteration_contains(
        self,
        node: ast.AST,
        needle: dict[str, Any],
        hay: dict[str, Any],
        span: dict[str, Any],
        negated: bool,
    ) -> dict[str, Any] | None:
        """`i in x` on a class without `__contains__`: the iteration scan.

        CPython falls back to the iterator protocol and compares each
        element with `==`. The legacy sequence protocol (iteration through
        `__getitem__`) is a recorded non-goal and stays refused, so the
        fallback needs an `__iter__` definer.

        The scan drives the resolved iterator's `__next__` and stops at the
        `StopIteration` the drain raises, the same shape the `for` loop
        over a `__next__` class uses.
        """
        resolved = self._iter_resolve(node, hay, span)
        if resolved is None:
            return None
        elem = self._iter_elem_type(resolved)
        if elem is None:
            return None
        if _node_type_kind(resolved) != "class":
            # A list, dict, str or bytes result: the materialized list
            # membership test answers the scan.
            return self._materialized_contains(node, needle, resolved, elem, span, negated)
        it_t = resolved["type"]
        itn = self._temp("it")
        self.locals[itn] = it_t
        flag = self._temp("itr")
        self.locals[flag] = {"kind": "bool"}
        found = self._temp("has")
        self.locals[found] = {"kind": "bool"}
        held = self._temp("needle")
        self.locals[held] = needle["type"]
        item = self._temp("item")
        self.locals[item] = elem
        step = self._dunder_call(node, _name(itn, it_t, span), "__next__", [], span)
        if step is None:
            return None
        self.prelude.extend(
            [
                _assign_node(held, needle, span),
                _assign_node(itn, resolved, span),
                _assign_node(flag, _const_bool(False, span), span),
                _assign_node(found, _const_bool(False, span), span),
            ]
        )
        body = [
            _assign_node(item, step, span),
            _if_node(
                self._compare_values(node, "==", _name(item, elem, span), _name(held, needle["type"], span), span),
                [_assign_node(found, _const_bool(True, span), span)],
                [],
                span,
            ),
        ]
        self.prelude.append(
            _while_node(
                _unary_node("not", _name(flag, {"kind": "bool"}, span), {"kind": "bool"}, span),
                [_try_stop_body(body, [_assign_node(flag, _const_bool(True, span), span)], span)],
                span,
            )
        )
        result = _name(found, {"kind": "bool"}, span)
        if negated:
            return _unary_node("not", result, {"kind": "bool"}, span)
        return result

    def _materialized_contains(
        self,
        node: ast.AST,
        needle: dict[str, Any],
        haystack: dict[str, Any],
        elem: dict[str, Any],
        span: dict[str, Any],
        negated: bool,
    ) -> dict[str, Any] | None:
        """The membership test over a materialized list, dict, str or bytes."""
        from pyscriptc.lower._expr_compare import _contains_node

        if _node_type_kind(haystack) == "list":
            if elem["kind"] not in _SCALAR and elem["kind"] != "class":
                return None
            if not types_equal(needle["type"], elem):
                return None
        return _contains_node(haystack, needle, span, negated=negated)

    def _materialize_iterable(
        self,
        node: ast.AST,
        resolved: dict[str, Any],
        elem: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Drain a resolved iterable into a `list[elem]` value.

        The drain drives the iterator's `__next__` and stops at the
        `StopIteration` the drain raises, the same shape the `for` loop
        over a `__next__` class uses. The eager model of the compiler
        applies: the elements materialize before the consumer sees them.
        """
        kind = _node_type_kind(resolved)
        if kind == "list":
            return resolved
        if kind == "dict":
            keys = self._abi_call(
                "builtins", "dict.keys", [resolved], {"kind": "list", "elem": elem}, "ps_dict_keys", span
            )
            return keys
        if kind != "class":
            return None
        it_t = resolved["type"]
        itn = self._temp("it")
        self.locals[itn] = it_t
        flag = self._temp("itr")
        self.locals[flag] = {"kind": "bool"}
        dest = self._temp("mat")
        list_t: dict[str, Any] = {"kind": "list", "elem": elem}
        self.locals[dest] = list_t
        item = self._temp("item")
        self.locals[item] = elem
        step = self._dunder_call(node, _name(itn, it_t, span), "__next__", [], span)  # type: ignore[attr-defined]
        if step is None:
            return None
        self.prelude.extend(
            [
                _assign_node(itn, resolved, span),
                _assign_node(flag, _const_bool(False, span), span),
                _assign_node(dest, _zero(list_t, span), span),
            ]
        )
        push = node_to_dict(
            _ir.expr_stmt(
                _ir.call(
                    _ir.module_callee("builtins", "list.append"),
                    [dict_to_node(_name(dest, list_t, span)), dict_to_node(_name(item, elem, span))],
                    dict_to_type({"kind": "none"}),
                    _mk_span(span),
                    "ps_list_push",
                    "",
                    -1,
                ),
                _mk_span(span),
            )
        )
        body = [_assign_node(item, step, span), push]
        self.prelude.append(
            _while_node(
                _unary_node("not", _name(flag, {"kind": "bool"}, span), {"kind": "bool"}, span),
                [_try_stop_body(body, [_assign_node(flag, _const_bool(True, span), span)], span)],
                span,
            )
        )
        return _name(dest, list_t, span)

    def _class_iterable_value(
        self,
        node: ast.AST,
        value: dict[str, Any],
        span: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """A class operand as a materialized list, or None.

        The pair is the list value and its element type. A class operand
        without an `__iter__` definer is not iterable: the caller keeps
        its own fence.
        """
        resolved = self._iter_resolve(node, value, span)
        if resolved is None:
            return None
        elem = self._iter_elem_type(resolved)
        if elem is None:
            return None
        materialized = self._materialize_iterable(node, resolved, elem, span)
        if materialized is None:
            return None
        return materialized, elem

    def _iter_resolve(
        self,
        node: ast.AST,
        iterable: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The shared iteration resolver: the `__iter__` call and its result.

        The result's own static type decides what the loop does: a lowered
        class with `__next__` drives the loop through `__next__`; a list,
        dict, set, str, bytes or range goes through the existing loop for
        that type; anything else fences (`PS2002`).

        Returns None when the class has no `__iter__` definer, which means
        "this is not an iterable class": the caller keeps its own fence.
        """
        typ = iterable["type"]
        if self._class_dunder(typ, "__iter__") is None:
            return None
        called = self._dunder_call(node, iterable, "__iter__", [], span)  # type: ignore[attr-defined]
        if called is None:
            return None
        return called

    def _iter_elem_type(self, resolved: dict[str, Any]) -> dict[str, Any] | None:
        """The element type of a resolved iterator, or None.

        A lowered class with `__next__` yields the `__next__` definer's
        return type; a list, dict, set, str, bytes or range yields its own
        element type.
        """
        typ = resolved["type"]
        kind = _node_type_kind(resolved)
        if kind == "class":
            cname = str(typ.get("name"))
            info = self.classes.get(cname, {})  # type: ignore[attr-defined]
            if info.get("iterator"):
                elem = typ.get("elem")
                return elem if elem is not None else None
            nextdef = self._definer(cname, "__next__")  # type: ignore[attr-defined]
            if nextdef is None:
                return None
            sig = self.classes[nextdef]["methods"].get("__next__")  # type: ignore[attr-defined]
            if sig is None:
                return None
            return sig[1]
        if kind == "list":
            return typ["elem"]
        if kind == "dict":
            return typ["key"]
        if kind == "str":
            return {"kind": "str"}
        if kind == "bytes":
            return {"kind": "int"}
        return None

    # ------------------------------------------------------------------
    # the with family: the __exit__ carrier erasure
    # ------------------------------------------------------------------

    def _exit_carrier_types(self, fn: ast.FunctionDef, args: list[ast.arg]) -> list[dict[str, Any] | None] | None:
        """The carrier types an `__exit__` signature's parameters erase to.

        The `with` family passes three values to `__exit__`: the
        exception's class name (a string), the exception itself and a
        traceback stand-in that is always empty. The CPython spellings a
        user writes (`type[BaseException] | None`, `BaseException | None`,
        `TracebackType | None`) name types the compiler does not lower, so
        the erasure table maps them onto the carrier slots.

        Returns None for any other method (and for an `__exit__` whose
        parameters are not in the recognized spellings), which keeps the
        ordinary annotation path.
        """
        if fn.name != "__exit__":
            return None
        if len(args) != 3:
            return None
        out: list[dict[str, Any] | None] = []
        for arg in args:
            if arg.annotation is None:
                return None
            erased = _exit_annotation(arg.annotation)
            if erased is None:
                return None
            slot = erased["carrier"]
            elem: dict[str, Any] = {"kind": "exc"} if slot == "exc_value" else {"kind": "str"}
            out.append(self._opt_type(elem, fn) if erased["optional"] else elem)
        return out


def _try_stop_body(
    body: list[dict[str, Any]], handler_body: list[dict[str, Any]], span: dict[str, Any]
) -> dict[str, Any]:
    """The try/except-StopIteration node the drain loops spell."""
    return node_to_dict(
        _ir.try_stmt(
            [dict_to_node(stmt) for stmt in body],
            [_ir.Handler(["StopIteration"], [8], [dict_to_node(stmt) for stmt in handler_body])],
            _mk_span(span),
        )
    )


# ----------------------------------------------------------------------
# the `with` family: the __exit__ carrier erasure
# ----------------------------------------------------------------------

# The CPython spellings a `__exit__` signature uses, mapped to the carrier
# slot each one fills. The carrier passes the exception's class name as a
# string, the exception itself and a traceback stand-in that is always
# empty (divergence: the class name is a string, not the class object).
#
# The slot order is the carrier's: (class name, exception, traceback).
_EXIT_CARRIER_SLOTS = ("exc_type", "exc_value", "traceback")


def _exit_annotation(node: ast.AST) -> dict[str, Any] | None:
    """The carrier type one `__exit__` parameter annotation erases to.

    The recognized spellings are the ones CPython code uses:

    * `type[BaseException] | None` and `type[BaseException]` fill the
      class-name slot (`Optional[str]`).
    * `BaseException | None` and `BaseException` fill the exception slot
      (`Optional[Exception]`).
    * `TracebackType | None` and `TracebackType` fill the traceback slot
      (`Optional[str]`).

    The optionality of the arm is kept: a non-optional annotation keeps
    the plain carrier type, so the parameter is not a tagged optional.

    Returns None for any other spelling, which keeps the ordinary
    annotation path (and its own fence).
    """
    optional = False
    arms: list[ast.expr] = []
    pending = [node]
    while pending:
        cur = pending.pop(0)
        if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.BitOr):
            pending.insert(0, cur.right)
            pending.insert(0, cur.left)
            continue
        arms.append(cur)
    rest = [arm for arm in arms if not (isinstance(arm, ast.Constant) and arm.value is None)]
    if len(rest) != len(arms):
        optional = True
    if len(rest) != 1:
        return None
    inner = rest[0]
    if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name) and inner.value.id == "type":
        elts = inner.slice.elts if isinstance(inner.slice, ast.Tuple) else [inner.slice]
        if len(elts) != 1:
            return None
        if not (isinstance(elts[0], ast.Name) and elts[0].id == "BaseException"):
            return None
        return {"carrier": "exc_type", "optional": optional}
    if isinstance(inner, ast.Name):
        if inner.id == "BaseException":
            return {"carrier": "exc_value", "optional": optional}
        if inner.id == "TracebackType":
            return {"carrier": "traceback", "optional": optional}
    return None


def _exit_returns_bool(ret: dict[str, Any]) -> bool:
    """True when an `__exit__` return type is one the `with` lowering accepts.

    `bool` suppresses by value, `bool | None` keeps the tagged optional
    (None never suppresses), and a plain `None` return never suppresses.
    """
    kind = ret.get("kind")
    if kind == "bool":
        return True
    if kind == "none":
        return True
    return bool(ret.get("optional")) and ret.get("elem", {}).get("kind") == "bool"
