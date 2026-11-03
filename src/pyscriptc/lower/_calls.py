"""Call and intrinsic lowering for the lowerer.

Split out of `__init__.py` as E0 move 4 (calls and intrinsics land together:
they share the `_call_name` / `_call_attr` dispatch). `Lowerer` composes this
mixin in. The area owns the name-call dispatcher, the argument fitters, the
builtin container calls (`set`, `frozenset`, `bool`, `list`), and the
intrinsic family bodies for math, json, struct and hashlib. The
attribute-call family moved verbatim to `lower._call_attr`, which composes
here as `CallAttrMixin`.

Module-level helpers used only here moved verbatim (`_const_set`,
`_float_special_arg`, `_const_text`, `_capturing_groups`,
`_struct_arg_kinds`); the rest of the shared leaves stay in `_core.py`. The
boundary calls in both directions stay `self._method()` so the move is
behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.builtins import builtin_spec
from pyscriptc.errors import span_of
from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._annotations import _type_slug
from pyscriptc.lower._call_attr import CallAttrMixin
from pyscriptc.lower._core import (
    _assign,
    _attr,
    _binary_int,
    _compare,
    _const_bool,
    _const_int,
    _const_none,
    _const_str,
    _has_typevar,
    _len_call,
    _name,
    _show,
    _sig_typevars,
    _subst_type,
)
from pyscriptc.lower._stdlib_shared import _mod_call
from pyscriptc.reflection import kwargs_call_note, typing_note
from pyscriptc.restlower import _static_builtin_fold, builtin_call
from pyscriptc.stdlib_queue import math_function


class CallsMixin(CallAttrMixin):
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
                    return {"kind": "const", "value": special, "type": {"kind": "float"}, "span": span}
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
                kind = args[0]["type"]["kind"]
                if kind not in {"str", "bytes"}:
                    self.fail(node, "PS2003", "int with an explicit base takes a str or bytes value")
                if len(node.args) == 1:
                    base_node = next(kw.value for kw in node.keywords if kw.arg == "base")
                    if isinstance(base_node, ast.Constant) and isinstance(base_node.value, int):
                        if base_node.value == 0:
                            self.fail(node, "PS2003", "int base 0 is not lowered")
                        if base_node.value < 2 or base_node.value > 36:
                            self.fail(node, "PS2003", "int lowers base 2 to 36 only")
                    args.append(self._expr(base_node, {"kind": "int"}))
                elif args[1]["type"]["kind"] != "int":
                    self.fail(node, "PS2003", "int base is int")
                return self._abi_call("builtins", "int", args, {"kind": "int"}, "ps_int_from_base", span)
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
                if name in {"str", "len"} and len(args) == 1 and args[0]["type"]["kind"] == "class":
                    called = self._dunder_call(node, args[0], {"str": "__str__", "len": "__len__"}[name], [], span)
                    if called is None and name == "str":
                        called = self._dunder_call(node, args[0], "__repr__", [], span)
                    if called is not None:
                        return called
                self.fail(node, "PS2003", f"{name} is not lowered for these arguments")
            if name == "float" and args[0]["type"]["kind"] == "int":
                return {"kind": "cast", "op": "int_to_float", "value": args[0], "type": spec, "span": span}
            if name == "str" and args[0]["type"]["kind"] == "str":
                return args[0]
            if name == "str" and args[0]["type"]["kind"] == "none":
                # None is not a runtime value; str(None) folds at lowering.
                return {"kind": "const", "value": "None", "type": spec, "span": span}
            if name == "int" and args[0]["type"]["kind"] in {"bytes", "str"}:
                return self._abi_call("builtins", "int", args, spec, "ps_int_from_bytes", span)
            return {
                "kind": "call",
                "callee": {"kind": "builtin", "name": name},
                "args": args,
                "type": spec,
                "span": span,
            }
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
                return self._linked_call(node, span, self.pkg_funcs[name], name)
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
        if ftype is not None and ftype["kind"] == "func":
            args = self._fill_args(node, self._callable_params(ftype), name)
            return {
                "kind": "call",
                "callee": {"kind": "value", "target": _name(name, ftype, span)},
                "args": args,
                "type": ftype["return"],
                "span": span,
            }
        slot = self.global_slots.get(name)
        if slot is not None and slot.get("active") and slot["type"]["kind"] == "func":
            # A module-level callable bind (`h = f`, `@dec def`, partial):
            # read the pair cell back through the global slot.
            ftype = slot["type"]
            params = [{"name": f"_{index}", "type": typ} for index, typ in enumerate(ftype["params"])]
            args = self._fill_args(node, params, name)
            return {
                "kind": "call",
                "callee": {"kind": "value", "target": self._global_get(name, span)},
                "args": args,
                "type": ftype["return"],
                "span": span,
            }
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
            return {
                "kind": "call",
                "callee": {"kind": "function", "name": self._ir_name(name)},
                "args": args,
                "type": ret,
                "span": span,
            }
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
        if not typ.get("optional") or typ["elem"]["kind"] not in {"str", "bytes", "int", "float"}:
            return arg
        if arg["kind"] != "name":
            temp = self._temp("optarg")
            self.locals[temp] = typ
            self.prelude.append(_assign(temp, arg, span))
            arg = _name(temp, typ, span)
        message = f"{name}() argument must be a string, a bytes-like object or a real number, not 'NoneType'"
        self.prelude.append(
            {
                "kind": "if",
                "cond": {
                    "kind": "unary",
                    "op": "not",
                    "operand": _attr(arg, "_tag", {"kind": "bool"}, span),
                    "type": {"kind": "bool"},
                    "span": span,
                },
                "then": [{"kind": "raise", "exc": "TypeError", "message": _const_str(message, span), "span": span}],
                "else": [],
                "span": span,
            }
        )
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
            if _subst_type(param["type"], mapping)["kind"] != "none"
        ]
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": self._ir_name(emit)},
            "args": args,
            "type": _subst_type(ret, mapping),
            "span": span,
        }

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
        if want["kind"] == "typevar":
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
        if want["kind"] == "list" and got["kind"] == "list":
            self._unify(got["elem"], want["elem"], mapping, node, arg_node)
            return
        if want["kind"] == "dict" and got["kind"] == "dict":
            self._unify(got["key"], want["key"], mapping, node, arg_node)
            self._unify(got["value"], want["value"], mapping, node, arg_node)
            return
        if want["kind"] == "func" and got["kind"] == "func":
            for got_param, want_param in zip(got["params"], want["params"], strict=False):
                self._unify(got_param, want_param, mapping, node, arg_node)
            self._unify(got["return"], want["return"], mapping, node, arg_node)
            return
        if (
            want["kind"] == "class"
            and got["kind"] == "class"
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
            args.append({"kind": "list", "elems": elems, "type": var_type, "span": span_of(node, self.filename)})
        return args

    def _math_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        spec = math_function(attr)
        if spec is None:
            self.fail(node, "PS2001", f"math.{attr} is not in the lowered subset of math")
        if len(node.args) != len(spec["params"]):
            self.fail(node, "PS2003", f"math.{attr} expects {len(spec['params'])} argument(s)")
        args = []
        for arg_node, want in zip(node.args, spec["params"], strict=True):
            arg = self._expr(arg_node)
            if want["kind"] == "float" and arg["type"]["kind"] == "int":
                arg = {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
            if not types_equal(arg["type"], want):
                self.fail(node, "PS1003", f"math.{attr} argument is {_show(arg['type'])}, expected {_show(want)}")
            args.append(arg)
        return _mod_call("math", attr, args, spec["return"], span)

    def _math_gcd(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.args) != 2:
            self.fail(node, "PS2003", "math.gcd expects 2 argument(s)")
        args = [self._expr(arg_node) for arg_node in node.args]
        for arg in args:
            if arg["type"]["kind"] != "int":
                self.fail(node, "PS1003", f"math.gcd argument is {_show(arg['type'])}, expected int")
        return self._abi_call("math", "gcd", args, {"kind": "int"}, "ps_math_gcd", span)

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
            if arg["type"]["kind"] == "int":
                arg = {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
            if arg["type"]["kind"] != "float":
                self.fail(node, "PS1003", f"math.isclose argument is {_show(arg['type'])}, expected float")
            args.append(arg)
        for index, name in enumerate(defaults, start=2):
            if args[index] is None:
                args[index] = {"kind": "const", "value": defaults[name], "type": {"kind": "float"}, "span": span}
        return self._abi_call("math", "isclose", args, {"kind": "bool"}, "ps_math_isclose", span)

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
                    param["default"] = {"kind": "const", "value": default, "type": want, "span": span}
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
                args.append({"kind": "const", "value": default, "type": {"kind": "str"}, "span": span})
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
        if want["kind"] == "buf":
            if got["kind"] not in {"str", "bytes"}:
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
        if arg["type"]["kind"] != "str":
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
        if handle["type"]["kind"] != "class" or handle["type"].get("name") not in {"TextIO", "BytesIO"}:
            self.fail(node, "PS1003", "json.load argument is a file handle (TextIO or BytesIO)")
        kind = {"kind": "bytes"} if handle["type"]["name"] == "BytesIO" else {"kind": "str"}
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
        if handle["type"]["kind"] != "class" or handle["type"].get("name") not in {"TextIO", "BytesIO"}:
            self.fail(node, "PS1003", "json.dump argument is a file handle (TextIO or BytesIO)")
        text = _mod_call("json", "dumps", [arg], {"kind": "str"}, span)
        flags = self._json_dumps_flags(node)
        if flags:
            text["json_flags"] = flags
        write = self._abi_call("io", "write", [handle, text], {"kind": "int"}, "ps_io_write", span)
        self.prelude.append({"kind": "expr", "expr": write, "span": span})
        return _const_none(span)

    def _json_dumps(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.args) != 1:
            self.fail(node, "PS2003", "json.dumps expects 1 argument(s)")
        arg = self._expr(node.args[0])
        self._json_type(node, arg["type"], "json.dumps")
        expr = _mod_call("json", "dumps", [arg], {"kind": "str"}, span)
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
        kind = typ["kind"]
        if kind in {"int", "bool", "str", "float"}:
            return
        if typ.get("optional"):
            self._json_type(node, typ["elem"], what)
            return
        if kind == "list":
            if typ["elem"]["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            self._json_type(node, typ["elem"], what)
            return
        if kind == "dict":
            if typ["key"]["kind"] != "str":
                self.fail(node, "PS2001", "JSON object keys are str")
            if typ["value"]["kind"] == "bool":
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
        format_arg = self._expr(node.args[0])
        if attr == "pack":
            if len(node.args) - 1 != len(kinds):
                self.fail(node, "PS2003", f"struct.pack expects {len(kinds)} value(s) for this format")
            args = [format_arg]
            for kind, arg_node in zip(kinds, node.args[1:], strict=True):
                arg = self._expr(arg_node)
                got = arg["type"]["kind"]
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
            return _mod_call("struct", "pack", args, {"kind": "bytes"}, span)
        if len(node.args) != 2:
            self.fail(node, "PS2003", "struct.unpack expects 2 argument(s)")
        distinct = set(kinds)
        if len(distinct) > 1:
            self.fail(node, "PS2001", "struct.unpack result types are mixed")
        elem = "int" if not distinct else next(iter(distinct))
        buffer = self._expr(node.args[1])
        if buffer["type"]["kind"] not in {"bytes", "str"}:
            self.fail(node, "PS1003", "struct.unpack buffer is bytes")
        return _mod_call(
            "struct",
            "unpack",
            [format_arg, buffer],
            {"kind": "list", "elem": {"kind": elem}},
            span,
        )

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
                {"name": "pattern", "type": {"kind": "str"}},
                {"name": "string", "type": {"kind": "str"}},
                {
                    "name": "flags",
                    "type": {"kind": "int"},
                    "default": {"kind": "const", "value": 0, "type": {"kind": "int"}, "span": span},
                },
            ]
            args = self._fill_args(node, params, f"re.{attr}")
        opt_t = self._opt_type({"kind": "class", "name": "Match"}, node)
        res = self._opt_box(None, opt_t, span)
        dest = res["id"]
        hit = self._abi_call("re", f"{attr}.hit", args, {"kind": "bool"}, f"ps_re_{attr}_hit", span)
        made = self._abi_call("re", f"{attr}.make", args, opt_t["elem"], f"ps_re_{attr}_make", span)
        self.prelude.append(
            {
                "kind": "if",
                "cond": hit,
                "then": [
                    {
                        "kind": "setattr",
                        "object": _name(dest, opt_t, span),
                        "name": "_tag",
                        "value": _const_bool(True, span),
                        "span": span,
                    },
                    {
                        "kind": "setattr",
                        "object": _name(dest, opt_t, span),
                        "name": "_val",
                        "value": made,
                        "span": span,
                    },
                ],
                "else": [],
                "span": span,
            }
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
            zero = {"kind": "const", "value": 0, "type": {"kind": "int"}, "span": span}
            params = [
                {"name": "pattern", "type": {"kind": "str"}},
                {"name": "repl", "type": {"kind": "str"}},
                {"name": "string", "type": {"kind": "str"}},
                {"name": "count", "type": {"kind": "int"}, "default": zero},
                {"name": "flags", "type": {"kind": "int"}, "default": zero},
            ]
            args = self._fill_args(node, params, "re.subn")
        tup = self._tuple_type([{"kind": "str"}, {"kind": "int"}], node)
        return self._abi_call("re", "subn", args, tup, "ps_re_subn", span)

    def _group_tuple(self, node: ast.AST, pattern: ast.expr | None) -> tuple[dict[str, Any], str | None]:
        text = self._pattern_literal(pattern) if pattern is not None else None
        groups = _capturing_groups(text) if text is not None else 0
        if groups > 4:
            self.fail(node, "PS2002", "re.findall with more than four capturing groups is not lowered")
        if groups >= 2:
            elem = self._tuple_type([{"kind": "str"} for _ in range(groups)], node)
            return {"kind": "list", "elem": elem}, "ps_re_findall_groups"
        return {"kind": "list", "elem": {"kind": "str"}}, None

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
        self.locals[out] = {"kind": "str"}
        elem = arg["type"]["elem"]
        value = _attr(arg, "_val", elem, span)
        tagged_str: dict[str, Any] | None = None
        if elem["kind"] == "class":
            tagged_str = self._dunder_call(node, value, "__str__", [], span)
            if tagged_str is None:
                tagged_str = self._dunder_call(node, value, "__repr__", [], span)
            if tagged_str is None:
                self.fail(node, "PS2003", "str is not lowered for this optional element type")
        else:
            tagged_str = {
                "kind": "call",
                "callee": {"kind": "builtin", "name": "str"},
                "args": [value],
                "type": {"kind": "str"},
                "span": span,
            }
        self.prelude.append(
            {
                "kind": "if",
                "cond": _attr(arg, "_tag", {"kind": "bool"}, span),
                "then": [_assign(out, tagged_str, span)],
                "else": [_assign(out, _const_str("None", span), span)],
                "span": span,
            }
        )
        return _name(out, {"kind": "str"}, span)

    def _abi_call(
        self,
        module: str,
        name: str,
        args: list[dict[str, Any]],
        typ: dict[str, Any],
        symbol: str,
        span: dict[str, Any],
    ) -> dict[str, Any]:
        expr = _mod_call(module, name, args, typ, span)
        if symbol:
            expr["symbol"] = symbol
        return expr

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
                if got.get("kind") == "tuple-default":
                    # A fixed-shape tuple default: each call site that omits
                    # the argument materializes its own record (CPython
                    # shares one tuple; value-identical replay here).
                    got = self._materialize_tuple(list(got["elems"]), span_of(node, self.filename), node)
                    self._expect(node, got["type"], param["type"], param["name"])
                elif param.get("optdefault"):
                    got = self._opt_box(
                        None if got["type"]["kind"] == "none" else got,
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
            if value["kind"] in {"const", "name"}:
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
            args.append({"kind": "list", "elems": elems, "type": varparam["type"], "span": span})
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
                key = {"kind": "const", "value": kw.arg, "type": {"kind": "str"}, "span": span}
                pairs.append({"key": key, "value": value})
            args.append({"kind": "dict", "pairs": pairs, "type": kwparam["type"], "span": span})
        return args

    def _set_call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) > 1:
            self.fail(node, "PS2003", "set expects 1 argument(s)")
        if not node.args:
            if expect is None or not expect.get("set"):
                self.fail(node, "PS1001", "an empty set needs a set annotation")
            return {"kind": "list", "elems": [], "type": expect, "span": span}
        source = self._expr(node.args[0])
        if source["type"]["kind"] != "list":
            self.fail(node, "PS2002", "set() takes a list of int or str")
        elem = source["type"]["elem"]
        if elem["kind"] not in {"int", "str"} or source["type"].get("set"):
            self.fail(node, "PS2002", "set elements are int or str")
        typ = {"kind": "list", "elem": elem, "set": True}
        if expect and expect.get("set"):
            self._expect(node, typ, expect, "set")
        folded = _const_set(source, typ, span)
        if folded is not None:
            return folded
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, {"kind": "list", "elems": [], "type": typ, "span": span}, span))
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
        typ = {"kind": "list", "elem": {"kind": kind}, "set": True}
        if expect is not None and expect.get("set"):
            self._expect(node, typ, expect, "frozenset")
        return {"kind": "list", "elems": elems, "type": typ, "span": span, "frozen": True}

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
            elems.append({"kind": "const", "value": ch, "type": {"kind": "str"}, "span": char_span})
        typ = {"kind": "list", "elem": {"kind": "str"}, "set": True}
        if expect is not None and expect.get("set"):
            self._expect(node, typ, expect, "frozenset")
        return {"kind": "list", "elems": elems, "type": typ, "span": span, "frozen": True}

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
        kind = arg["type"]["kind"]
        if kind not in {"bool", "int", "float", "str", "bytes", "list", "dict"}:
            self.fail(node, "PS2002", f"bool() of {kind} is not lowered")
        return self._truthy(arg, span)

    def _list_call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "list expects 1 argument(s)")
        source = self._expr(node.args[0])
        src_t = source["type"]
        if src_t["kind"] == "class" and self.classes.get(src_t["name"], {}).get("iterator"):
            # list(it): drain the remaining elements and move the cursor to
            # the end, like CPython's consuming list(iterator).
            elem = src_t["elem"]
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            typ = {"kind": "list", "elem": elem}
            if expect is not None and expect["kind"] == "list":
                self._expect(node, typ, expect, "list")
            itn = self._pin(source, self.prelude, span)
            int_t = {"kind": "int"}
            seq_t = {"kind": "list", "elem": elem}
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
                    {
                        "kind": "setattr",
                        "object": itn,
                        "name": "idx",
                        "value": _len_call(seq_r, span),
                        "span": span,
                    },
                ]
            )
            return _name(out, typ, span)
        if src_t["kind"] != "list":
            self.fail(node, "PS2002", "list() takes a list, a set, or *args")
        elem = src_t["elem"]
        if elem["kind"] == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        typ = {"kind": "list", "elem": elem}
        if expect is not None and expect["kind"] == "list":
            self._expect(node, typ, expect, "list")
        if source["kind"] == "list" and not source["type"].get("set"):
            return {"kind": "list", "elems": list(source["elems"]), "type": typ, "span": span}
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
        typ = {"kind": "list", "elem": elem, "set": True}
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, {"kind": "list", "elems": [], "type": typ, "span": span}, span))
        if which == "union":
            self.prelude.extend(self._set_absorb(dest, typ, left, span))
            self.prelude.extend(self._set_absorb(dest, typ, right, span))
            return _name(dest, typ, span)
        # inter: keep members of `right`; diff: drop them; xor: both diffs.
        pairs = [(left, right)] if which != "xor" else [(left, right), (right, left)]
        int_t = {"kind": "int"}
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
                    item,
                    {
                        "kind": "subscript",
                        "value": _name(src, source["type"], span),
                        "index": _name(idx, int_t, span),
                        "type": elem,
                        "span": span,
                    },
                    span,
                ),
                {
                    "kind": "if",
                    "cond": {
                        "kind": "contains",
                        "hay": _name(oth, other["type"], span),
                        "needle": item_n,
                        "negated": which != "inter",
                        "type": {"kind": "bool"},
                        "span": span,
                    },
                    "then": self._set_insert_stmts(dest, typ, item_n, span),
                    "else": [],
                    "span": span,
                },
                _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
            ]
            self.prelude.extend(
                [
                    _assign(src, source, span),
                    _assign(oth, other, span),
                    _assign(
                        length,
                        {
                            "kind": "call",
                            "callee": {"kind": "builtin", "name": "len"},
                            "args": [_name(src, source["type"], span)],
                            "type": int_t,
                            "span": span,
                        },
                        span,
                    ),
                    _assign(idx, _const_int(0, span), span),
                    {
                        "kind": "while",
                        "cond": _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                        "body": body,
                        "span": span,
                    },
                ]
            )
        return _name(dest, typ, span)

    def _set_absorb(
        self, dest: str, typ: dict[str, Any], source: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = typ["elem"]
        int_t = {"kind": "int"}
        src = self._temp("src")
        idx = self._temp("idx")
        length = self._temp("len")
        item = self._temp("item")
        self.locals[src] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[item] = elem
        body = [
            _assign(
                item,
                {
                    "kind": "subscript",
                    "value": _name(src, source["type"], span),
                    "index": _name(idx, int_t, span),
                    "type": elem,
                    "span": span,
                },
                span,
            ),
            *self._set_insert_stmts(dest, typ, _name(item, elem, span), span),
            _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span),
        ]
        return [
            _assign(src, source, span),
            _assign(
                length,
                {
                    "kind": "call",
                    "callee": {"kind": "builtin", "name": "len"},
                    "args": [_name(src, source["type"], span)],
                    "type": int_t,
                    "span": span,
                },
                span,
            ),
            _assign(idx, _const_int(0, span), span),
            {
                "kind": "while",
                "cond": _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                "body": body,
                "span": span,
            },
        ]

    def _set_insert_stmts(
        self, dest: str, typ: dict[str, Any], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = typ["elem"]
        slot = self._temp("elt")
        self.locals[slot] = elem
        needle = _name(slot, elem, span)
        hay = _name(dest, typ, span)
        missing = {
            "kind": "contains",
            "hay": hay,
            "needle": needle,
            "negated": True,
            "type": {"kind": "bool"},
            "span": span,
        }
        push = {
            "kind": "expr",
            "expr": {
                "kind": "call",
                "callee": {"kind": "module", "module": "builtins", "name": "list.append"},
                "args": [hay, needle],
                "symbol": "ps_list_push",
                "type": {"kind": "none"},
                "span": span,
            },
            "span": span,
        }
        return [
            _assign(slot, value, span),
            {"kind": "if", "cond": missing, "then": [push], "else": [], "span": span},
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
            if not isinstance(flag, dict) or flag.get("kind") != "const" or flag.get("type", {}).get("kind") != "int":
                return False
        if value.get("kind") != "call":
            return False
        callee = value.get("callee")
        if not isinstance(callee, dict):
            return False
        if callee.get("kind") != "module" or callee.get("module") != "re" or callee.get("name") != "compile":
            return False
        typ = value.get("type")
        return isinstance(typ, dict) and typ.get("kind") == "class" and typ.get("name") == "Pattern"

    def _all_any(self, node: ast.Call, span: dict[str, Any], want_all: bool) -> dict[str, Any]:
        label = "all" if want_all else "any"
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", f"{label} expects 1 argument(s)")
        source = self._expr(node.args[0])
        if source["type"]["kind"] != "list":
            self.fail(node, "PS2002", f"{label}() takes a list")
        elem = source["type"]["elem"]
        if elem["kind"] not in {"bool", "int", "float", "str", "bytes", "list", "dict"}:
            self.fail(node, "PS2002", f"{label}() of {_show(elem)} is not lowered")
        int_t = {"kind": "int"}
        bool_t = {"kind": "bool"}
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        result = self._temp("pred")
        self.locals[seq] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[result] = bool_t
        item = {
            "kind": "subscript",
            "value": _name(seq, source["type"], span),
            "index": _name(idx, int_t, span),
            "type": elem,
            "span": span,
        }
        truth = self._truthy(item, span)
        stop = _assign(idx, _name(length, int_t, span), span)
        step = _assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)
        if want_all:
            cond = {"kind": "unary", "op": "not", "operand": truth, "type": bool_t, "span": span}
            hit = _const_bool(False, span)
        else:
            cond = truth
            hit = _const_bool(True, span)
        self.prelude.extend(
            [
                _assign(seq, source, span),
                _assign(idx, _const_int(0, span), span),
                _assign(
                    length,
                    {
                        "kind": "call",
                        "callee": {"kind": "builtin", "name": "len"},
                        "args": [_name(seq, source["type"], span)],
                        "type": int_t,
                        "span": span,
                    },
                    span,
                ),
                _assign(result, _const_bool(want_all, span), span),
                {
                    "kind": "while",
                    "cond": _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    "body": [
                        {
                            "kind": "if",
                            "cond": cond,
                            "then": [_assign(result, hit, span), stop],
                            "else": [step],
                            "span": span,
                        }
                    ],
                    "span": span,
                },
            ]
        )
        return _name(result, bool_t, span)


def _const_set(source: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """A constant list becomes the set list the IR already stores, first occurrence kept."""

    if source.get("kind") != "list":
        return None
    elems: list[dict[str, Any]] = []
    seen: list[Any] = []
    for item in source["elems"]:
        if item.get("kind") != "const":
            return None
        if item["value"] in seen:
            continue
        seen.append(item["value"])
        elems.append(item)
    return {"kind": "list", "elems": elems, "type": typ, "span": span}


_ASCII_SPACE = " \t\n\r\v\f"
_FLOAT_WORDS = {"inf": "inf", "infinity": "inf", "nan": "nan"}


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
