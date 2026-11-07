"""Attribute-call lowering, split verbatim from `lower._calls`.

`CallAttrMixin` holds the attribute-call dispatcher (`_call_attr`) plus the
handlers it owns: the `super()` call, the module and package call forms, the
class-alias and named-class calls, the container method families (`list`,
`set`, `dict.keys`), the `str`/`bytes` method rows, the hash, pattern and
match receivers, the `Path` and generator families and the synthetic-class
dispatch. `CallsMixin` composes it, so every method stays reachable as
`self._call_attr(...)` and friends. The module-level helper
`_const_dict_keys` moved with the family so nothing here back-imports
`lower._calls`. Every body below is the code that lived in `lower._calls`,
unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc._irnode import t_leaf
from pyscriptc.builtins import EXCEPTIONS, method_spec
from pyscriptc.coro import DONE, STATE
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    Fence,
    _any_accepts,
    _assign,
    _attr,
    _const_bool,
    _const_int,
    _const_none,
    _const_str,
    _dotted,
    _mk_span,
    _mk_type,
    _name,
    _show,
    _type_kind,
)
from pyscriptc.lower._stdlib_shared import _mod_call, _synth_module
from pyscriptc.reflection import inspect_note, kwargs_call_note, typing_note
from pyscriptc.restlower import (
    _lock_method,
    _mk_iter,
    _queue_method,
    _thread_method,
)
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.restlower._argparse import _add_argument, _parse_args
from pyscriptc.restlower._asyncio import _task_method
from pyscriptc.restlower._asyncio_sync import _prim_method
from pyscriptc.tirserde import dict_to_node, node_to_dict


def _get_expect(expect: dict[str, Any] | None, value_type: dict[str, Any]) -> dict[str, Any]:
    """The expectation `dict.get`'s desugared read proves an `Any` element with.

    A plain `d[k]` subscript proves an unresolved element from the read
    site's expected type (the annotation, the call parameter). `d.get(k, v)`
    lowers to the same subscript inside an `IfExp`, so the call site's
    concrete expectation is the same proof; without one the dict's own
    value type (`any` for `dict[K, Any]`) stands and PS1107 fences the read.
    """
    if _type_kind(value_type) != "any":
        return value_type
    if expect is None:
        return value_type
    if _type_kind(expect) == "any":
        return value_type
    optional: bool = expect.get("optional", False)
    if optional:
        return value_type
    return expect


def _contains_node(hay: dict[str, Any], needle: dict[str, Any], negated: bool, span: dict[str, Any]) -> dict[str, Any]:
    """The membership node the dict IR spells."""
    return node_to_dict(_ir.contains(dict_to_node(hay), dict_to_node(needle), t_leaf("bool"), _mk_span(span), negated))


def _if_node(
    cond: dict[str, Any],
    then: list[dict[str, Any]],
    else_: list[dict[str, Any]],
    span: dict[str, Any],
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


def _setitem_node(
    container: dict[str, Any], index: dict[str, Any], value: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The setitem statement node."""
    return node_to_dict(
        _ir.setitem_stmt(dict_to_node(container), dict_to_node(index), dict_to_node(value), _mk_span(span))
    )


