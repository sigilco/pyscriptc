"""Call and intrinsic lowering for the lowerer.

Split out of `__init__.py` as E0 move 4 (calls and intrinsics land together:
they share the `_call_name` / `_call_attr` dispatch). `Lowerer` composes this
mixin in. The area owns the name-call dispatcher, the argument fitters, the
builtin container calls (`set`, `frozenset`, `bool`, `list`), and the `re`
pattern helpers. The attribute-call family moved verbatim to
`lower._call_attr`, which composes here as `CallAttrMixin`; the wave-call
family (the `math` rows, the fixed-spec wave call and the `json`/`struct`
bodies) moved verbatim to `lower._calls_more`, which composes here as
`CallsMoreMixin`.

Module-level helpers used only here stay here (`_const_set`,
`_float_special_arg`); the ones the wave family needs moved with it
(`_const_float_text`, `_const_text`, `_capturing_groups`,
`_struct_arg_kinds`), and the rest of the shared leaves stay in `_core.py`.
The boundary calls in both directions stay `self._method()` so the move is
behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.builtins import builtin_spec
from pyscriptc.errors import span_of
from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._annotations import _type_slug
from pyscriptc.lower._call_attr import CallAttrMixin
from pyscriptc.lower._calls_more import (  # noqa: F401  (the moved helpers stay exported)
    _ASCII_SPACE,
    _FLOAT_WORDS,
    CallsMoreMixin,
    _capturing_groups,
    _const_float_text,
    _const_text,
    _struct_arg_kinds,
    _t_class,
    _t_leaf,
    _t_list,
    _type_name,
)
from pyscriptc.lower._core import (
    _assign,
    _attr,
    _binary_int,
    _compare,
    _const_bool,
    _const_int,
    _const_str,
    _has_typevar,
    _len_call,
    _mk_span,
    _mk_type,
    _name,
    _show,
    _sig_typevars,
    _subst_type,
)
from pyscriptc.reflection import kwargs_call_note, typing_note
from pyscriptc.restlower import _static_builtin_fold, builtin_call
from pyscriptc.tirserde import dict_to_node, node_to_dict


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record: int, float, bool, str, bytes, list,
    dict, class, exc, func, any or none."""

    kind: str = typ["kind"]
    return kind


def _maybe_type_kind(typ: dict[str, Any]) -> str | None:
    """The kind tag of a type record, or None when the record carries none."""

    if "kind" not in typ:
        return None
    return _type_kind(typ)


def _node_kind(node: dict[str, Any]) -> str:
    """The kind tag of an IR node: call, const, name, list, attr, ..."""

    kind: str = node["kind"]
    return kind


def _maybe_node_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of an IR node, or None when the node carries none."""

    if "kind" not in node:
        return None
    return _node_kind(node)


def _node_type_kind(node: dict[str, Any]) -> str:
    """The kind tag of the type record an IR node carries."""

    typ: dict[str, Any] = node["type"]
    return _type_kind(typ)


def _maybe_node_type_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of the type record an IR node carries, or None when the
    node carries no type record."""

    if "type" not in node:
        return None
    return _node_type_kind(node)


