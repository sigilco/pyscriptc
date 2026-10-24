"""Call and intrinsic lowering for the lowerer.

Split out of `__init__.py` as E0 move 4 (calls and intrinsics land together:
they share the `_call_name` / `_call_attr` dispatch). `Lowerer` composes this
mixin in. The area owns the name-call and attribute-call dispatchers, the
argument fitters, the builtin container calls (`set`, `frozenset`, `bool`,
`list`, `dict.keys`, set methods), and the intrinsic family bodies for
math, json, struct, hashlib, re patterns, and synthetic classes.

Module-level helpers used only here moved verbatim (`_const_set`,
`_const_dict_keys`, `_float_special_arg`, `_const_text`, `_capturing_groups`,
`_struct_arg_kinds`); the rest of the shared leaves stay in `_core.py`. The
boundary calls in both directions stay `self._method()` so the move is
behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.builtins import EXCEPTIONS, builtin_spec, method_spec
from pyscriptc.coro import DONE, STATE
from pyscriptc.errors import span_of
from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._classes import _type_slug
from pyscriptc.lower._core import (
    _assign,
    _attr,
    _binary_int,
    _compare,
    _const_bool,
    _const_int,
    _const_none,
    _const_str,
    _dotted,
    _has_typevar,
    _len_call,
    _name,
    _show,
    _sig_typevars,
    _subst_type,
)
from pyscriptc.lower._stdlib_shared import _mod_call, _synth_module
from pyscriptc.reflection import inspect_note, kwargs_call_note, typing_note
from pyscriptc.restlower import (
    _lock_method,
    _mk_iter,
    _queue_method,
    _static_builtin_fold,
    _thread_method,
    builtin_call,
)
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.restlower._argparse import _add_argument, _parse_args
from pyscriptc.restlower._asyncio import _task_method
from pyscriptc.restlower._asyncio_sync import _prim_method
from pyscriptc.stdlib_queue import MATH_FUNCTIONS


class CallsMixin:
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
            self._reject_keywords(node)
            args = [self._expr(arg) for arg in node.args]
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
            if binding.kind == "pyfunc":
                return self._linked_call(node, span, self.pkg_funcs[name], name)
            if binding.kind == "const" and self._ffi_module(binding.module) is not None:
                self.fail(node, "PS2002", f"{name} is a constant and is not a function")
            self.fail(node, "PS2002", f"{name} is not a function")
        if name in self.class_aliases or name in self.classes:
            return self._construct(node, span, self.class_aliases.get(name, name))
        ftype = self.locals.get(name, self.captures.get(name))
        if ftype is not None and ftype["kind"] == "func":
            # Callable annotations carry bare types, no names or defaults.
            params = [{"name": f"_{index}", "type": typ} for index, typ in enumerate(ftype["params"])]
            args = self._fill_args(node, params, name)
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
        if any(kw.arg is None for kw in node.keywords):
            self.fail(node, "PS2002", kwargs_call_note())
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            self.fail(node, "PS2002", "starred calls are not lowered")
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

    def _super_call(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        """`super().method(args)`: resolution starts one MRO step past the
        class the enclosing method text lives in (CPython's __class__ cell).
        The receiver is still self, so the emitted symbol is the one valid for
        self's static layout — the same `_emit_owner` rule every call uses.
        """
        assert isinstance(node.func, ast.Attribute)
        super_call = node.func.value
        assert isinstance(super_call, ast.Call)
        if super_call.args or super_call.keywords:
            self.fail(super_call, "PS2002", "super() takes no arguments")
        class_name, _, method_name = (self.function or "").rpartition(".")
        info = self.classes.get(class_name)
        owner = info["method_owner"].get(method_name) if info is not None else None
        self_t = self.locals.get("self", {}).get("name") if owner is not None else None
        if owner is None or self_t is None:
            self.fail(node, "PS2002", "super is not lowered")
        mro = self.classes[self_t].get("mro", [self_t])
        if owner not in mro:
            self.fail(node, "PS2002", "super is not lowered")
        method = node.func.attr
        ancestors = mro[mro.index(owner) + 1 :]
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
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{emit}.{method}"},
            "args": args,
            "type": sig[1],
            "span": span,
        }

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
            if status == "erased":
                note = typing_note(member)
                if note is not None:
                    self.fail(node, "PS2002", note)
                self.fail(node, "PS2002", f"{dotted} is a typing name and is not a value")
            if status == "pkg":
                item = self._pkg_item(node, module, member)
                if item["kind"] == "class":
                    return self._construct(node, span, str(item["name"]))
                if item["kind"] != "func":
                    self.fail(node, "PS2002", f"{dotted} is not a function")
                return self._linked_call(node, span, item, dotted)
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
            return self._island_method_call(node, span, receiver)
        # boxed:end
        if receiver["type"]["kind"] == "class" and receiver["type"].get("name") == "ArgParser":
            # argparse parser methods need the lowerer state, so they have a
            # handler instead of a fixed method row.
            if node.func.attr == "add_argument":
                return _add_argument(self, node, span, receiver)
            if node.func.attr == "parse_args":
                return _parse_args(self, node, span, receiver)
            self.fail(node, "PS2001", f"ArgParser.{node.func.attr} is not lowered")
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
                    return {"kind": "const", "value": False, "type": {"kind": "bool"}, "span": span}
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
                return {
                    "kind": "call",
                    "callee": {"kind": "method", "receiver": kind, "name": attr, "tuple": typ["name"]},
                    "args": [receiver, sep],
                    "type": typ,
                    "span": span,
                }
            if attr == "format" and kind == "str":
                # Same engine as `%`; arg layout is [kwarg names, positionals, kwarg values].
                if any(keyword.arg is None for keyword in node.keywords):
                    self.fail(node, "PS2003", "str.format **kwargs are not lowered")
                names = [
                    {"kind": "const", "value": keyword.arg, "type": {"kind": "str"}, "span": span}
                    for keyword in node.keywords
                ]
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
            return {
                "kind": "call",
                "callee": {"kind": "method", "receiver": kind, "name": node.func.attr},
                "args": [receiver, *args],
                "type": spec,
                "span": span,
            }
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
                self.prelude.append({"kind": "assign", "target": t, "value": v, "span": span})
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
                value = self._expr(ifexp, receiver["type"]["value"])
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
                self.prelude.append({"kind": "expr", "expr": call, "span": span})
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
                self.prelude.append({"kind": "assign", "target": t, "value": v, "span": span})
            if attr == "setdefault":
                val = self._expr(node.args[1], val_t)
                self._expect(node, val["type"], val_t, "dict.setdefault value")
                cond = {
                    "kind": "contains",
                    "hay": _name(d_t, dtyp, span),
                    "needle": _name(k_t, key_t, span),
                    "negated": True,
                    "type": {"kind": "bool"},
                    "span": span,
                }
                self.prelude.append(
                    {
                        "kind": "if",
                        "cond": cond,
                        "then": [
                            {
                                "kind": "setitem",
                                "container": _name(d_t, dtyp, span),
                                "index": _name(k_t, key_t, span),
                                "value": val,
                                "span": span,
                            }
                        ],
                        "else": [],
                        "span": span,
                    }
                )
                return {
                    "kind": "subscript",
                    "value": _name(d_t, dtyp, span),
                    "index": _name(k_t, key_t, span),
                    "type": val_t,
                    "span": span,
                }
            v_t = self._temp("popv").replace(".", "_")
            self.locals[v_t] = val_t
            getitem = {
                "kind": "subscript",
                "value": _name(d_t, dtyp, span),
                "index": _name(k_t, key_t, span),
                "type": val_t,
                "span": span,
            }
            delitem = self._abi_call(
                "builtins",
                "dict.delitem",
                [_name(d_t, dtyp, span), _name(k_t, key_t, span)],
                {"kind": "none"},
                "ps_map_del",
                span,
            )
            then = [
                {"kind": "assign", "target": v_t, "value": getitem, "span": span},
                {"kind": "expr", "expr": delitem, "span": span},
            ]
            if len(node.args) == 2:
                fallback = self._expr(node.args[1], val_t)
                self._expect(node, fallback["type"], val_t, "dict.pop default")
                other = [{"kind": "assign", "target": v_t, "value": fallback, "span": span}]
            else:
                other = [{"kind": "raise", "exc": "KeyError", "message": None, "span": span}]
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
        if kind == "class":
            return self._method_call(node, span, receiver)
        self.fail(node, "PS2001", f"call of attribute is not lowered ({_dotted(node.func)})")
        raise AssertionError("unreachable")

    def _math_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        spec = MATH_FUNCTIONS.get(attr)
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
        pattern = {"kind": "attr", "value": receiver, "name": "pattern", "type": {"kind": "str"}, "span": span}
        flags = {"kind": "attr", "value": receiver, "name": "flags", "type": {"kind": "int"}, "span": span}
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
            count = (
                self._expr(node.args[2])
                if len(node.args) == 3
                else {"kind": "const", "value": 0, "type": {"kind": "int"}, "span": span}
            )
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
            maxsplit = (
                self._expr(node.args[1])
                if len(node.args) == 2
                else {"kind": "const", "value": 0, "type": {"kind": "int"}, "span": span}
            )
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
            index = {"kind": "const", "value": 0, "type": {"kind": "int"}, "span": span}
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
                        "value": got,
                        "span": span,
                    },
                ],
                "else": [],
                "span": span,
            }
        )
        return res

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

    def _method_call(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        class_name = receiver["type"]["name"]
        method = node.func.attr
        if class_name not in self.classes:
            self.fail(node, "PS2002", f"{class_name} did not lower")
        info = self.classes[class_name]
        sig = info["methods"].get(method)
        kind = info.get("method_kind", {}).get(method, "method")
        if sig is None or method == "__init__" or kind == "property":
            if kind == "property":
                self.fail(node, "PS2002", "a property is not called")
            self.fail(node, "PS2001", f"{class_name}.{method} is not a method")
        if kind in {"static", "class"}:
            owner = info.get("method_owner", {}).get(method, class_name)
            args = self._fill_args(node, sig[0], f"{class_name}.{method}")
        else:
            owner = self._emit_owner(class_name, method)
            params = [param for param in sig[0] if param["name"] != "self"]
            args = [receiver, *self._fill_args(node, params, f"{class_name}.{method}")]
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{owner}.{method}"},
            "args": args,
            "type": sig[1],
            "span": span,
        }

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
            {
                "kind": "setattr",
                "object": receiver,
                "name": STATE,
                "value": _const_int(DONE, span),
                "span": span,
            }
        )
        self.prelude.append(
            {
                "kind": "raise",
                "exc": name,
                "message": None,
                **({"code": self.classes[name]["exc_code"]} if name not in EXCEPTIONS else {}),
                "span": span,
            }
        )
        # Unreachable after the raise; the frame's elem keeps the expression typed.
        out = self._temp("thr")
        self.locals[out] = (
            frame_t.get("elem", {"kind": "none"}) if isinstance(frame_t.get("elem"), dict) else {"kind": "none"}
        )
        return _name(out, self.locals[out], span)

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
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{owner}.{method}"},
            "args": args,
            "type": sig[1],
            "span": span,
        }

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
        self.prelude.append(
            {
                "kind": "if",
                "cond": has,
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
                        "value": got,
                        "span": span,
                    },
                ],
                "else": [],
                "span": span,
            }
        )
        return res

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

    def _reject_keywords(self, node: ast.Call) -> None:
        if node.keywords:
            self.fail(node, "PS2003", "keyword arguments are not lowered")

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

    def _fill_args(self, node: ast.Call, params: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
        if any(kw.arg is None for kw in node.keywords):
            self.fail(node, "PS2002", kwargs_call_note())
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            self.fail(node, "PS2002", "starred calls are not lowered")
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
            elif kw.arg in names:
                index = names.index(kw.arg)
                if kw.arg in seen:
                    self.fail(kw, "PS2003", f"{kw.arg} given more than once")
                seen.add(kw.arg)
                slots[index] = self._expr(kw.value, params[index]["type"])
            else:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
        args: list[dict[str, Any]] = []
        for index, param in enumerate(params):
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects {len(params)} argument(s)")
                got = param["default"]
                if param.get("optdefault"):
                    got = self._opt_box(
                        None if got["type"]["kind"] == "none" else got,
                        param["type"],
                        got.get("span", span_of(node, self.filename)),
                    )
            if not types_equal(got["type"], param["type"]) and not self._accepts_protocol(param["type"], got["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        return args

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

    def _list_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        if self._receiver_is_frozen(node, receiver):
            self.fail(node, "PS2002", "frozenset has no mutating methods")
        attr = node.func.attr
        if attr == "append":
            self._reject_keywords(node)
            if len(node.args) != 1:
                self.fail(node, "PS2003", "list.append expects 1 argument(s)")
            if receiver["kind"] != "name":
                self.fail(node, "PS2002", "list.append needs a name")
            elem = receiver["type"]["elem"]
            value = self._expr(node.args[0], elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", "list.append takes an element of that list")
            self.prelude.extend(
                [
                    {
                        "kind": "expr",
                        "expr": {
                            "kind": "call",
                            "callee": {"kind": "module", "module": "builtins", "name": "list.append"},
                            "args": [receiver, value],
                            "symbol": "ps_list_push",
                            "type": {"kind": "none"},
                            "span": span,
                        },
                        "span": span,
                    }
                ]
            )
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
            self.prelude.append({"kind": "expr", "expr": call, "span": span})
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
                self.prelude.append({"kind": "expr", "expr": call, "span": span})
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
                self.prelude.append({"kind": "expr", "expr": call, "span": span})
                return _const_none(span)
            if attr == "remove":
                if len(node.args) != 1:
                    self.fail(node, "PS2003", "list.remove expects 1 argument(s)")
                value = self._expr(node.args[0], elem)
                self._expect(node, value["type"], elem, "list.remove element")
                call = self._abi_call(
                    "builtins", "list.remove", [receiver, value], {"kind": "none"}, f"ps_seq_remove_{suffix}", span
                )
                self.prelude.append({"kind": "expr", "expr": call, "span": span})
                return _const_none(span)
            if attr == "reverse":
                if node.args:
                    self.fail(node, "PS2003", "list.reverse expects 0 argument(s)")
                call = self._abi_call("builtins", "list.reverse", [receiver], {"kind": "none"}, "ps_seq_reverse", span)
                self.prelude.append({"kind": "expr", "expr": call, "span": span})
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
            self.prelude.append(
                {
                    "kind": "assign",
                    "target": receiver["id"],
                    "value": {"kind": "list", "elems": [], "type": receiver["type"], "span": span},
                    "span": span,
                }
            )
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
            if receiver["kind"] != "name":
                self.fail(node, "PS2002", f"set.{attr} needs a name")
            elem = receiver["type"]["elem"]
            value = self._expr(node.args[0], elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", f"set.{attr} takes an element of that set")
            if attr == "add":
                self.prelude.extend(self._set_insert_stmts(receiver["id"], receiver["type"], value, span))
                return _const_none(span)
            # discard removes when present; remove raises KeyError when absent.
            slot = self._temp("elt")
            self.locals[slot] = elem
            hay = _name(receiver["id"], receiver["type"], span)
            needle = _name(slot, elem, span)
            drop = _mod_call("builtins", "list.remove", [hay, needle], {"kind": "none"}, span)
            drop["symbol"] = f"ps_seq_remove_{'i64' if elem['kind'] == 'int' else 'buf'}"
            else_body = (
                [{"kind": "raise", "exc": "KeyError", "message": None, "span": span}] if attr == "remove" else []
            )
            self.prelude.append(_assign(slot, value, span))
            self.prelude.append(
                {
                    "kind": "if",
                    "cond": {
                        "kind": "contains",
                        "hay": _name(receiver["id"], receiver["type"], span),
                        "needle": needle,
                        "negated": False,
                        "type": {"kind": "bool"},
                        "span": span,
                    },
                    "then": [{"kind": "expr", "expr": drop, "span": span}],
                    "else": else_body,
                    "span": span,
                }
            )
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