def _subscript_node(
    value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The subscript read node."""
    return node_to_dict(_ir.subscript(dict_to_node(value), dict_to_node(index), _mk_type(typ), _mk_span(span)))


def _raise_keyerror(span: dict[str, Any]) -> dict[str, Any]:
    """The bare KeyError raise node."""
    return node_to_dict(_ir.raise_stmt("KeyError", _mk_span(span), -1))


def _expr_node(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The expression statement node."""
    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _list_node(elems: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The list literal node."""
    return node_to_dict(_ir.list_lit([dict_to_node(elem) for elem in elems], _mk_type(typ), _mk_span(span)))


def _function_call_node(
    fname: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The call to a lowered class method."""
    return node_to_dict(
        _ir.call(
            _ir.function_callee(fname),
            [dict_to_node(arg) for arg in args],
            _mk_type(typ),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _opt_fill(dest: str, opt_t: dict[str, Any], got: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
    """The hit-then-fill setattr pair: the tag first, then the value."""
    target = _name(dest, opt_t, span)
    return [
        node_to_dict(
            _ir.setattr_stmt(dict_to_node(target), "_tag", dict_to_node(_const_bool(True, span)), _mk_span(span))
        ),
        node_to_dict(_ir.setattr_stmt(dict_to_node(target), "_val", dict_to_node(got), _mk_span(span))),
    ]


class CallAttrMixin:
    def _call_attr(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        if (
            isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "super"
        ):
            return self._super_call(node, span)
        if node.func.attr == "get" and self._is_environ(node.func.value):
            # `os.environ.get(k, default)` is always a str. The 1-arg form
            # returns the tagged `str | None`, like `os.getenv(k)`.
            self._reject_keywords(node)
            if len(node.args) not in {1, 2}:
                self.fail(node, "PS2003", "os.environ.get expects 1-2 argument(s)")
            args = [self._expr(arg) for arg in node.args]
            for arg in args:
                if arg["type"]["kind"] != "str":
                    self.fail(node, "PS1003", "os.environ.get takes str arguments")
            if len(args) == 2:
                return self._abi_call("os", "environ.get", args, {"kind": "str"}, "ps_os_getenv_or", span)
            return self._opt_env_get(node, args[0], span)
        resolved = self._resolve_module(node.func)
        if resolved is not None:
            dotted, module, member, status = resolved
            # boxed:begin
            if status == "island":
                return self._island_call(node, span, module, member, expect)
            # boxed:end
            if status == "erased":
                note = typing_note(member)
                if note is not None:
                    self.fail(node, "PS2002", note)
                self.fail(node, "PS2002", f"{dotted} is a typing name and is not a value")
            if status == "pkg":
                item = self._pkg_item(node, module, member)
                if item["kind"] == "class":
                    # `pkg.mod.Class(...)` spells the class through the module,
                    # so no from-import ever installed its export: install it
                    # here the way `_bind_one_export` does for the named form.
                    name = str(item["name"])
                    pending: list[Fence] = []
                    self._install_class_export(name, item, node, pending)
                    if pending:
                        raise pending[0]
                    return self._construct(node, span, name)
                if item["kind"] != "func":
                    self.fail(node, "PS2002", f"{dotted} is not a function")
                return self._linked_call(node, span, item, dotted, expect)
            if status == "queued":
                self._queued(node, dotted)
            if status != "lowered":
                note = inspect_note(member) if module == "inspect" else None
                if note is not None:
                    self.fail(node, "PS2001", note)
                self.fail(node, "PS2001", f"{dotted} is not in the lowered subset of {module}")
            return self._module_call(node, span, module, member, expect)
        if isinstance(node.func.value, ast.Name):
            bound = self.bindings.get(node.func.value.id)
            # boxed:begin
            if bound is not None and bound.kind == "island":
                return self._island_call(node, span, bound.module, node.func.attr, expect)
            if bound is not None and bound.kind == "island_member":
                target_mod = f"{bound.module}.{bound.attr}" if bound.attr else bound.module
                return self._island_call(node, span, target_mod, node.func.attr, expect)
            # boxed:end
            if bound is not None and bound.kind == "module" and self._ffi_module(bound.module) is not None:
                return self._ffi_call(node, span, bound.module, node.func.attr)
        named = None
        if isinstance(node.func.value, ast.Name):
            named = self.class_aliases.get(node.func.value.id, node.func.value.id)
        if named is not None and named in self.classes:
            return self._named_class_call(node, span, named)
        receiver = self._expr(node.func.value)
        # boxed:begin
        if receiver["type"].get("island"):
            return self._island_method_call(node, span, receiver, expect)
        # boxed:end
        if receiver["type"]["kind"] == "class" and receiver["type"].get("name") == "ArgParser":
            # argparse parser methods need the lowerer state, so they have a
            # handler instead of a fixed method row.
            if node.func.attr == "add_argument":
                return _add_argument(self, node, span, receiver)
            if node.func.attr == "parse_args":
                return _parse_args(self, node, span, receiver)
            self.fail(node, "PS2003", f"ArgParser.{node.func.attr} is not lowered")
        if receiver["type"]["kind"] == "class" and receiver["type"].get("name") == "Path":
            if node.func.attr in {"__enter__", "__exit__"}:
                return self._path_context(node, span, receiver)
        if receiver["type"].get("kind") == "class" and node.func.attr == "throw":
            frame = self.classes.get(str(receiver["type"].get("name")))
            if frame is not None and frame.get("coro") == "generator":
                return self._gen_throw(node, span, receiver)
        if receiver["type"]["kind"] == "list" and receiver["type"].get("set"):
            return self._set_method(node, span, receiver)
        if receiver["type"]["kind"] == "list":
            return self._list_method(node, span, receiver)
        kind = receiver["type"]["kind"]
        if kind in {"str", "bytes"}:
            attr = node.func.attr
            if attr in {"startswith", "endswith"} and len(node.args) == 1 and isinstance(node.args[0], ast.Tuple):
                # s.startswith((a, b)) is a or-chain of scalar calls; the empty
                # tuple is constant False per CPython.
                elts = node.args[0].elts
                if not elts:
                    return _const_bool(False, span)
                rewritten = ast.BoolOp(
                    op=ast.Or(),
                    values=[ast.Call(func=node.func, args=[elt], keywords=[]) for elt in elts],
                )
                for sub in ast.walk(rewritten):
                    sub.lineno, sub.col_offset = node.lineno, node.col_offset
                    sub.end_lineno, sub.end_col_offset = node.end_lineno, node.end_col_offset
                return self._expr(rewritten, expect)
            if attr in {"partition", "rpartition"}:
                # The result is a 3-tuple of the receiver's kind; it materializes
                # as the synthetic tuple_3 class, like zip/enumerate pairs.
                self._reject_keywords(node)
                if len(node.args) != 1:
                    self.fail(node, "PS2003", f"{kind}.{attr} expects 1 argument(s)")
                sep = self._expr(node.args[0])
                self._expect(node, sep["type"], {"kind": kind}, f"{kind}.{attr} separator")
                typ = self._tuple_type([{"kind": kind}] * 3, node)
                return node_to_dict(
                    _ir.call(
                        _ir.method_callee(kind, attr, str(typ["name"])),
                        [dict_to_node(receiver), dict_to_node(sep)],
                        _mk_type(typ),
                        _mk_span(span),
                        "",
                        "",
                        -1,
                    )
                )
            if attr == "format" and kind == "str":
                # Same engine as `%`; arg layout is [kwarg names, positionals, kwarg values].
                if any(keyword.arg is None for keyword in node.keywords):
                    self.fail(node, "PS2003", "str.format **kwargs are not lowered")
                names = [_const_str(str(keyword.arg), span) for keyword in node.keywords]
                tail = [self._expr(arg) for arg in node.args]
                tail += [self._expr(keyword.value) for keyword in node.keywords]
                for arg in tail:
                    a_type = arg["type"]
                    if a_type["kind"] == "dict":
                        if a_type["key"]["kind"] not in {"int", "str"} or a_type["value"]["kind"] not in {
                            "int",
                            "float",
                            "str",
                            "bytes",
                        }:
                            self.fail(node, "PS2002", "str.format needs scalar dict keys and values")
                    elif a_type["kind"] not in {"int", "float", "str", "bool", "bytes"}:
                        self.fail(node, "PS2004", f"str.format of {_show(arg['type'])} is not lowered")
                expr = self._abi_call(
                    "builtins", "format", [receiver, *names, *tail], {"kind": "str"}, "ps_str_format", span
                )
                expr["format_style"] = "dot"
                expr["format_kwnames"] = len(node.keywords)
                return expr
            self._reject_keywords(node)
            args = [self._expr(arg) for arg in node.args]
            if attr in {"encode", "decode"} and args:
                enc = args[0]
                value = str(enc.get("value", "")).lower().replace("-", "").replace("_", "")
                if enc["kind"] != "const" or value != "utf8":
                    self.fail(node, "PS2003", f"{kind}.{attr} lowers the 'utf-8' encoding only")
            spec = method_spec(receiver["type"], node.func.attr, [arg["type"] for arg in args])
            if spec is None:
                self.fail(node, "PS2001", f"call of attribute is not lowered ({_dotted(node.func)})")
            return node_to_dict(
                _ir.call(
                    _ir.method_callee(kind, node.func.attr),
                    [dict_to_node(receiver), *[dict_to_node(arg) for arg in args]],
                    _mk_type(spec),
                    _mk_span(span),
                    "",
                    "",
                    -1,
                )
            )
        if kind == "class" and receiver["type"]["name"] == "Hash":
            return self._hash_method(node, span, receiver)
        if kind == "class" and receiver["type"]["name"] == "Pattern":
            return self._pattern_method(node, span, receiver)
        if kind == "class" and receiver["type"]["name"] == "Match":
            return self._match_method(node, span, receiver)
        if kind == "class" and receiver["type"]["name"] == "Lock":
            return _lock_method(self, node, span, receiver)
        if kind == "class" and receiver["type"]["name"] in {"Thread", "Event", "Condition", "Semaphore"}:
            return _thread_method(self, node, span, receiver)
        if kind == "class" and receiver["type"]["name"] == "Task":
            return _task_method(self, node, span, receiver)
        if kind == "class" and receiver["type"]["name"] in {
            "AsyncLock",
            "AsyncEvent",
            "AsyncSemaphore",
            "AsyncQueue",
        }:
            return _prim_method(self, node, span, receiver)
        if kind == "class" and receiver["type"]["name"] == "Queue":
            return _queue_method(self, node, span, receiver)
        if kind == "class" and synth_method(receiver["type"]["name"], node.func.attr) is not None:
            return self._synth_call(node, span, receiver)
        if kind == "dict" and node.func.attr == "keys":
            return self._dict_keys(node, span, receiver)
        if kind == "dict" and node.func.attr == "get" and len(node.args) in {1, 2} and not node.keywords:
            # `d.get(k, default)` is `d[k] if k in d else default`. The 1-arg
            # form's `else None` arm unifies into the tagged `V | None`.
            key = self._expr(node.args[0])
            self._expect(node, key["type"], receiver["type"]["key"], "dict.get key")
            d_t = self._temp("getd").replace(".", "_")
            k_t = self._temp("getk").replace(".", "_")
            self.locals[d_t] = receiver["type"]
            self.locals[k_t] = receiver["type"]["key"]
            for t, v in ((d_t, receiver), (k_t, key)):
                self.prelude.append(_assign(t, v, span))
            ifexp = ast.IfExp(
                test=ast.Compare(
                    left=ast.Name(id=k_t, ctx=ast.Load()),
                    ops=[ast.In()],
                    comparators=[ast.Name(id=d_t, ctx=ast.Load())],
                ),
                body=ast.Subscript(
                    value=ast.Name(id=d_t, ctx=ast.Load()),
                    slice=ast.Name(id=k_t, ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
                orelse=node.args[1] if len(node.args) == 2 else ast.Constant(value=None),
            )
            for sub in ast.walk(ifexp):
                sub.lineno, sub.col_offset = node.lineno, node.col_offset
                sub.end_lineno, sub.end_col_offset = node.end_lineno, node.end_col_offset
            if len(node.args) == 2:
                value = self._expr(ifexp, _get_expect(expect, receiver["type"]["value"]))
                self._expect(node, value["type"], receiver["type"]["value"], "dict.get default")
                return value
            return self._expr(ifexp)
        if kind == "dict" and receiver["type"].get("ordered") and node.func.attr in {"move_to_end", "popitem"}:
            self.fail(node, "PS2001", f"OrderedDict.{node.func.attr} is not lowered")
        if kind == "dict" and node.func.attr in {"values", "items", "pop", "setdefault", "update", "clear"}:
            attr = node.func.attr
            self._reject_keywords(node)
            dtyp = receiver["type"]
            key_t, val_t = dtyp["key"], dtyp["value"]
            if attr in {"values", "items"}:
                if node.args:
                    self.fail(node, "PS2003", f"dict.{attr} expects 0 argument(s)")
                if key_t["kind"] not in {"int", "str"}:
                    self.fail(node, "PS2002", "dict keys are int or str")
                if attr == "values":
                    return self._abi_call(
                        "builtins", "dict.values", [receiver], {"kind": "list", "elem": val_t}, "ps_dict_values", span
                    )
                elem = self._tuple_type([key_t, val_t], node)
                return self._abi_call(
                    "builtins", "dict.items", [receiver], {"kind": "list", "elem": elem}, "ps_dict_items", span
                )
            if attr in {"update", "clear"}:
                # Both mutate the map in place; the abi call carries the receiver
                # pointer, so a name is not required.
                if attr == "clear":
                    if node.args:
                        self.fail(node, "PS2003", "dict.clear expects 0 argument(s)")
                    call = self._abi_call("builtins", "dict.clear", [receiver], {"kind": "none"}, "ps_map_clear", span)
                else:
                    if len(node.args) != 1:
                        self.fail(node, "PS2003", "dict.update expects 1 argument(s)")
                    other = self._expr(node.args[0])
                    if other["type"]["kind"] != "dict":
                        self.fail(node, "PS1003", "dict.update takes a dict")
                    self._expect(node, other["type"]["key"], key_t, "dict.update key")
                    self._expect(node, other["type"]["value"], val_t, "dict.update value")
                    # Counter.update adds counts; dict.update replaces them.
                    symbol = "ps_counter_update" if dtyp.get("counter") else "ps_map_update"
                    call = self._abi_call("builtins", "dict.update", [receiver, other], {"kind": "none"}, symbol, span)
                self.prelude.append(_expr_node(call, span))
                return _const_none(span)
            # pop and setdefault are statements: the dict is bound to a temp,
            # the key checked with `in`, and the value bound before a ps_map_del
            # or a setitem. `del` shares the ps_map_del call.
            if attr == "setdefault" and len(node.args) != 2:
                self.fail(node, "PS2003", "dict.setdefault expects 2 argument(s)")
            if attr == "pop" and len(node.args) not in {1, 2}:
                self.fail(node, "PS2003", "dict.pop expects 1 or 2 argument(s)")
            d_t = self._temp("popd").replace(".", "_")
            k_t = self._temp("popk").replace(".", "_")
            key = self._expr(node.args[0])
            self._expect(node, key["type"], key_t, f"dict.{attr} key")
            self.locals[d_t] = dtyp
            self.locals[k_t] = key_t
            for t, v in ((d_t, receiver), (k_t, key)):
                self.prelude.append(_assign(t, v, span))
            if attr == "setdefault":
                val = self._expr(node.args[1], val_t)
                self._expect(node, val["type"], val_t, "dict.setdefault value")
                cond = _contains_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), True, span)
                put = _setitem_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), val, span)
                self.prelude.append(_if_node(cond, [put], [], span))
                return _subscript_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), val_t, span)
            v_t = self._temp("popv").replace(".", "_")
            self.locals[v_t] = val_t
            getitem = _subscript_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), val_t, span)
            delitem = self._abi_call(
                "builtins",
                "dict.delitem",
                [_name(d_t, dtyp, span), _name(k_t, key_t, span)],
                {"kind": "none"},
                "ps_map_del",
                span,
            )
            then = [_assign(v_t, getitem, span), _expr_node(delitem, span)]
            if len(node.args) == 2:
                fallback = self._expr(node.args[1], val_t)
                self._expect(node, fallback["type"], val_t, "dict.pop default")
                other = [_assign(v_t, fallback, span)]
            else:
                other = [_raise_keyerror(span)]
            cond = _contains_node(_name(d_t, dtyp, span), _name(k_t, key_t, span), False, span)
            self.prelude.append(_if_node(cond, then, other, span))
            return _name(v_t, val_t, span)
        if kind == "class":
            return self._method_call(node, span, receiver)
        self.fail(node, "PS2001", f"call of attribute is not lowered ({_dotted(node.func)})")
        raise AssertionError("unreachable")

    def _super_call(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        """`super().method(args)` and `super(C, self).method(args)`: resolution
        starts one MRO step past the start class — the class the enclosing
        method text lives in (CPython's __class__ cell) for the zero-arg
        form, the explicit first argument for the two-arg form. The receiver
        is still self, so the emitted symbol is the one valid for
        self's static layout — the same `_emit_owner` rule every call uses.

        `super().__init__` on an exception class lowers to the runtime's
        own `__init__` (the record's message), not a class method: the
        builtin base has no lowered body to dispatch to.
        """
        assert isinstance(node.func, ast.Attribute)
        super_call = node.func.value
        assert isinstance(super_call, ast.Call)
        class_name, _, method_name = (self.function or "").rpartition(".")
        info = self.classes.get(class_name)
        owner = info["method_owner"].get(method_name) if info is not None else None
        self_t = self.locals.get("self", {}).get("name") if owner is not None else None
        if owner is None or self_t is None:
            self.fail(node, "PS2002", "super is not lowered")
        mro = self.classes[self_t].get("mro", [self_t])
        if owner not in mro:
            self.fail(node, "PS2002", "super is not lowered")
        # Two-argument form `super(C, self)`: the resolution start is C in
        # self's MRO instead of the class the method text lives in. C must
        # sit in self's MRO; the receiver must be the `self` local.
        if super_call.args or super_call.keywords:
            if super_call.keywords or len(super_call.args) != 2:
                self.fail(super_call, "PS2002", "super() takes zero or two positional arguments")
            start = super_call.args[0]
            recv = super_call.args[1]
            if not isinstance(start, ast.Name) or not isinstance(recv, ast.Name):
                self.fail(super_call, "PS2002", "super(C, self) takes a class and the self local")
            if recv.id != "self" or start.id not in mro:
                self.fail(super_call, "PS2002", "super(C, self) needs a base of the receiver's class")
            owner = start.id
        method = node.func.attr
        ancestors = mro[mro.index(owner) + 1 :]
        # The exception path: an ancestor off the class table that is a
        # builtin exception (`Exception`, `ValueError`, ...). The builtin
        # base has no lowered body, so `super().__init__(msg)` is the
        # class's message: the raise site computes it (a raise does not
        # call `__init__`, divergence 9), and the call itself is a no-op
        # here. The message expression is only checked for shape here; the
        # raise site evaluates it.
        builtin_ancestor = next((name for name in ancestors if name in EXCEPTIONS), None)
        if method == "__init__" and builtin_ancestor is not None and builtin_ancestor not in self.classes:
            if self.classes[self_t].get("exception") is None:
                self.fail(node, "PS2001", f"{self_t}.__init__ is not a method")
            # The builtin base takes the message and nothing else. A super
            # call that lands on a user exception base keeps its own
            # arity: that base's `__init__` takes it.
            if len(ancestors) == 1 and len(node.args) > 1:
                self.fail(node, "PS2003", "exception takes one scalar argument")
            return _const_none(span)
        ancestor = next((name for name in ancestors if method in self.classes.get(name, {}).get("methods", {})), None)
        if ancestor is None:
            self.fail(node, "PS2001", f"{self_t}.{method} is not a method")
        sig = self.classes[ancestor]["methods"][method]
        if self.classes[ancestor]["method_kind"].get(method, "method") != "method":
            self.fail(node, "PS2001", f"{self_t}.{method} is not a method")
        params = [param for param in sig[0] if param["name"] != "self"]
        args = [_name("self", self.locals["self"], span), *self._fill_args(node, params, f"{self_t}.{method}")]
        emit = next(
            (
                name
                for name in ancestors
                if method in self.classes.get(name, {}).get("methods", {})
                and (name == ancestor or ancestor not in self.classes[name].get("chain", {name}))
            ),
            ancestor,
        )
        return _function_call_node(f"cls.{emit}.{method}", args, sig[1], span)

    def _opt_env_get(self, node: ast.Call, key: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """`os.getenv(k)` / `os.environ.get(k)`: the tagged `str | None`.

        The cell starts untagged; only when ps_env_has reports the key is it
        filled with ps_os_getenv's str.
        """
        opt_t = self._opt_type({"kind": "str"}, node)
        res = self._opt_box(None, opt_t, span)
        dest = res["id"]
        has = self._abi_call("os", "environ.contains", [key], {"kind": "bool"}, "ps_env_has", span)
        got = self._abi_call("os", "environ.get", [key], {"kind": "str"}, "ps_os_getenv", span)
        self.prelude.append(_if_node(has, _opt_fill(dest, opt_t, got, span), [], span))
        return res

    def _named_class_call(self, node: ast.Call, span: dict[str, Any], class_name: str) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        info = self.classes[class_name]
        if info.get("enum"):
            self.fail(node, "PS2002", f"{class_name} is not callable")
        method = node.func.attr
        kind = info.get("method_kind", {}).get(method)
        sig = info["methods"].get(method)
        if kind == "property":
            self.fail(node, "PS2002", "a property is not called")
        if kind not in {"static", "class"} or sig is None:
            self.fail(node, "PS2002", f"{class_name}.{method} needs an instance")
        owner = info.get("method_owner", {}).get(method, class_name)
        args = self._fill_args(node, sig[0], f"{class_name}.{method}")
        return _function_call_node(f"cls.{owner}.{method}", args, sig[1], span)

    def _method_call(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        class_name = receiver["type"]["name"]
        method = node.func.attr
        if class_name not in self.classes:
            self.fail(node, "PS2002", f"{class_name} did not lower")
        info = self.classes[class_name]
        sig = info["methods"].get(method)
        kind = info.get("method_kind", {}).get(method, "method")
        if info.get("protocol") and kind == "method" and self._concrete_protocol_impl(class_name, method) is not None:
            # Issue #801: method calls lower statically (`cls.{owner}.{method}`),
            # so a Protocol-typed receiver dispatches to the Protocol body even
            # when the value's real class overrides the method: silent wrong
            # answer. Fence until dispatch through the concrete record exists.
            self.fail(
                node,
                "PS2001",
                f"a Protocol-typed receiver dispatches statically; {class_name}.{method} "
                "has no virtual call on a concrete subclass (use the concrete type)",
            )
        if sig is None or method == "__init__" or kind == "property":
            if kind == "property":
                self.fail(node, "PS2002", "a property is not called")
            if info.get("protocol") and self._concrete_protocol_impl(class_name, method) is not None:
                # Issue #801: declaration-only Protocol members skip class
                # lowering, so the receiver resolves to no body at all while a
                # concrete override exists. Fence instead of a bare "not a
                # method": the same dispatch gap.
                self.fail(
                    node,
                    "PS2001",
                    f"a Protocol-typed receiver dispatches statically; {class_name}.{method} "
                    "has no virtual call on a concrete subclass (use the concrete type)",
                )
            self.fail(node, "PS2001", f"{class_name}.{method} is not a method")
        if kind in {"static", "class"}:
            owner = info.get("method_owner", {}).get(method, class_name)
            args = self._fill_args(node, sig[0], f"{class_name}.{method}")
        else:
            owner = self._emit_owner(class_name, method)
            params = [param for param in sig[0] if param["name"] != "self"]
            args = [receiver, *self._fill_args(node, params, f"{class_name}.{method}")]
        return _function_call_node(f"cls.{owner}.{method}", args, sig[1], span)

    def _concrete_protocol_impl(self, protocol_name: str, method: str) -> str | None:
        """A subclass of `protocol_name` that owns its own `method` body.

        Protocol-typed method calls dispatch statically (issue #801): they
        always emit `cls.{protocol}.{method}`, so a concrete override on the
        value's real class never runs. This probe tells the fence whether the
        misdispatch is reachable.
        """
        for name, info in self.classes.items():
            if name == protocol_name or info.get("protocol"):
                continue
            if info.get("method_owner", {}).get(method) == name and self._is_subclass_name(name, protocol_name):
                return name
        return None

    def _synth_call(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        class_name = receiver["type"]["name"]
        if class_name == "Path" and node.func.attr == "read_text" and node.keywords:
            return self._path_read_text(node, span, receiver)
        if class_name == "Path" and node.func.attr in {"iterdir", "glob"}:
            return self._path_scan(node, span, receiver)
        if class_name == "Path" and node.func.attr == "mkdir":
            return self._path_mkdir(node, span, receiver)
        self._reject_keywords(node)
        spec = synth_method(class_name, node.func.attr)
        if spec is None:
            self.fail(node, "PS2001", f"{class_name}.{node.func.attr} is not lowered")
        params = spec["params"]
        if len(node.args) != len(params):
            self.fail(node, "PS2003", f"{class_name}.{node.func.attr} expects {len(params)} argument(s)")
        args = [receiver]
        ret = spec["return"]
        if ret == "elem":
            ret = receiver["type"]["elem"]
        for arg_node, want in zip(node.args, params, strict=True):
            arg = self._expr(arg_node)
            expected = receiver["type"]["elem"] if want == "elem" else want
            if not types_equal(arg["type"], expected):
                self.fail(node, "PS1003", f"{class_name}.{node.func.attr} argument has the wrong type")
            args.append(arg)
        module = _synth_module(class_name)
        return self._abi_call(module, f"{class_name}.{node.func.attr}", args, ret, spec["symbol"], span)

    def _path_read_text(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        if node.args:
            self.fail(node, "PS2003", "Path.read_text expects 0 argument(s)")
        if len(node.keywords) != 1 or node.keywords[0].arg != "encoding":
            self.fail(node, "PS2003", "Path.read_text accepts only encoding")
        encoding = node.keywords[0].value
        allowed = {"utf-8", "utf8", "ascii", "latin-1", "latin1"}
        if not isinstance(encoding, ast.Constant) or encoding.value not in allowed:
            self.fail(node, "PS2001", "Path.read_text encoding is utf-8, ascii, or latin-1")
        spec = synth_method("Path", "read_text")
        if spec is None:
            self.fail(node, "PS2001", "Path.read_text is not lowered")
        return self._abi_call("pathlib", "Path.read_text", [receiver], {"kind": "str"}, spec["symbol"], span)

    def _path_scan(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        """`Path.iterdir()` / `Path.glob(pat)` are R24 iterator values: the
        runtime materializes a list[Path] and `_mk_iter` wraps it in the
        {seq, idx} cursor record `for`/`next`/`list` already lower."""
        assert isinstance(node.func, ast.Attribute)
        list_t = {"kind": "list", "elem": {"kind": "class", "name": "Path"}}
        self._reject_keywords(node)
        if node.func.attr == "iterdir":
            if node.args:
                self.fail(node, "PS2003", "Path.iterdir expects 0 argument(s)")
            seq = self._abi_call("pathlib", "Path.iterdir", [receiver], list_t, "ps_path_iterdir", span)
        else:
            if len(node.args) != 1:
                self.fail(node, "PS2003", "Path.glob expects 1 argument(s)")
            pat = self._expr(node.args[0])
            if pat["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Path.glob pattern is str")
            seq = self._abi_call("pathlib", "Path.glob", [receiver, pat], list_t, "ps_path_glob", span)
        return _mk_iter(self, seq, node, span)

    def _path_mkdir(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        """`Path.mkdir(mode=0o777, parents=False, exist_ok=False)` ->
        ps_path_mkdir. `parents`/`exist_ok` are bool literals; `mode` is any
        int expression."""
        slots = self._place_nodes(node, ["mode", "parents", "exist_ok"])
        mode = _const_int(0o777, span) if slots[0] is None else self._expr(slots[0])
        if mode["type"]["kind"] != "int":
            self.fail(node, "PS1003", "Path.mkdir mode is int")
        args = [receiver, mode]
        for slot, arg_name in zip(slots[1:], ("parents", "exist_ok"), strict=True):
            if slot is None:
                flag = _const_int(0, span)
            elif isinstance(slot, ast.Constant) and isinstance(slot.value, bool):
                flag = _const_int(1 if slot.value else 0, span)
            else:
                self.fail(node, "PS1003", f"Path.mkdir {arg_name} is a bool literal")
            args.append(flag)
        return self._abi_call("pathlib", "Path.mkdir", args, {"kind": "none"}, "ps_path_mkdir", span)

    def _path_context(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        if node.func.attr == "__enter__":
            if node.args or node.keywords:
                self.fail(node, "PS2003", "Path.__enter__ expects 0 argument(s)")
            return receiver
        if len(node.args) != 3 or node.keywords:
            self.fail(node, "PS2003", "Path.__exit__ expects 3 argument(s)")
        for arg in node.args:
            self._expr(arg)
        return _const_none(span)

    def _gen_throw(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        """`gen.throw(E)`: mark the frame done and raise E out of the call.

        The exception class is a name here (exception classes are not values
        in this subset). A `yield` inside `try` never lowers, so no handler
        can surround the generator's live suspension point: CPython's "the
        generator catches it" outcome is unreachable, and propagate-and-
        mark-done is the exact semantics for every program that lowers.
        """
        if node.keywords:
            self.fail(node, "PS2003", "keyword arguments are not lowered")
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
            self.fail(node, "PS2002", "generator.throw takes one exception class")
        name = node.args[0].id
        if name not in EXCEPTIONS and not self.classes.get(name, {}).get("exception"):
            resolved = self.class_aliases.get(name, name)
            if not self.classes.get(resolved, {}).get("exception"):
                self.fail(node.args[0], "PS2002", "generator.throw takes a lowered exception class")
            name = resolved
        frame_t = receiver["type"]
        self.prelude.append(
            node_to_dict(
                _ir.setattr_stmt(dict_to_node(receiver), STATE, dict_to_node(_const_int(DONE, span)), _mk_span(span))
            )
        )
        self.prelude.append(
            node_to_dict(
                _ir.raise_stmt(
                    name,
                    _mk_span(span),
                    -1 if name in EXCEPTIONS else int(self.classes[name]["exc_code"]),
                )
            )
        )
        # Unreachable after the raise; the frame's elem keeps the expression typed.
        out = self._temp("thr")
        self.locals[out] = (
            frame_t.get("elem", {"kind": "none"}) if isinstance(frame_t.get("elem"), dict) else {"kind": "none"}
        )
        return _name(out, self.locals[out], span)

    def _hash_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        self._reject_keywords(node)
        name = node.func.attr
        if node.args:
            self.fail(node, "PS2003", f"Hash.{name} expects 0 argument(s)")
        if name == "hexdigest":
            return _mod_call("hashlib", "hexdigest", [receiver], {"kind": "str"}, span)
        if name == "digest":
            return _mod_call("hashlib", "digest", [receiver], {"kind": "bytes"}, span)
        self.fail(node, "PS2001", f"Hash.{name} is not lowered")
        raise AssertionError("unreachable")

    def _pattern_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        self._reject_keywords(node)
        name = node.func.attr
        pattern = _attr(receiver, "pattern", {"kind": "str"}, span)
        flags = _attr(receiver, "flags", {"kind": "int"}, span)
        if name == "findall":
            if len(node.args) != 1:
                self.fail(node, "PS2003", "Pattern.findall expects 1 argument(s)")
            ret, symbol = self._group_tuple(node, node.func.value)
            text = self._expr(node.args[0])
            if text["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Pattern.findall argument is " + _show(text["type"]) + ", expected str")
            expr = _mod_call("re", "findall", [pattern, text, flags], ret, span)
            if symbol:
                expr["symbol"] = symbol
            return expr
        if name in {"search", "match", "fullmatch"}:
            if len(node.args) != 1:
                self.fail(node, "PS2003", f"Pattern.{name} expects 1 argument(s)")
            text = self._expr(node.args[0])
            if text["type"]["kind"] != "str":
                self.fail(node, "PS1003", f"Pattern.{name} argument is " + _show(text["type"]) + ", expected str")
            return self._re_opt_match(node, span, name, [pattern, text, flags])
        if name in {"sub", "subn"}:
            if len(node.args) not in {2, 3}:
                self.fail(node, "PS2003", f"Pattern.{name} expects 2-3 argument(s)")
            repl = self._expr(node.args[0])
            text = self._expr(node.args[1])
            if repl["type"]["kind"] != "str" or text["type"]["kind"] != "str":
                self.fail(node, "PS1003", f"Pattern.{name} arguments are str")
            count = self._expr(node.args[2]) if len(node.args) == 3 else _const_int(0, span)
            if count["type"]["kind"] != "int":
                self.fail(node, "PS1003", f"Pattern.{name} count is an int")
            if name == "subn":
                return self._re_subn(node, span, [pattern, repl, text, count, flags])
            return _mod_call("re", "sub", [pattern, repl, text, count, flags], {"kind": "str"}, span)
        if name == "split":
            if len(node.args) not in {1, 2}:
                self.fail(node, "PS2003", "Pattern.split expects 1-2 argument(s)")
            text = self._expr(node.args[0])
            if text["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Pattern.split argument is " + _show(text["type"]) + ", expected str")
            maxsplit = self._expr(node.args[1]) if len(node.args) == 2 else _const_int(0, span)
            if maxsplit["type"]["kind"] != "int":
                self.fail(node, "PS1003", "Pattern.split maxsplit is an int")
            return _mod_call(
                "re", "split", [pattern, text, maxsplit, flags], {"kind": "list", "elem": {"kind": "str"}}, span
            )
        self.fail(node, "PS2001", f"Pattern.{name} is not lowered")
        raise AssertionError("unreachable")

    def _match_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        self._reject_keywords(node)
        name = node.func.attr
        if name not in {"group", "start", "end", "span"}:
            self.fail(node, "PS2001", f"Match.{name} is not lowered")
        if len(node.args) > 1:
            self.fail(node, "PS2003", f"Match.{name} expects 0-1 argument(s)")
        if node.args:
            index = self._expr(node.args[0])
            if index["type"]["kind"] != "int":
                self.fail(node, "PS2001", f"Match.{name} names are not lowered")
        else:
            index = _const_int(0, span)
        if name == "start" or name == "end":
            return self._abi_call("re", f"Match.{name}", [receiver, index], {"kind": "int"}, f"ps_match_{name}", span)
        if name == "span":
            start = self._abi_call("re", "Match.start", [receiver, index], {"kind": "int"}, "ps_match_start", span)
            end = self._abi_call("re", "Match.end", [receiver, index], {"kind": "int"}, "ps_match_end", span)
            return self._materialize_tuple([start, end], span, node)
        const0 = not node.args or (isinstance(node.args[0], ast.Constant) and node.args[0].value == 0)
        if const0:
            # Group 0 always takes part in a match that exists.
            return self._abi_call("re", "Match.group", [receiver, index], {"kind": "str"}, "ps_match_group", span)
        # `m.group(i)` is `str | None`: the group may not have taken part. Same
        # hit-then-fill optional as os.getenv.
        opt_t = self._opt_type({"kind": "str"}, node)
        res = self._opt_box(None, opt_t, span)
        dest = res["id"]
        hit = self._abi_call("re", "Match.group_hit", [receiver, index], {"kind": "bool"}, "ps_match_group_hit", span)
        got = self._abi_call("re", "Match.group", [receiver, index], {"kind": "str"}, "ps_match_group", span)
        self.prelude.append(_if_node(hit, _opt_fill(dest, opt_t, got, span), [], span))
        return res

    def _list_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        if self._receiver_is_frozen(node, receiver):
            self.fail(node, "PS2002", "frozenset has no mutating methods")
        attr = node.func.attr
        if attr == "append":
            self._reject_keywords(node)
            if len(node.args) != 1:
                self.fail(node, "PS2003", "list.append expects 1 argument(s)")
            if receiver["kind"] not in {"name", "attr"}:
                # A module-level list reads through `ps_global_get_ptr`, which
                # hands back the shared sequence pointer: the push mutates the
                # same object the slot holds. An attribute read hands back the
                # field's own sequence pointer the same way. Other non-name
                # receivers keep the fence.
                recv = node.func.value
                slot = self.global_slots.get(recv.id) if isinstance(recv, ast.Name) else None
                if slot is None or not slot.get("active"):
                    self.fail(node, "PS2002", "list.append needs a name")
            elem = receiver["type"]["elem"]
            value = self._expr(node.args[0], elem)
            if not types_equal(value["type"], elem) and not _any_accepts(elem, value["type"]):
                self.fail(node, "PS1003", "list.append takes an element of that list")
            push = node_to_dict(
                _ir.call(
                    _ir.module_callee("builtins", "list.append"),
                    [dict_to_node(receiver), dict_to_node(value)],
                    _mk_type({"kind": "none"}),
                    _mk_span(span),
                    "ps_list_push",
                    "",
                    -1,
                )
            )
            self.prelude.append(_expr_node(push, span))
            return _const_none(span)
        elem = receiver["type"]["elem"]
        # In-place mutations go through a prelude call; reads return a value.
        suffix = {"int": "i64", "float": "f64", "str": "buf", "bytes": "buf"}.get(elem["kind"])
        if attr == "sort":
            desc = _const_int(0, span)
            for kw in node.keywords:
                if kw.arg == "reverse":
                    rv = self._expr(kw.value)
                    if rv["kind"] != "const" or rv["type"]["kind"] != "bool":
                        self.fail(node, "PS2003", "list.sort reverse is a constant bool")
                    desc = _const_int(1 if rv["value"] else 0, span)
                else:
                    self.fail(node, "PS2003", f"list.sort keyword {kw.arg} is not lowered")
            if node.args:
                self.fail(node, "PS2003", "list.sort expects 0 argument(s)")
            if suffix is None:
                self.fail(node, "PS2002", "list.sort is lowered for int, float, str, and bytes elements")
            call = self._abi_call(
                "builtins", "list.sort", [receiver, desc], {"kind": "none"}, f"ps_seq_sort_{suffix}", span
            )
            self.prelude.append(_expr_node(call, span))
            return _const_none(span)
        self._reject_keywords(node)
        mutating = {"extend", "insert", "remove", "reverse"}
        if attr in mutating | {"index", "count", "pop"}:
            if suffix is None:
                self.fail(node, "PS2002", f"list.{attr} is lowered for int, float, str, and bytes elements")
            if attr == "extend":
                if len(node.args) != 1:
                    self.fail(node, "PS2003", "list.extend expects 1 argument(s)")
                other = self._expr(node.args[0])
                if other["type"]["kind"] != "list" or other["type"].get("set"):
                    self.fail(node, "PS1003", "list.extend takes a list")
                self._expect(node, other["type"]["elem"], elem, "list.extend element")
                call = self._abi_call(
                    "builtins", "list.extend", [receiver, other], {"kind": "none"}, "ps_seq_extend", span
                )
                self.prelude.append(_expr_node(call, span))
                return _const_none(span)
            if attr == "insert":
                if len(node.args) != 2:
                    self.fail(node, "PS2003", "list.insert expects 2 argument(s)")
                index = self._expr(node.args[0])
                self._expect(node, index["type"], {"kind": "int"}, "list.insert index")
                value = self._expr(node.args[1], elem)
                self._expect(node, value["type"], elem, "list.insert element")
                call = self._abi_call(
                    "builtins",
                    "list.insert",
                    [receiver, index, value],
                    {"kind": "none"},
                    f"ps_seq_insert_{suffix}",
                    span,
                )
                self.prelude.append(_expr_node(call, span))
                return _const_none(span)
            if attr == "remove":
                if len(node.args) != 1:
                    self.fail(node, "PS2003", "list.remove expects 1 argument(s)")
                value = self._expr(node.args[0], elem)
                self._expect(node, value["type"], elem, "list.remove element")
                call = self._abi_call(
                    "builtins", "list.remove", [receiver, value], {"kind": "none"}, f"ps_seq_remove_{suffix}", span
                )
                self.prelude.append(_expr_node(call, span))
                return _const_none(span)
            if attr == "reverse":
                if node.args:
                    self.fail(node, "PS2003", "list.reverse expects 0 argument(s)")
                call = self._abi_call("builtins", "list.reverse", [receiver], {"kind": "none"}, "ps_seq_reverse", span)
                self.prelude.append(_expr_node(call, span))
                return _const_none(span)
            if attr in {"index", "count"}:
                if len(node.args) != 1:
                    self.fail(node, "PS2003", f"list.{attr} expects 1 argument(s)")
                value = self._expr(node.args[0], elem)
                self._expect(node, value["type"], elem, f"list.{attr} element")
                return self._abi_call(
                    "builtins", f"list.{attr}", [receiver, value], {"kind": "int"}, f"ps_seq_{attr}_{suffix}", span
                )
            if len(node.args) > 1:
                self.fail(node, "PS2003", "list.pop expects at most 1 argument(s)")
            index = self._expr(node.args[0]) if node.args else _const_int(-1, span)
            self._expect(node, index["type"], {"kind": "int"}, "list.pop index")
            return self._abi_call("builtins", "list.pop", [receiver, index], elem, f"ps_seq_pop_{suffix}", span)
        if attr == "clear":
            if node.args:
                self.fail(node, "PS2003", "list.clear expects 0 argument(s)")
            if receiver["kind"] != "name":
                self.fail(node, "PS2002", "list.clear needs a name")
            self.prelude.append(_assign(str(receiver["id"]), _list_node([], receiver["type"], span), span))
            return _const_none(span)
        if attr == "copy":
            if node.args:
                self.fail(node, "PS2003", "list.copy expects 0 argument(s)")
            return self._abi_call(
                "builtins",
                "list.copy",
                [
                    receiver,
                    _const_int(0, span),
                    _const_int(0, span),
                    _const_int(0, span),
                    _const_int(0, span),
                    _const_int(1, span),
                ],
                {"kind": "list", "elem": elem},
                "ps_seq_slice",
                span,
            )
        # The generic wording, byte-identical to the dispatch tail, keeps
        # the fenced list-method probes' IR unchanged.
        self.fail(node, "PS2001", f"call of attribute is not lowered ({_dotted(node.func)})")
        raise AssertionError("unreachable")

    def _set_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        attr = node.func.attr
        if attr in {"add", "discard", "remove"}:
            if self._receiver_is_frozen(node, receiver):
                self.fail(node, "PS2002", "frozenset has no mutating methods")
            self._reject_keywords(node)
            if len(node.args) != 1:
                self.fail(node, "PS2003", f"set.{attr} expects 1 argument(s)")
            if receiver["kind"] != "name" and not (attr == "add" and receiver["kind"] == "attr"):
                self.fail(node, "PS2002", f"set.{attr} needs a name")
            elem = receiver["type"]["elem"]
            value = self._expr(node.args[0], elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", f"set.{attr} takes an element of that set")
            if attr == "add":
                hay = receiver if receiver["kind"] == "attr" else _name(str(receiver["id"]), receiver["type"], span)
                self.prelude.extend(self._set_insert_stmts(hay, receiver["type"], value, span))
                return _const_none(span)
            # discard removes when present; remove raises KeyError when absent.
            slot = self._temp("elt")
            self.locals[slot] = elem
            hay = _name(receiver["id"], receiver["type"], span)
            needle = _name(slot, elem, span)
            drop = _mod_call("builtins", "list.remove", [hay, needle], {"kind": "none"}, span)
            drop["symbol"] = f"ps_seq_remove_{'i64' if elem['kind'] == 'int' else 'buf'}"
            else_body = [_raise_keyerror(span)] if attr == "remove" else []
            self.prelude.append(_assign(slot, value, span))
            cond = _contains_node(_name(str(receiver["id"]), receiver["type"], span), needle, False, span)
            self.prelude.append(_if_node(cond, [_expr_node(drop, span)], else_body, span))
            return _const_none(span)
        if attr in {"union", "intersection"}:
            self._reject_keywords(node)
            if len(node.args) != 1:
                self.fail(node, "PS2003", f"set.{attr} expects 1 argument(s)")
            elem = receiver["type"]["elem"]
            other = self._expr(node.args[0])
            if other["type"]["kind"] != "list" or not types_equal(other["type"]["elem"], elem):
                self.fail(node, "PS1003", f"set.{attr} takes a list or set of the same element type")
            return self._set_algebra(span, receiver, other, "union" if attr == "union" else "inter")
        self.fail(node, "PS2001", "set.add is the only lowered set method")
        raise AssertionError("unreachable")

    def _dict_keys(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        self._reject_keywords(node)
        if node.args:
            self.fail(node, "PS2003", "dict.keys expects 0 argument(s)")
        key_t = receiver["type"]["key"]
        if key_t["kind"] not in {"int", "str"}:
            self.fail(node, "PS2002", "dict keys are int or str")
        typ = {"kind": "list", "elem": key_t}
        folded = _const_dict_keys(receiver, typ, span)
        if folded is not None:
            return folded
        return self._abi_call("builtins", "dict.keys", [receiver], typ, "ps_dict_keys", span)

    def _receiver_is_frozen(self, node: ast.Call, receiver: dict[str, Any]) -> bool:
        if receiver.get("frozen"):
            return True
        assert isinstance(node.func, ast.Attribute)
        return self._binding_is_frozen(node.func.value)

    def _place_nodes(self, node: ast.Call, names: list[str]) -> list[ast.expr | None]:
        if any(kw.arg is None for kw in node.keywords):
            self.fail(node, "PS2002", kwargs_call_note())
        if len(node.args) > len(names):
            self.fail(node, "PS2003", "too many positional arguments")
        slots: list[ast.expr | None] = [None] * len(names)
        for index, arg in enumerate(node.args):
            slots[index] = arg
        seen: set[str] = set()
        for kw in node.keywords:
            assert kw.arg is not None
            if kw.arg not in names:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
            index = names.index(kw.arg)
            if slots[index] is not None or kw.arg in seen:
                self.fail(kw, "PS2003", f"{kw.arg} given more than once")
            seen.add(kw.arg)
            slots[index] = kw.value
        return slots


def _const_dict_keys(receiver: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """A dict literal's keys are already the list the IR builds, in insertion order."""

    if receiver.get("kind") != "dict":
        return None
    elems: list[dict[str, Any]] = []
    seen: list[Any] = []
    for pair in receiver["pairs"]:
        key = pair["key"]
        if key.get("kind") != "const":
            return None
        if key["value"] in seen:
            continue
        seen.append(key["value"])
        elems.append(key)
    return _list_node(elems, typ, span)