class CallsMixin(CallsMoreMixin, CallAttrMixin):
    def _call_name(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        if name == "super":
            self.fail(node, "PS2002", "super is not lowered")
        if name == "all":
            return self._all_any(node, span, True)
        if name == "any":
            return self._all_any(node, span, False)
        if name == "set":
            return self._set_call(node, span, expect)
        if name == "frozenset":
            return self._frozenset_call(node, span, expect)
        if name == "bool":
            return self._bool_call(node, span)
        if name == "list":
            return self._list_call(node, span, expect)
        found = builtin_call(self, node, span)
        if found is not None:
            return found
        if name in self.type_aliases:
            note = typing_note(name)
            if note is not None:
                self.fail(node, "PS2002", note)
            self.fail(node, "PS2002", f"{name} is a typing name and is not a value")
        if name in self.typevars:
            self.fail(node, "PS2002", f"{name} is a TypeVar and is not a value")
        if name in {"int", "float", "len", "str", "id"}:
            if name == "float":
                special = _float_special_arg(node)
                if special is not None:
                    return node_to_dict(_ir.const_node(_ir.P_STR, special, _ir.t_leaf("float"), _mk_span(span)))
            if name != "int":
                self._reject_keywords(node)
            args = [self._expr(arg) for arg in node.args]
            if name in {"int", "float"} and len(args) >= 1:
                args[0] = self._opt_arg_or_type_error(name, args[0], span)
            if name == "int" and node.args and (len(node.args) == 2 or node.keywords):
                # The `base` row: `int(text, base)` positional and `int(text,
                # base=N)` keyword are the same call; an unknown keyword name
                # stays fenced below.
                if len(node.args) == 2 and any(kw.arg == "base" for kw in node.keywords):
                    self.fail(node, "PS2003", "int got base given more than once")
                named = [kw.arg for kw in node.keywords if kw.arg not in {None, "base"}]
                if named:
                    self.fail(node, "PS2003", f"unexpected keyword argument {named[0]}")
                if any(kw.arg is None for kw in node.keywords):
                    self.fail(node, "PS2002", "**kwargs are not lowered")
                kind = _node_type_kind(args[0])
                if kind not in {"str", "bytes"}:
                    self.fail(node, "PS2003", "int with an explicit base takes a str or bytes value")
                if len(node.args) == 1:
                    base_node = next(kw.value for kw in node.keywords if kw.arg == "base")
                    if isinstance(base_node, ast.Constant) and isinstance(base_node.value, int):
                        if base_node.value == 0:
                            self.fail(node, "PS2003", "int base 0 is not lowered")
                        if base_node.value < 2 or base_node.value > 36:
                            self.fail(node, "PS2003", "int lowers base 2 to 36 only")
                    args.append(self._expr(base_node, _t_leaf("int")))
                elif _node_type_kind(args[1]) != "int":
                    self.fail(node, "PS2003", "int base is int")
                return self._abi_call("builtins", "int", args, _t_leaf("int"), "ps_int_from_base", span)
            if len(args) == 1:
                folded = _static_builtin_fold(name, args[0], span, fence=self)
                if folded is not None:
                    return folded
            spec = builtin_spec(name, [arg["type"] for arg in args])
            if spec is None and name == "str" and len(args) == 1 and args[0]["type"].get("optional"):
                # str(next(it, None)): unbox the tagged optional, print
                # "None" for the untagged arm, str(_val) otherwise.
                return self._str_of_opt(node, args[0], span)
            if spec is None:
                if name in {"str", "len"} and len(args) == 1 and _node_type_kind(args[0]) == "class":
                    called = self._dunder_call(node, args[0], {"str": "__str__", "len": "__len__"}[name], [], span)
                    if called is None and name == "str":
                        called = self._dunder_call(node, args[0], "__repr__", [], span)
                    if called is not None:
                        return called
                self.fail(node, "PS2003", f"{name} is not lowered for these arguments")
            if name == "float" and _node_type_kind(args[0]) == "int":
                return node_to_dict(_ir.cast("int_to_float", dict_to_node(args[0]), _mk_type(spec), _mk_span(span)))
            if name == "str" and _node_type_kind(args[0]) == "str":
                return args[0]
            if name == "str" and _node_type_kind(args[0]) == "none":
                # None is not a runtime value; str(None) folds at lowering.
                return node_to_dict(_ir.const_node(_ir.P_STR, "None", _mk_type(spec), _mk_span(span)))
            if name == "int" and _node_type_kind(args[0]) in {"bytes", "str"}:
                return self._abi_call("builtins", "int", args, spec, "ps_int_from_bytes", span)
            return node_to_dict(
                _ir.call(
                    _ir.builtin(name),
                    [dict_to_node(arg) for arg in args],
                    _mk_type(spec),
                    _mk_span(span),
                    "",
                    "",
                    -1,
                )
            )
        binding = self.bindings.get(name)
        if binding is not None:
            if binding.queued:
                self._queued(node, f"{binding.module}.{binding.attr}" if binding.attr else binding.module)
            if binding.kind == "erased":
                note = typing_note(name)
                if note is not None:
                    self.fail(node, "PS2002", note)
            if binding.kind == "func":
                return self._module_call(node, span, binding.module, binding.attr, expect)
            # boxed:begin
            if binding.kind == "island_member":
                return self._island_call(node, span, binding.module, binding.attr, expect)
            # boxed:end
            if binding.kind == "pyfunc":
                return self._linked_call(node, span, self.pkg_funcs[name], name, expect)
            # boxed:begin
            # The per-module plane split: the definition rides another
            # module's island floor, so the call crosses the boundary
            # through the boxed seam, namespaced by the defining module.
            if binding.kind == "island_floor":
                return self._island_floor_call(node, span, binding.module, binding.attr, expect)
            # boxed:end
            if binding.kind == "const" and self._ffi_module(binding.module) is not None:
                self.fail(node, "PS2002", f"{name} is a constant and is not a function")
            self.fail(node, "PS2002", f"{name} is not a function")
        # boxed:begin
        # An island class is instantiated island-side (issue #628): the class
        # object in the island namespace is callable, so the call site is the
        # existing `ps_island_fn_invoke` trampoline and the instance is a
        # handle.
        if name in self.island_classes:
            return self._island_fn_call(node, span, name, expect)
        # boxed:end
        if name in self.class_aliases or name in self.classes:
            return self._construct(node, span, self.class_aliases.get(name, name))
        ftype = self.locals.get(name, self.captures.get(name))
        if ftype is not None and _type_kind(ftype) == "func":
            args = self._fill_args(node, self._callable_params(ftype), name)
            return node_to_dict(
                _ir.call(
                    _ir.value_callee(dict_to_node(_name(name, ftype, span))),
                    [dict_to_node(arg) for arg in args],
                    _mk_type(ftype["return"]),
                    _mk_span(span),
                    "",
                    "",
                    -1,
                )
            )
        slot = self.global_slots.get(name)
        if slot is not None and slot.get("active") and _type_kind(slot["type"]) == "func":
            # A module-level callable bind (`h = f`, `@dec def`, partial):
            # read the pair cell back through the global slot.
            ftype = slot["type"]
            params = [{"name": f"_{index}", "type": typ} for index, typ in enumerate(ftype["params"])]
            args = self._fill_args(node, params, name)
            return node_to_dict(
                _ir.call(
                    _ir.value_callee(dict_to_node(self._global_get(name, span))),
                    [dict_to_node(arg) for arg in args],
                    _mk_type(ftype["return"]),
                    _mk_span(span),
                    "",
                    "",
                    -1,
                )
            )
        # boxed:begin
        # The boxed tier (issue #612): a call to a definition the planner
        # routed to the island lowers to the fn trampoline beside
        # `ps_island_call`. The check sits after the local/global reads (a
        # shadowing local or slot wins, like CPython's scoping) and before
        # the signature lookup, because a fenced definition still has a
        # signature but no lowered body to call. A generic sibling the
        # demotion cannot reach (issue #620) fences here: the island seed
        # cannot instantiate its monomorphizations.
        if name in self.island_fns:
            if name in self.generic_fns:
                self.fail(
                    node,
                    "PS2002",
                    f"{self.function or 'the caller'} runs on the island and cannot call the compiled "
                    f"function {name}: generic compiled function {name} cannot be demoted to the island; "
                    "the island-to-compiled seam is the `--emit binding` surface",
                )
            return self._island_fn_call(node, span, name, expect)
        # boxed:end
        if name in self.sigs:
            if name in self.generic_fns:
                return self._generic_call(node, span, name)
            params, ret = self.sigs[name]
            args = self._fill_args(node, params, name)
            return node_to_dict(
                _ir.call(
                    _ir.function_callee(self._ir_name(name)),
                    [dict_to_node(arg) for arg in args],
                    _mk_type(ret),
                    _mk_span(span),
                    "",
                    "",
                    -1,
                )
            )
        self.fail(node, "PS2002", f"call to {name} is not lowered")
        raise AssertionError("unreachable")

    def _callable_params(self, ftype: dict[str, Any]) -> list[dict[str, Any]]:
        """The call signature of a callable local.

        A nested def keeps its names and defaults; a Callable annotation
        carries bare types, so its parameters are positional only.
        """
        known = self.local_sigs.get(id(ftype))
        if known is not None and not any(param.get("vararg") or param.get("kwarg") for param in known[1]):
            return known[1]
        return [{"name": f"_{index}", "type": typ, "posonly": True} for index, typ in enumerate(ftype["params"])]

    def _opt_arg_or_type_error(self, name: str, arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """`int(x)` on a tagged `str | None`: CPython raises TypeError for None.

        The tag is tested at the call; the unwrapped `_val` feeds the lowered
        conversion, so `int(m.group(1))` runs without a narrowing temp.
        """
        typ = arg["type"]
        if not typ.get("optional") or _type_kind(typ["elem"]) not in {"str", "bytes", "int", "float"}:
            return arg
        if _node_kind(arg) != "name":
            temp = self._temp("optarg")
            self.locals[temp] = typ
            self.prelude.append(_assign(temp, arg, span))
            arg = _name(temp, typ, span)
        message = f"{name}() argument must be a string, a bytes-like object or a real number, not 'NoneType'"
        cond = node_to_dict(
            _ir.unary(
                "not", dict_to_node(_attr(arg, "_tag", _t_leaf("bool"), span)), _ir.t_leaf("bool"), _mk_span(span)
            )
        )
        raised = node_to_dict(_ir.raise_stmt("TypeError", _mk_span(span), -1, dict_to_node(_const_str(message, span))))
        self.prelude.append(node_to_dict(_ir.if_stmt(cond, [dict_to_node(raised)], [], _mk_span(span))))
        return _attr(arg, "_val", typ["elem"], span)

    def _generic_call(self, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
        """A call to a generic function (`def ident(x: T) -> T`): unify the
        argument types with the signature's TypeVars, emit a specialized
        `ident.int`-style copy, and call it."""
        params, ret = self.sigs[name]
        mapping: dict[str, dict[str, Any]] = {}
        args = self._fill_generic_args(node, params, mapping, name)
        emit = self._fn_instance(node, name, params, ret, mapping)
        # Params whose TypeVar resolved to none drop from the emitted
        # signature; their args drop with them.
        args = [
            arg
            for arg, param in zip(args, params, strict=True)
            if _type_kind(_subst_type(param["type"], mapping)) != "none"
        ]
        return node_to_dict(
            _ir.call(
                _ir.function_callee(self._ir_name(emit)),
                [dict_to_node(arg) for arg in args],
                _mk_type(_subst_type(ret, mapping)),
                _mk_span(span),
                "",
                "",
                -1,
            )
        )

    def _fn_instance(
        self,
        node: ast.Call,
        name: str,
        params: list[dict[str, Any]],
        ret: dict[str, Any],
        mapping: dict[str, dict[str, Any]],
    ) -> str:
        """Bind every signature TypeVar (from the args, else its bound) and
        queue the specialized body; returns the emitted function name."""
        tvars = _sig_typevars([*(param["type"] for param in params), ret])
        for tv in tvars:
            if tv not in mapping:
                bound = self.typevars.get(tv)
                if bound is None:
                    self.fail(node, "PS1003", f"cannot infer {tv} for {name}")
                mapping[tv] = bound
        key = (name, tuple((tv, type_key(mapping[tv])) for tv in tvars))
        emit = self.inst_fns.get(key)
        if emit is None:
            emit = f"{name}.{'_'.join(_type_slug(mapping[tv]) for tv in tvars)}"
            self.inst_fns[key] = emit
            self.inst_queue.append((name, emit, dict(mapping)))
        return emit

    def _generic_arg(
        self,
        arg_node: ast.expr,
        param: dict[str, Any],
        mapping: dict[str, dict[str, Any]],
        node: ast.Call,
    ) -> dict[str, Any]:
        ptype = param["type"]
        if not _has_typevar(ptype):
            return self._expr(arg_node, ptype)
        value = self._expr(arg_node)
        self._unify(value["type"], ptype, mapping, node, arg_node)
        return value

    def _unify(
        self,
        got: dict[str, Any],
        want: dict[str, Any],
        mapping: dict[str, dict[str, Any]],
        node: ast.Call,
        arg_node: ast.expr,
    ) -> None:
        """Bind the TypeVars in `want` against the concrete `got`, or check the
        concrete parts match when nothing remains to bind."""
        if _type_kind(want) == "typevar":
            tv = want["name"]
            bound = self.typevars.get(tv)
            existing = mapping.get(tv)
            if existing is not None:
                if not types_equal(existing, got):
                    self.fail(arg_node, "PS1003", f"{tv} is {_show(existing)}, not {_show(got)}")
                return
            if bound is not None:
                self._expect(arg_node, got, bound, tv)
                mapping[tv] = bound
            else:
                mapping[tv] = got
            return
        if _type_kind(want) == "list" and _type_kind(got) == "list":
            self._unify(got["elem"], want["elem"], mapping, node, arg_node)
            return
        if _type_kind(want) == "dict" and _type_kind(got) == "dict":
            self._unify(got["key"], want["key"], mapping, node, arg_node)
            self._unify(got["value"], want["value"], mapping, node, arg_node)
            return
        if _type_kind(want) == "func" and _type_kind(got) == "func":
            for got_param, want_param in zip(got["params"], want["params"], strict=False):
                self._unify(got_param, want_param, mapping, node, arg_node)
            self._unify(got["return"], want["return"], mapping, node, arg_node)
            return
        if (
            _type_kind(want) == "class"
            and _type_kind(got) == "class"
            and got["name"] == want["name"]
            and isinstance(want.get("elem"), dict)
            and isinstance(got.get("elem"), dict)
        ):
            self._unify(got["elem"], want["elem"], mapping, node, arg_node)
            return
        self._expect(arg_node, got, want, node.func.id if isinstance(node.func, ast.Name) else "call")

    def _fill_generic_args(
        self,
        node: ast.Call,
        params: list[dict[str, Any]],
        mapping: dict[str, dict[str, Any]],
        what: str,
    ) -> list[dict[str, Any]]:
        """`_fill_args` for a signature with TypeVar placeholders: each
        argument lowers once; placeholder parameters unify their declared
        TypeVars against the argument type instead of expecting it."""
        self._expand_kwdisplay(node)
        if any(kw.arg is None for kw in node.keywords):
            self.fail(node, "PS2002", kwargs_call_note())
        node.args = self._expand_starred(node)
        var_index = next((index for index, param in enumerate(params) if param.get("vararg")), None)
        ordinary = params if var_index is None else params[:var_index]
        if var_index is None and len(node.args) > len(params):
            self.fail(node, "PS1003", f"{what} expects {len(params)} argument(s)")
        if var_index is not None and len(node.args) < len(ordinary):
            self.fail(node, "PS1003", f"{what} expects {len(ordinary)} argument(s)")
        slots: list[dict[str, Any] | None] = [None] * len(params)
        names = [param["name"] for param in params]
        limit = len(node.args) if var_index is None else min(len(node.args), len(ordinary))
        for index in range(limit):
            slots[index] = self._generic_arg(node.args[index], params[index], mapping, node)
        seen = {param["name"] for param, slot in zip(params, slots, strict=True) if slot is not None}
        for kw in node.keywords:
            if kw.arg in seen:
                self.fail(node, "PS1003", f"{what} gets {kw.arg} twice")
            if kw.arg not in names:
                self.fail(node, "PS1003", f"{what} does not take {kw.arg}")
            index = names.index(kw.arg)
            seen.add(kw.arg)
            slots[index] = self._generic_arg(kw.value, params[index], mapping, node)
        args: list[dict[str, Any]] = []
        for index, param in enumerate(params):
            if param.get("vararg"):
                continue
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} misses {param['name']}")
                got = param["default"]
                if param.get("optdefault"):
                    got = self._opt_box(got, param["type"], span_of(node, self.filename))
            want = _subst_type(param["type"], mapping)
            if not _has_typevar(want) and not types_equal(got["type"], want):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        if var_index is not None:
            varparam = params[var_index]
            elem = varparam["type"]["elem"]
            elems = []
            for arg_node in node.args[len(ordinary) :]:
                value = self._expr(arg_node)
                self._unify(value["type"], elem, mapping, node, arg_node)
                elems.append(value)
            var_type = _subst_type(varparam["type"], mapping)
            args.append(
                node_to_dict(
                    _ir.list_lit(
                        [dict_to_node(e) for e in elems], _mk_type(var_type), _mk_span(span_of(node, self.filename))
                    )
                )
            )
        return args

    def _re_opt_match(
        self,
        node: ast.Call,
        span: dict[str, Any],
        attr: str,
        args: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """`re.search`/`re.match`/`re.fullmatch` -> the tagged `Match | None`.

        Two engine runs like `os.getenv`: a bool probe, then the make call
        fills the cell only when the probe hit.
        """
        if args is None:
            params = [
                {"name": "pattern", "type": _t_leaf("str")},
                {"name": "string", "type": _t_leaf("str")},
                {
                    "name": "flags",
                    "type": _t_leaf("int"),
                    "default": _const_int(0, span),
                },
            ]
            args = self._fill_args(node, params, f"re.{attr}")
        opt_t = self._opt_type(_t_class("Match"), node)
        res = self._opt_box(None, opt_t, span)
        dest = res["id"]
        hit = self._abi_call("re", f"{attr}.hit", args, _t_leaf("bool"), f"ps_re_{attr}_hit", span)
        made = self._abi_call("re", f"{attr}.make", args, opt_t["elem"], f"ps_re_{attr}_make", span)
        tag = node_to_dict(
            _ir.setattr_stmt(
                dict_to_node(_name(dest, opt_t, span)), "_tag", dict_to_node(_const_bool(True, span)), _mk_span(span)
            )
        )
        val = node_to_dict(
            _ir.setattr_stmt(dict_to_node(_name(dest, opt_t, span)), "_val", dict_to_node(made), _mk_span(span))
        )
        self.prelude.append(
            node_to_dict(_ir.if_stmt(dict_to_node(hit), [dict_to_node(tag), dict_to_node(val)], [], _mk_span(span)))
        )
        return res

    def _re_subn(
        self,
        node: ast.Call,
        span: dict[str, Any],
        args: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """`re.subn`/`Pattern.subn` -> the (new text, count) tuple class."""
        if args is None:
            zero = _const_int(0, span)
            params = [
                {"name": "pattern", "type": _t_leaf("str")},
                {"name": "repl", "type": _t_leaf("str")},
                {"name": "string", "type": _t_leaf("str")},
                {"name": "count", "type": _t_leaf("int"), "default": zero},
                {"name": "flags", "type": _t_leaf("int"), "default": zero},
            ]
            args = self._fill_args(node, params, "re.subn")
        tup = self._tuple_type([_t_leaf("str"), _t_leaf("int")], node)
        return self._abi_call("re", "subn", args, tup, "ps_re_subn", span)

    def _group_tuple(self, node: ast.AST, pattern: ast.expr | None) -> tuple[dict[str, Any], str | None]:
        text = self._pattern_literal(pattern) if pattern is not None else None
        groups = _capturing_groups(text) if text is not None else 0
        if groups > 4:
            self.fail(node, "PS2002", "re.findall with more than four capturing groups is not lowered")
        if groups >= 2:
            elem = self._tuple_type([_t_leaf("str") for _ in range(groups)], node)
            return _t_list(elem), "ps_re_findall_groups"
        return _t_list(_t_leaf("str")), None

    def _pattern_literal(self, node: ast.expr) -> str | None:
        direct = _const_text(node)
        if direct is not None:
            return direct
        if not isinstance(node, ast.Call) or not node.args:
            return None
        func = node.func
        if isinstance(func, ast.Name):
            binding = self.bindings.get(func.id)
            if binding is not None and binding.kind == "func" and binding.module == "re" and binding.attr == "compile":
                return _const_text(node.args[0])
            return None
        if isinstance(func, ast.Attribute):
            resolved = self._resolve_module(func)
            if resolved is not None and resolved[1] == "re" and resolved[2] == "compile":
                return _const_text(node.args[0])
        return None

    def _str_of_opt(self, node: ast.expr, arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """`str(opt)`: "None" when the tag is clear, str(_val) when set."""
        out = self._temp("stropt")
        self.locals[out] = _t_leaf("str")
        elem = arg["type"]["elem"]
        value = _attr(arg, "_val", elem, span)
        tagged_str: dict[str, Any] | None = None
        if _type_kind(elem) == "class":
            tagged_str = self._dunder_call(node, value, "__str__", [], span)
            if tagged_str is None:
                tagged_str = self._dunder_call(node, value, "__repr__", [], span)
            if tagged_str is None:
                self.fail(node, "PS2003", "str is not lowered for this optional element type")
        else:
            tagged_str = node_to_dict(
                _ir.call(_ir.builtin("str"), [dict_to_node(value)], _ir.t_leaf("str"), _mk_span(span), "", "", -1)
            )
        self.prelude.append(
            node_to_dict(
                _ir.if_stmt(
                    dict_to_node(_attr(arg, "_tag", _t_leaf("bool"), span)),
                    [dict_to_node(_assign(out, tagged_str, span))],
                    [dict_to_node(_assign(out, _const_str("None", span), span))],
                    _mk_span(span),
                )
            )
        )
        return _name(out, _t_leaf("str"), span)

    def _abi_call(
        self,
        module: str,
        name: str,
        args: list[dict[str, Any]],
        typ: dict[str, Any],
        symbol: str,
        span: dict[str, Any],
    ) -> dict[str, Any]:
        return node_to_dict(
            _ir.call(
                _ir.module_callee(module, name),
                [dict_to_node(arg) for arg in args],
                _mk_type(typ),
                _mk_span(span),
                symbol,
                "",
                -1,
            )
        )

    def _reject_keywords(self, node: ast.Call) -> None:
        if node.keywords:
            self.fail(node, "PS2003", "keyword arguments are not lowered")

    def _fill_args(self, node: ast.Call, params: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
        self._expand_kwdisplay(node)
        if any(kw.arg is None for kw in node.keywords):
            self.fail(node, "PS2002", kwargs_call_note())
        node.args = self._expand_starred(node)
        var_index = next(
            (index for index, param in enumerate(params) if param.get("vararg") or param.get("kwarg")), None
        )
        if var_index is not None:
            return self._fill_varargs(node, params, var_index, what)
        kwonly_start = next((index for index, param in enumerate(params) if param.get("kwonly")), len(params))
        positional = params[:kwonly_start]
        if len(node.args) > len(positional):
            self.fail(node, "PS1003", f"{what} expects {len(positional)} argument(s)")
        slots: list[dict[str, Any] | None] = [None] * len(params)
        names = [param["name"] for param in params]
        pos_names = [param["name"] for param in positional]
        for index, arg_node in enumerate(node.args):
            slots[index] = self._expr(arg_node, params[index]["type"])
        order = list(range(len(node.args)))
        seen: set[str] = set()
        for kw in node.keywords:
            assert kw.arg is not None
            if kw.arg in pos_names:
                index = names.index(kw.arg)
                if params[index].get("posonly"):
                    self.fail(kw, "PS2003", f"{what} got {kw.arg} as a keyword for a positional-only parameter")
                if index < len(node.args) or kw.arg in seen:
                    self.fail(kw, "PS2003", f"{kw.arg} given more than once")
                seen.add(kw.arg)
                slots[index] = self._expr(kw.value, params[index]["type"])
                order.append(index)
            elif kw.arg in names:
                index = names.index(kw.arg)
                if kw.arg in seen:
                    self.fail(kw, "PS2003", f"{kw.arg} given more than once")
                seen.add(kw.arg)
                slots[index] = self._expr(kw.value, params[index]["type"])
                order.append(index)
            else:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
        if order != sorted(order):
            self._pin_arg_order(slots, order, span_of(node, self.filename))
        args: list[dict[str, Any]] = []
        for index, param in enumerate(params):
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects {len(params)} argument(s)")
                got = param["default"]
                if _maybe_node_kind(got) == "tuple-default":
                    # A fixed-shape tuple default: each call site that omits
                    # the argument materializes its own record (CPython
                    # shares one tuple; value-identical replay here).
                    got = self._materialize_tuple(list(got["elems"]), span_of(node, self.filename), node)
                    self._expect(node, got["type"], param["type"], param["name"])
                elif param.get("optdefault"):
                    got = self._opt_box(
                        None if _node_type_kind(got) == "none" else got,
                        param["type"],
                        got.get("span", span_of(node, self.filename)),
                    )
            if not types_equal(got["type"], param["type"]) and not self._accepts_protocol(param["type"], got["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        return args

    def _pin_arg_order(self, slots: list[dict[str, Any] | None], order: list[int], span: dict[str, Any]) -> None:
        """Keyword arguments written out of parameter order still evaluate left to right.

        The call lists its arguments in parameter order, so each written
        argument that is not a constant or a name goes into a temp, in the
        order the source wrote them.
        """
        for index in order:
            value = slots[index]
            assert value is not None
            if _node_kind(value) in {"const", "name"}:
                continue
            temp = self._temp("arg")
            self.locals[temp] = value["type"]
            self.prelude.append(_assign(temp, value, span))
            slots[index] = _name(temp, value["type"], span)

    def _expand_starred(self, node: ast.Call) -> list[ast.expr]:
        """Positional arguments with statically-lengthed starred operands
        expanded in place: `f(*[1, 2])` and `f(*(1, 2))` fill the known
        signature positionally, so arity mismatches stay PS1003 count
        errors.

        A starred operand whose length the compiler cannot see (a bound
        list name, a call result) keeps the PS2002 fence: the callee is
        static, so the frame slots are fixed and nothing replays a
        runtime-length tail into them.
        """
        if not any(isinstance(arg, ast.Starred) for arg in node.args):
            return node.args
        expanded: list[ast.expr] = []
        for arg in node.args:
            if not isinstance(arg, ast.Starred):
                expanded.append(arg)
                continue
            operand = arg.value
            display = isinstance(operand, (ast.List, ast.Tuple)) and not any(
                isinstance(elt, ast.Starred) for elt in operand.elts
            )
            if not display:
                self.fail(arg, "PS2002", "starred calls are not lowered")
            expanded.extend(operand.elts)
        return expanded

    def _expand_kwdisplay(self, node: ast.Call) -> None:
        """A `**` operand that is a dict display with constant str keys
        forwards as explicit keyword arguments: `f(**{"a": 1})` binds the
        `a` parameter exactly like `f(a=1)`.

        The display is rewritten into `node.keywords` pairs before any fill
        path runs, so duplicates, unknown names, and missing parameters
        surface as the ordinary keyword-binding diagnostics. A `**` operand
        that is not a display (a name, a call) keeps the PS2002 fence: a
        runtime dict carries no static key set, so the lowering cannot
        prove which declared params its members bind to.
        """
        if not any(kw.arg is None for kw in node.keywords):
            return
        keywords: list[ast.keyword] = []
        for kw in node.keywords:
            if kw.arg is not None:
                keywords.append(kw)
                continue
            operand = kw.value
            display = isinstance(operand, ast.Dict) and not any(key is None for key in operand.keys)
            if not display:
                self.fail(kw, "PS2002", kwargs_call_note())
            for key_node, val_node in zip(operand.keys, operand.values, strict=True):
                if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                    self.fail(key_node, "PS2002", kwargs_call_note())
                keywords.append(ast.keyword(arg=key_node.value, value=val_node))
        node.keywords = keywords

    def _fill_varargs(
        self, node: ast.Call, params: list[dict[str, Any]], var_index: int, what: str
    ) -> list[dict[str, Any]]:
        fixed = params[:var_index]
        varparam = params[var_index] if params[var_index].get("vararg") else None
        kwparam = params[-1] if params[-1].get("kwarg") else None
        tail = params[var_index + 1 :]
        # Keyword-only parameters can sit before the `**kwargs` capture, so
        # split them out of the fixed prefix rather than assuming a layout.
        positional_params = [param for param in fixed if not param.get("kwonly")]
        kwonly = [param for param in fixed if param.get("kwonly")] + [param for param in tail if param.get("kwonly")]
        if varparam is not None and any(kw.arg == varparam["name"] for kw in node.keywords):
            self.fail(node, "PS2002", "a starred parameter is not passed by keyword")
        positional = list(node.args[: len(positional_params)])
        extra = list(node.args[len(positional_params) :])
        slots: list[dict[str, Any] | None] = [None] * len(positional_params)
        kw_slots: list[dict[str, Any] | None] = [None] * len(kwonly)
        names = [param["name"] for param in positional_params]
        kw_names = [param["name"] for param in kwonly]
        for index, arg_node in enumerate(positional):
            slots[index] = self._expr(arg_node, positional_params[index]["type"])
        seen: set[str] = set()
        leftover: list[ast.keyword] = []
        for kw in node.keywords:
            assert kw.arg is not None
            if kw.arg in names:
                index = names.index(kw.arg)
                if positional_params[index].get("posonly"):
                    self.fail(kw, "PS2003", f"{what} got {kw.arg} as a keyword for a positional-only parameter")
                if index < len(positional) or kw.arg in seen:
                    self.fail(kw, "PS2003", f"{kw.arg} given more than once")
                seen.add(kw.arg)
                slots[index] = self._expr(kw.value, positional_params[index]["type"])
            elif kw.arg in kw_names:
                index = kw_names.index(kw.arg)
                if kw.arg in seen:
                    self.fail(kw, "PS2003", f"{kw.arg} given more than once")
                seen.add(kw.arg)
                kw_slots[index] = self._expr(kw.value, kwonly[index]["type"])
            elif kwparam is not None:
                leftover.append(kw)
            else:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
        args: list[dict[str, Any]] = []
        for index, param in enumerate(positional_params):
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects {len(positional_params)} argument(s)")
                got = param["default"]
            if not types_equal(got["type"], param["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        span = span_of(node, self.filename)
        if varparam is not None:
            elem = varparam["type"]["elem"]
            elems = []
            for arg_node in extra:
                value = self._expr(arg_node, elem)
                if not types_equal(value["type"], elem):
                    self.fail(node, "PS1003", f"argument {varparam['name']} of {what} has the wrong type")
                elems.append(value)
            args.append(
                node_to_dict(_ir.list_lit([dict_to_node(e) for e in elems], _mk_type(varparam["type"]), _mk_span(span)))
            )
        elif extra:
            self.fail(node, "PS1003", f"{what} expects {len(positional_params)} argument(s)")
        for index, param in enumerate(kwonly):
            got = kw_slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects keyword argument {param['name']}")
                got = param["default"]
            if not types_equal(got["type"], param["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        if kwparam is not None:
            pairs = []
            for kw in leftover:
                value = self._expr(kw.value, kwparam["type"]["value"])
                if not types_equal(value["type"], kwparam["type"]["value"]):
                    self.fail(node, "PS1003", f"argument {kw.arg} of {what} has the wrong type")
                key = _const_str(str(kw.arg), span)
                pairs.append({"key": key, "value": value})
            args.append(
                node_to_dict(
                    _ir.dict_lit(
                        [_ir.Pair(dict_to_node(p["key"]), dict_to_node(p["value"])) for p in pairs],
                        _mk_type(kwparam["type"]),
                        _mk_span(span),
                    )
                )
            )
        return args

    def _set_call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) > 1:
            self.fail(node, "PS2003", "set expects 1 argument(s)")
        if not node.args:
            if expect is None or not expect.get("set"):
                self.fail(node, "PS1001", "an empty set needs a set annotation")
            return node_to_dict(_ir.list_lit([], _mk_type(expect), _mk_span(span)))
        source = self._expr(node.args[0])
        if _node_type_kind(source) != "list":
            self.fail(node, "PS2002", "set() takes a list of int or str")
        elem = source["type"]["elem"]
        if _type_kind(elem) not in {"int", "str"} or source["type"].get("set"):
            self.fail(node, "PS2002", "set elements are int or str")
        typ = _t_list(elem, True)
        if expect and expect.get("set"):
            self._expect(node, typ, expect, "set")
        folded = _const_set(source, typ, span)
        if folded is not None:
            return folded
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, node_to_dict(_ir.list_lit([], _mk_type(typ), _mk_span(span))), span))
        self.prelude.extend(self._set_absorb(dest, typ, source, span))
        return _name(dest, typ, span)

    def _frozenset_call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        """A list or set display of const str or const int, or one str constant, is the set list.

        A str constant is the set of its characters, in order, first duplicate kept.
        Bytes, a name, a comprehension, and anything else stay fenced. The value
        is marked frozen so .add and .remove on it do not lower. There is no frozenset kind.
        """
        if node.keywords or len(node.args) != 1:
            self.fail(node, "PS2002", "call to frozenset is not lowered")
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return self._frozenset_str(node, arg, span, expect)
        if not isinstance(arg, (ast.List, ast.Set)):
            self.fail(node, "PS2002", "call to frozenset is not lowered")
        folded = self._frozen_const_elems(arg)
        if folded is None:
            self.fail(node, "PS2002", "call to frozenset is not lowered")
        kind, elems = folded
        typ = _t_list(_t_leaf(kind), True)
        if expect is not None and expect.get("set"):
            self._expect(node, typ, expect, "frozenset")
        return node_to_dict(_ir.list_lit([dict_to_node(e) for e in elems], _mk_type(typ), _mk_span(span), True))

    def _frozen_const_elems(self, node: ast.List | ast.Set) -> tuple[str, list[dict[str, Any]]] | None:
        if not node.elts or any(isinstance(elt, ast.Starred) for elt in node.elts):
            return None
        kinds: list[str] = []
        for elt in node.elts:
            if not isinstance(elt, ast.Constant) or isinstance(elt.value, bool):
                return None
            if isinstance(elt.value, str):
                kinds.append("str")
            elif isinstance(elt.value, int):
                kinds.append("int")
            else:
                return None
        if len(set(kinds)) != 1:
            return None
        elems: list[dict[str, Any]] = []
        seen: list[Any] = []
        for elt in node.elts:
            value = self._expr(elt)
            if value["value"] in seen:
                continue
            seen.append(value["value"])
            elems.append(value)
        return kinds[0], elems

    def _frozenset_str(
        self, node: ast.Call, arg: ast.Constant, span: dict[str, Any], expect: dict[str, Any] | None
    ) -> dict[str, Any]:
        text = self._const(arg, span_of(arg, self.filename))["value"]
        assert isinstance(text, str)
        char_span = span_of(arg, self.filename)
        elems: list[dict[str, Any]] = []
        seen: list[str] = []
        for ch in text:
            if ch in seen:
                continue
            seen.append(ch)
            elems.append(_const_str(ch, char_span))
        typ = _t_list(_t_leaf("str"), True)
        if expect is not None and expect.get("set"):
            self._expect(node, typ, expect, "frozenset")
        return node_to_dict(_ir.list_lit([dict_to_node(e) for e in elems], _mk_type(typ), _mk_span(span), True))

    def _note_frozen(self, name: str, node: ast.expr) -> None:
        if self._binding_is_frozen(node):
            self.frozen_names.add(name)
        else:
            self.frozen_names.discard(name)

    def _binding_is_frozen(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
            return True
        if isinstance(node, ast.Name):
            if node.id in self.frozen_names:
                return True
            found = self.constants.get(node.id)
            return isinstance(found, dict) and bool(found.get("frozen"))
        if isinstance(node, ast.IfExp):
            return self._binding_is_frozen(node.body) or self._binding_is_frozen(node.orelse)
        if isinstance(node, ast.BoolOp):
            return any(self._binding_is_frozen(value) for value in node.values)
        return False

    def _bool_call(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "bool expects 1 argument(s)")
        arg = self._expr(node.args[0])
        folded = _static_builtin_fold("bool", arg, span, fence=self)
        if folded is not None:
            return folded
        kind = _node_type_kind(arg)
        if kind not in {"bool", "int", "float", "str", "bytes", "list", "dict"}:
            self.fail(node, "PS2002", f"bool() of {kind} is not lowered")
        return self._truthy(arg, span)

    def _list_call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "list expects 1 argument(s)")
        source = self._expr(node.args[0])
        src_t = source["type"]
        if _type_kind(src_t) == "class" and self.classes.get(_type_name(src_t), {}).get("iterator"):
            # list(it): drain the remaining elements and move the cursor to
            # the end, like CPython's consuming list(iterator).
            elem = src_t["elem"]
            if _type_kind(elem) == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            typ = _t_list(elem)
            if expect is not None and _type_kind(expect) == "list":
                self._expect(node, typ, expect, "list")
            itn = self._pin(source, self.prelude, span)
            int_t = _t_leaf("int")
            seq_t = _t_list(elem)
            seq_r = _attr(itn, "seq", seq_t, span)
            idx_r = _attr(itn, "idx", int_t, span)
            out = self._temp("dr")
            self.locals[out] = typ
            self.prelude.extend(
                [
                    _assign(
                        out,
                        self._abi_call(
                            "builtins",
                            "list",
                            [
                                seq_r,
                                idx_r,
                                _const_int(0, span),
                                _const_int(1, span),
                                _const_int(0, span),
                                _const_int(1, span),
                            ],
                            typ,
                            "ps_seq_slice",
                            span,
                        ),
                        span,
                    ),
                    node_to_dict(
                        _ir.setattr_stmt(dict_to_node(itn), "idx", dict_to_node(_len_call(seq_r, span)), _mk_span(span))
                    ),
                ]
            )
            return _name(out, typ, span)
        if _type_kind(src_t) != "list":
            self.fail(node, "PS2002", "list() takes a list, a set, or *args")
        elem = src_t["elem"]
        if _type_kind(elem) == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        typ = _t_list(elem)
        if expect is not None and _type_kind(expect) == "list":
            self._expect(node, typ, expect, "list")
        if _node_kind(source) == "list" and not source["type"].get("set"):
            return node_to_dict(_ir.list_lit([dict_to_node(e) for e in source["elems"]], _mk_type(typ), _mk_span(span)))
        return self._abi_call(
            "builtins",
            "list",
            [
                source,
                _const_int(0, span),
                _const_int(0, span),
                _const_int(0, span),
                _const_int(0, span),
                _const_int(1, span),
            ],
            typ,
            "ps_seq_slice",
            span,
        )

    def _set_algebra(
        self, span: dict[str, Any], left: dict[str, Any], right: dict[str, Any], which: str
    ) -> dict[str, Any]:
        """A new set from two set/list operands: `a | b`, `a & b`, `a - b`, `a ^ b`.

        The result is a fresh set; the operands are read through `_set_absorb`-style
        loops, each insert gated by a membership test against the other operand
        for inter/diff/xor (`which` is `union`, `inter`, `diff`, or `xor`)."""
        elem = left["type"]["elem"]
        typ = _t_list(elem, True)
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, node_to_dict(_ir.list_lit([], _mk_type(typ), _mk_span(span))), span))
        if which == "union":
            self.prelude.extend(self._set_absorb(dest, typ, left, span))
            self.prelude.extend(self._set_absorb(dest, typ, right, span))
            return _name(dest, typ, span)
        # inter: keep members of `right`; diff: drop them; xor: both diffs.
        pairs = [(left, right)] if which != "xor" else [(left, right), (right, left)]
        int_t = _t_leaf("int")
        for source, other in pairs:
            src = self._temp("src")
            oth = self._temp("oth")
            idx = self._temp("idx")
            length = self._temp("len")
            item = self._temp("item")
            self.locals[src] = source["type"]
            self.locals[oth] = other["type"]
            self.locals[idx] = int_t
            self.locals[length] = int_t
            self.locals[item] = elem
            item_n = _name(item, elem, span)
            body = [
                _assign(
                    item, _subscript_node(_name(src, source["type"], span), _name(idx, int_t, span), elem, span), span
                ),
                node_to_dict(
                    _ir.if_stmt(
                        dict_to_node(_contains_node(_name(oth, other["type"], span), item_n, which != "inter", span)),
                        [
                            dict_to_node(stmt)
                            for stmt in self._set_insert_stmts(_name(dest, typ, span), typ, item_n, span)
                        ],
                        [],
                        _mk_span(span),
                    )
                ),
                _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
            ]
            self.prelude.extend(
                [
                    _assign(src, source, span),
                    _assign(oth, other, span),
                    _assign(length, _len_of(_name(src, source["type"], span), int_t, span), span),
                    _assign(idx, _const_int(0, span), span),
                    node_to_dict(
                        _ir.while_stmt(
                            dict_to_node(_compare("<", _name(idx, int_t, span), _name(length, int_t, span), span)),
                            [dict_to_node(stmt) for stmt in body],
                            _mk_span(span),
                        )
                    ),
                ]
            )
        return _name(dest, typ, span)

    def _set_absorb(
        self, dest: str, typ: dict[str, Any], source: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = typ["elem"]
        int_t = _t_leaf("int")
        src = self._temp("src")
        idx = self._temp("idx")
        length = self._temp("len")
        item = self._temp("item")
        self.locals[src] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[item] = elem
        body = [
            _assign(item, _subscript_node(_name(src, source["type"], span), _name(idx, int_t, span), elem, span), span),
            *self._set_insert_stmts(_name(dest, typ, span), typ, _name(item, elem, span), span),
            _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
        ]
        return [
            _assign(src, source, span),
            _assign(length, _len_of(_name(src, source["type"], span), int_t, span), span),
            _assign(idx, _const_int(0, span), span),
            node_to_dict(
                _ir.while_stmt(
                    dict_to_node(_compare("<", _name(idx, int_t, span), _name(length, int_t, span), span)),
                    [dict_to_node(stmt) for stmt in body],
                    _mk_span(span),
                )
            ),
        ]

    def _set_insert_stmts(
        self, hay: dict[str, Any], typ: dict[str, Any], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = typ["elem"]
        slot = self._temp("elt")
        self.locals[slot] = elem
        needle = _name(slot, elem, span)
        missing = _contains_node(hay, needle, True, span)
        push = node_to_dict(
            _ir.expr_stmt(
                dict_to_node(
                    node_to_dict(
                        _ir.call(
                            _ir.module_callee("builtins", "list.append"),
                            [dict_to_node(hay), dict_to_node(needle)],
                            _mk_type(_t_leaf("none")),
                            _mk_span(span),
                            "ps_list_push",
                            "",
                            -1,
                        )
                    )
                ),
                _mk_span(span),
            )
        )
        return [
            _assign(slot, value, span),
            node_to_dict(_ir.if_stmt(dict_to_node(missing), [dict_to_node(push)], [], _mk_span(span))),
        ]

    def _one_str_pattern(self, node: ast.expr, value: dict[str, Any]) -> bool:
        """True when this is re.compile of one str constant plus constant flags.

        That is the call a function already lowers to a Pattern, keywords
        included — the wave fills them before the value reaches here. The
        stored value is that call, copied at each use. Anything else is not
        this form.
        """
        if not isinstance(node, ast.Call) or len(node.args) not in {1, 2}:
            return False
        arg = node.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            return False
        if len(node.args) == 2:
            lowered_args = value.get("args") or []
            flag = lowered_args[1] if len(lowered_args) == 2 else None
            if not isinstance(flag, dict) or _maybe_node_kind(flag) != "const" or _maybe_node_type_kind(flag) != "int":
                return False
        if _maybe_node_kind(value) != "call":
            return False
        callee = value.get("callee")
        if not isinstance(callee, dict):
            return False
        if _maybe_node_kind(callee) != "module" or callee.get("module") != "re" or callee.get("name") != "compile":
            return False
        typ = value.get("type")
        return isinstance(typ, dict) and _maybe_type_kind(typ) == "class" and typ.get("name") == "Pattern"

    def _all_any(self, node: ast.Call, span: dict[str, Any], want_all: bool) -> dict[str, Any]:
        label = "all" if want_all else "any"
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", f"{label} expects 1 argument(s)")
        arg = node.args[0]
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
            return self._all_any_comp(arg, span, want_all)
        source = self._expr(arg)
        if _node_type_kind(source) != "list":
            self.fail(node, "PS2002", f"{label}() takes a list")
        elem = source["type"]["elem"]
        if _type_kind(elem) not in {"bool", "int", "float", "str", "bytes", "list", "dict"}:
            self.fail(node, "PS2002", f"{label}() of {_show(elem)} is not lowered")
        int_t = _t_leaf("int")
        bool_t = _t_leaf("bool")
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        result = self._temp("pred")
        self.locals[seq] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[result] = bool_t
        item = _subscript_node(_name(seq, source["type"], span), _name(idx, int_t, span), elem, span)
        truth = self._truthy(item, span)
        stop = _assign(idx, _name(length, int_t, span), span)
        step = _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)
        if want_all:
            cond = node_to_dict(_ir.unary("not", dict_to_node(truth), _mk_type(bool_t), _mk_span(span)))
            hit = _const_bool(False, span)
        else:
            cond = truth
            hit = _const_bool(True, span)
        self.prelude.extend(
            [
                _assign(seq, source, span),
                _assign(idx, _const_int(0, span), span),
                _assign(length, _len_of(_name(seq, source["type"], span), int_t, span), span),
                _assign(result, _const_bool(want_all, span), span),
                node_to_dict(
                    _ir.while_stmt(
                        dict_to_node(_compare("<", _name(idx, int_t, span), _name(length, int_t, span), span)),
                        [
                            dict_to_node(
                                node_to_dict(
                                    _ir.if_stmt(
                                        dict_to_node(cond),
                                        [dict_to_node(_assign(result, hit, span)), dict_to_node(stop)],
                                        [dict_to_node(step)],
                                        _mk_span(span),
                                    )
                                )
                            )
                        ],
                        _mk_span(span),
                    )
                ),
            ]
        )
        return _name(result, bool_t, span)

    def _all_any_comp(
        self, comp: ast.GeneratorExp | ast.ListComp, span: dict[str, Any], want_all: bool
    ) -> dict[str, Any]:
        """`all`/`any` over a comprehension: the eager comp loop updates a
        bool accumulator instead of materializing the element list, so a
        bool element never builds a `list[bool]` (still fenced elsewhere)."""
        result = self._temp("pred")
        self.locals[result] = {"kind": "bool"}
        self._comp(comp, span, None, pred=(result, want_all))
        return _name(result, {"kind": "bool"}, span)


def _subscript_node(
    value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The subscript read node."""
    return node_to_dict(_ir.subscript(dict_to_node(value), dict_to_node(index), _mk_type(typ), _mk_span(span)))


def _contains_node(hay: dict[str, Any], needle: dict[str, Any], negated: bool, span: dict[str, Any]) -> dict[str, Any]:
    """The membership node."""
    return node_to_dict(
        _ir.contains(dict_to_node(hay), dict_to_node(needle), _mk_type(_t_leaf("bool")), _mk_span(span), negated)
    )


def _len_of(seq: dict[str, Any], int_t: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The builtin len call node with the given int type."""
    return node_to_dict(_ir.call(_ir.builtin("len"), [dict_to_node(seq)], _mk_type(int_t), _mk_span(span), "", "", -1))


def _const_set(source: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """A constant list becomes the set list the IR already stores, first occurrence kept."""

    if _maybe_node_kind(source) != "list":
        return None
    elems: list[dict[str, Any]] = []
    seen: list[Any] = []
    for item in source["elems"]:
        if _maybe_node_kind(item) != "const":
            return None
        if item["value"] in seen:
            continue
        seen.append(item["value"])
        elems.append(item)
    return node_to_dict(_ir.list_lit([dict_to_node(e) for e in elems], _mk_type(typ), _mk_span(span)))


def _float_special_arg(node: ast.Call) -> str | None:
    """float() of an inf or nan spelling is the float const math.inf / math.nan already use.

    Optional leading sign and surrounding ASCII whitespace. A name, a number
    string, bytes, and any other argument stay on the ordinary path.
    """
    if node.keywords or len(node.args) != 1:
        return None
    arg = node.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        return None
    body = arg.value.strip(_ASCII_SPACE)
    sign = ""
    if body[:1] in "+-":
        sign = body[0]
        body = body[1:]
    kind = _FLOAT_WORDS.get(body.lower())
    if kind is None:
        return None
    if kind == "nan":
        return "nan"
    if sign == "-":
        return "-inf"
    return "inf"
