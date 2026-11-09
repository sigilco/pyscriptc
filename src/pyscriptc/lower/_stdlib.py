"""Stdlib import binding and module dispatch for the lowerer.

Split out of `__init__.py` as the first E0 move commit. `Lowerer` composes
this mixin in. The area owns module call dispatch, `_resolve_module`, and the
fences that keep queued stdlib modules out of the IR. The import-binding
family (`_bind_import`, `_bind_from`, and their helpers) moved verbatim to
`lower._stdlib_bind`, which composes here as `StdlibBindMixin`.

Shared module-level helpers (`Fence`, `Binding`, `_package_base`,
`_is_none_expr`) live in `_core.py`; binding-side helpers shared with the
static-statement walk live in `_stdlib_shared.py`. The boundary calls in both
directions stay `self._method()` so the move is behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any, NoReturn

from pyscriptc import _irnode as _ir
from pyscriptc._irnode import t_class, t_leaf, t_list

# boxed:begin
from pyscriptc.boxed import (  # boxed:begin
    DEF_HANDLE_SYMBOL,
    INVOKE_SYMBOL,
    MOD_DEF_HANDLE_SYMBOL,
    island_class_type,
    island_handle_type,
)  # boxed:end
from pyscriptc.coro import enabled

# boxed:end
from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import FfiManifest, is_handle_type, type_ir
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _MISSING,
    Binding,
    Fence,
    _assign,
    _const_bool,
    _const_bytes,
    _const_float,
    _const_int,
    _const_str,
    _const_truthy,
    _is_none_expr,
    _make_const,
    _mk_span,
    _mk_type,
    _module_closed,
    _name,
    _raw_const,
    _show,
    module_needs_slot,
)
from pyscriptc.lower._misc_emit import _one_identity_note
from pyscriptc.lower._stdlib_bind import StdlibBindMixin
from pyscriptc.restlower import alias_name, rest_call
from pyscriptc.stdlib_queue import (
    ffi_manifests,
    wave_function,
)
from pyscriptc.stdmore import (
    PTR_CLASSES,
    REST_MODULES,
    member_callable,
    module_constant,
)
from pyscriptc.tirserde import dict_to_node, node_to_dict


# boxed:begin
def _island_handle(name: str) -> dict[str, Any]:
    """The opaque handle type of an island value (issue #553)."""
    return island_handle_type(name)


def _expr_stmt_node(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The expr-statement node one dict-IR `{"kind": "expr", ...}` spells."""

    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _try_stmt_node(body: list[dict[str, Any]], finalbody: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    """The try/finally statement one dict-IR `{"kind": "try", ...}` spells."""

    return node_to_dict(
        _ir.try_stmt(
            [dict_to_node(stmt) for stmt in body],
            [],
            _mk_span(span),
            None,
            [dict_to_node(stmt) for stmt in finalbody],
        )
    )


def _const_str_node(value: str, span: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The str-constant node one dict-IR `{"kind": "const", ...}` spells.

    The island keyword marshalling marks the const with extra keys the typed
    const record has no slot for, so the spelling keeps the dict whole.
    """

    node: dict[str, Any] = node_to_dict(_ir.const_str(value, t_leaf("str"), _mk_span(span)))
    if extra:
        node.update(extra)
    return node


def _island_call_node(
    module: str,
    name: str,
    args: list[dict[str, Any]],
    typ: dict[str, Any],
    symbol: str,
    span: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The island call node: a typed call plus the island's extra keys.

    The island seam carries keys the typed call record has no slot for
    (`island`, `island_fn`, `island_npos`, `island_nkw`), so the typed node
    spells the call and the extras ride along on the dict.
    """

    node: dict[str, Any] = node_to_dict(
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
    if extra:
        node.update(extra)
    return node


def _island_peek_handle(module: str, span: dict[str, Any]) -> dict[str, Any]:
    """The isolated island-module handle node the peek outside a scope reads."""

    return node_to_dict(
        _ir.name_expr(
            f"<imod:{module}>",
            t_class(f"{module} module", "", None, False, False, True),
            _mk_span(span),
        )
    )


def _set_list_literal(elems: list[dict[str, Any]], elem_kind: str, span: dict[str, Any]) -> dict[str, Any]:
    """The set-backed list node one dict-IR `{"kind": "list", "set": True}` spells."""

    return node_to_dict(
        _ir.list_lit([dict_to_node(elem) for elem in elems], t_list(t_leaf(elem_kind), True), _mk_span(span))
    )


# The `%ps.arg` kind codes the trampoline marshals (issue #553); they match
# `ps_island.c`.
_ISLAND_CODES = {"int": 1, "float": 2, "str": 3, "bool": 4, "list": 5, "bytes": 6, "handle": 7, "dict": 8}
_ISLAND_SCALARS = frozenset({"int", "float", "str", "bool", "bytes"})
# boxed:end


class StdlibDispatchMixin(StdlibBindMixin):
    def _accept_module_function(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, fences: list[Fence]) -> bool:
        """Module `@staticmethod` is the function the class decorator already emits.

        The call passes no instance. Other module decorators stay fenced.
        typing's `@overload` stubs erase (only the final undecorated
        implementation lowers); `@abstractmethod` erases off the list.
        """
        if any(self._erased_deco_name(d) == "overload" for d in fn.decorator_list):
            return False
        fn.decorator_list = [d for d in fn.decorator_list if self._erased_deco_name(d) is None]
        if isinstance(fn, ast.AsyncFunctionDef) and not fn.decorator_list and enabled():
            # A module-level coroutine: the coro pass owns it (PS2002 fences
            # from its shape check already carry the precise message).
            return True
        if self._is_contextmanager_deco(fn) and enabled():
            # `@contextmanager`: the coro pass owns it (the generator frame
            # plus the `cm.<name>` wrapper).
            return True
        message = self._module_decorator_fence(fn)
        # boxed:begin
        if self.fallback_definitions and fn.decorator_list:
            # Any user decorator (whether it fences native above or waits
            # for the deferred pass below) runs island-side: the seed carries
            # the decorated source and CPython applies it, so the decorator
            # fences drop with the definition (issue #676). A generic def
            # stays native: its monomorphizations lower, and the planner's
            # generic rules must keep applying to it. The early AST check
            # mirrors the signature pass: a declared TypeVar name in an
            # annotation is generic before `sigs` exists.
            generic = any(
                isinstance(arg.annotation, ast.Name) and arg.annotation.id in self.typevars
                for arg in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            )
            if not generic:
                self._boxed_island.add(fn.name)
        # boxed:end
        if message is None:
            if fn.decorator_list and not self._is_module_staticmethod(fn):
                # A user decorator wraps the function after the signature
                # pass: the def lowers normally, the wrap binds as a
                # callable global in the deferred pass.
                self.deferred_module.append(fn)
            return True
        fences.append(Fence("PS2002", message, span_of(fn, self.filename), fn.name))
        return False

    def _is_module_staticmethod(self, fn: ast.FunctionDef) -> bool:
        return (
            len(fn.decorator_list) == 1
            and isinstance(fn.decorator_list[0], ast.Name)
            and fn.decorator_list[0].id == "staticmethod"
        )

    def _bind_ffi(self, name: str, bound: str) -> bool:
        """Bind a `--ffi` manifest module, or return False when it is not one.

        A manifest module binds like a lowered stdlib module (one synthetic
        import-table row, `.agents/docs/ffi/ffi-manifest.md` section 3). Without
        the manifest loaded the import falls through to the normal fence: the
        closed-table refusal with its lane hint stays today's behavior.
        """
        manifest = self._ffi_module(name)
        if manifest is None:
            return False
        self.bindings[bound or name] = Binding("module", name, "", False)
        return True

    def _ffi_module(self, name: str) -> FfiManifest | None:
        return ffi_manifests().get(name)

    def _ffi_call(self, node: ast.Call, span: dict[str, Any], module: str, attr: str) -> dict[str, Any]:
        """Lower a call into a `--ffi` manifest module against its `py` signature."""
        manifest = self._ffi_module(module)
        fn = manifest.function(attr) if manifest is not None else None
        if fn is None:
            self.fail(node, "PS2001", f"{module}.{attr} is not in the FFI manifest of {module}")
        self._reject_keywords(node)
        count = len(node.args)
        if count < fn.required_arity or count > fn.arity:
            self.fail(node, "PS2003", f"{module}.{attr} expects {fn.required_arity} argument(s)")
        args: list[dict[str, Any]] = []
        consumed_locals: list[str] = []
        for index, arg_node in enumerate(node.args):
            param = fn.params[index]
            want = type_ir(param.type, manifest.handle_names)
            arg = self._expr(arg_node, want)
            if not types_equal(arg["type"], want):
                if is_handle_type(arg["type"]) and is_handle_type(want):
                    # Both opaque, the values are bare pointers: refusing at
                    # the type is the only protection the ABI gives.
                    self.fail(
                        node,
                        "PS4012",
                        f"{module}.{attr} argument {param.name} expects the handle {want['name']}, "
                        f"not {_show(arg['type'])}",
                    )
                self.fail(node, "PS1003", f"{module}.{attr} argument {param.name} is {_show(arg['type'])}")
            # Ownership is per-argument: a consumed parameter must carry a
            # handle whose owner (the local it came from) dies with the call
            # (issue #224). Borrowed or unknown-origin handles pass as is.
            if param.name in fn.consumed_params():
                owner = arg.get("id") if arg["kind"] == "name" else None
                if owner is None:
                    self.fail(
                        node,
                        "PS4011",
                        f"{module}.{attr} consumes {param.name}; the argument must be a handle variable this "
                        "function owns, so the compiler can prove it is dead afterwards",
                    )
                if owner in self.borrowed_handles:
                    self.fail(
                        node,
                        "PS4011",
                        f"{owner} is borrowed here (it is a handle parameter or an alias of one), so this "
                        f"function cannot consume it; the caller still owns the value",
                    )
                if owner not in self.handle_locals:
                    self.fail(
                        node,
                        "PS4011",
                        f"{module}.{attr} consumes {param.name}; the argument must be a handle variable this "
                        "function owns, so the compiler can prove it is dead afterwards",
                    )
                if owner in consumed_locals:
                    self.fail(node, "PS4011", f"{owner} is consumed twice in one call")
                consumed_locals.append(owner)
            args.append(arg)
        for owner in consumed_locals:
            # The consumed name is dead from here on: a later read fences
            # with PS4010 before the IR ever emits.
            del self.handle_locals[owner]
            self.consumed_handles.add(owner)
        return self._abi_call(module, attr, args, type_ir(fn.returns, manifest.handle_names), fn.abi, span)

    def _is_contextmanager_deco(self, fn: ast.FunctionDef) -> bool:
        """True when the function carries `contextlib.contextmanager`.

        The name alone is not enough: the decorator has to resolve to the
        stdlib member, so a local `contextmanager` function is not mistaken
        for it.
        """
        if len(fn.decorator_list) != 1:
            return False
        dec = fn.decorator_list[0]
        target = dec.func if isinstance(dec, ast.Call) and not dec.args and not dec.keywords else dec
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            return binding is not None and binding.module == "contextlib" and binding.attr == "contextmanager"
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            return resolved is not None and resolved[1] == "contextlib" and resolved[2] == "contextmanager"
        return False

    def _own_handle(self, name: str, typ: dict[str, Any]) -> None:
        """Record that a local owns a manifest handle.

        Ownership is per-name and per-scope: a name bound to a handle value
        owns it until a consuming call takes it, or until the scope ends
        (where the `frees` symbol runs). A name already borrowed (a handle
        parameter, or an alias of one) stays a borrow: the value belongs to
        the caller, so neither the rebinding nor the scope exit frees it
        (issue #224). Rebinding the name drops the old ownership, so the old
        handle is not freed twice.
        """
        if not is_handle_type(typ):
            return
        if name in self.borrowed_handles:
            return
        self.handle_locals[name] = typ
        self.consumed_handles.discard(name)

    def _rebind_handle(
        self, name: str, typ: dict[str, Any], value_node: ast.expr, span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """The free of a handle a rebinding drops, plus the new ownership.

        `r = other` replaces the value `r` holds. There is no refcounting
        (the design doc's rule), so the compiler frees the old handle at the
        rebinding point instead of leaking it (issue #224). A name that was
        never a handle has nothing to free, and `r = r` frees nothing: the
        value keeps its owner.

        A value copied from a borrowed name stays a borrow: `x = res` where
        `res` is a handle parameter aliases the caller's handle, so the
        scope exit must not free `x` (issue #224). The value the rebinding
        drops is still freed there: ownership of that one died with the
        rebinding, borrow or not.
        """
        old = self.handle_locals.get(name)
        borrowed_source = isinstance(value_node, ast.Name) and value_node.id in self.borrowed_handles
        self._own_handle(name, typ)
        if borrowed_source:
            # The new value is the caller's handle: `x` borrows it too.
            self.borrowed_handles.add(name)
            self.handle_locals.pop(name, None)
        if old is None or (isinstance(value_node, ast.Name) and value_node.id == name):
            return []
        return [_expr_stmt_node(self._free_call(name, old, span), span)]

    def _free_call(self, name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """The IR call that releases one owned handle.

        The free rides the manifest's own consuming thunk: the `[[fn]]` that
        declares `consumes` for this handle type is the one whose abi thunk
        releases it (the design doc's `frees` symbol is what that thunk
        calls, not what the compiler calls). Without such a thunk there is
        nothing to call, and the compiler refuses the program (PS4013)
        rather than leak the handle silently.
        """
        # boxed:begin
        if typ.get("island"):
            # The island handle's ownership ends at the seam: one trampoline
            # free releases the island object (issue #553).
            return self._abi_call("island", "free", [_name(name, typ, span)], {"kind": "none"}, "ps_island_free", span)
        # boxed:end
        module, fn = self._consuming_fn(typ["name"])
        if fn is None:
            self.fail(
                ast.Pass(),
                "PS4013",
                f"the handle {typ['name']} has no consuming function in its manifest, so scope-exit free "
                "has nothing to call; declare a [[fn]] with consumes = [this handle]",
            )
        arg = _name(name, typ, span)
        return self._abi_call(module, fn.name, [arg], {"kind": "none"}, fn.abi, span)

    def _consuming_fn(self, handle: str) -> tuple[str, Any] | None:
        """The manifest function that consumes this handle type, if one exists."""
        for module, manifest in ffi_manifests().items():
            for fn in manifest.functions:
                if any(ref.handle == handle for ref in fn.consumes):
                    return module, fn
        return None

    def _scope_exit_frees(self, body: list[dict[str, Any]], span: dict[str, Any]) -> list[dict[str, Any]]:
        """Wrap a body so owned handles free on every exit path.

        The IR has no per-scope destructor, so the free rides `try`/`finally`
        semantics: the body becomes the try body and the frees the
        finalbody, which the emitter runs on the normal path and on every
        propagation path (issue #224). A handle the scope still owns and
        did not return frees here; a consumed one already went with its
        call, a returned one belongs to the caller, and a borrowed one
        (a handle parameter or an alias of one) stays the caller's.
        """
        owned = [
            (name, typ)
            for name, typ in self.handle_locals.items()
            if name not in self.consumed_handles
            and name not in self.returned_handles
            and name not in self.borrowed_handles
        ]
        if not owned:
            return body
        frees = [_expr_stmt_node(self._free_call(name, typ, span), span) for name, typ in owned]
        return [_try_stmt_node(body, frees, span)]

    def _module_decorator_fence(self, fn: ast.FunctionDef) -> str | None:
        if not fn.decorator_list:
            return None
        if len(fn.decorator_list) != 1:
            # A stack of user decorators defers to the same wrap pass a
            # single one does (`_bind_callable_global` applies them
            # bottom-up). Under the fallback tier the island seed owns the
            # decorated source, so the stack keeps its fence there. A
            # builtin spelling anywhere in the stack keeps the stacked
            # fence: `staticmethod` is not a lowered callable.
            if self.fallback_definitions:
                return "stacked decorators are not lowered"
            for dec in fn.decorator_list:
                if (isinstance(dec, ast.Name) and dec.id in {"staticmethod", "property", "classmethod"}) or (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id in {"staticmethod", "property", "classmethod"}
                ):
                    return "stacked decorators are not lowered"
            return None
        dec = fn.decorator_list[0]
        if isinstance(dec, ast.Name):
            if dec.id == "staticmethod":
                return None
            if dec.id == "property":
                return "a module property is not lowered"
            if dec.id == "classmethod":
                return "a module classmethod is not lowered"
            return None
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Name)
            and dec.func.id in {"staticmethod", "property", "classmethod"}
        ):
            return "a decorator expression is not lowered"
        return None

    def _module_call(
        self,
        node: ast.Call,
        span: dict[str, Any],
        module: str,
        attr: str,
        expect: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self._ffi_module(module) is not None:
            return self._ffi_call(node, span, module, attr)
        if module == "enum" and attr == "auto":
            # enum.auto only lowers as an enum body member; a call anywhere
            # else has no enum to draw its counter from.
            self.fail(node, "PS2001", "enum.auto is only lowered inside an enum")
        if module == "math":
            if attr == "isclose":
                return self._math_isclose(node, span)
            self._reject_keywords(node)
            if attr == "gcd":
                return self._math_gcd(node, span)
            return self._math_call(node, span, attr)
        if module == "bisect":
            from pyscriptc.restlower._bisect import _bisect

            return _bisect(self, node, span, attr)
        if module in REST_MODULES:
            return rest_call(self, node, span, module, attr, expect)
        if module == "json" and attr == "loads":
            self._reject_keywords(node)
            return self._json_loads(node, span, expect)
        if module == "json" and attr == "load":
            return self._json_load(node, span, expect)
        if module == "json" and attr == "dump":
            return self._json_dump(node, span)
        if module == "json" and attr == "dumps":
            return self._json_dumps(node, span)
        if module == "struct" and attr in {"pack", "unpack"}:
            self._reject_keywords(node)
            return self._struct_call(node, span, attr)
        if module == "re" and attr == "findall":
            ret, symbol = self._group_tuple(node, node.args[0] if node.args else None)
            expr = self._fixed_call(node, span, module, attr, wave_function(module, attr))
            expr["type"] = ret
            if symbol:
                expr["symbol"] = symbol
            return expr
        if module == "re" and attr in {"search", "match", "fullmatch"}:
            return self._re_opt_match(node, span, attr)
        if module == "re" and attr == "subn":
            return self._re_subn(node, span)
        spec = wave_function(module, attr)
        if spec is None:
            self.fail(node, "PS2001", f"{module}.{attr} is not in the lowered subset of {module}")
        return self._fixed_call(node, span, module, attr, spec)

    # boxed:begin
    def _island_check_arg(self, node: ast.AST, arg: dict[str, Any]) -> None:
        typ = arg["type"]
        if typ.get("island"):
            return
        if typ["kind"] in _ISLAND_SCALARS:
            return
        if typ["kind"] == "list":
            elem = typ.get("elem") or {}
            if elem.get("island") or elem.get("kind") in _ISLAND_SCALARS:
                return
            self.fail(node, "PS1003", "island list arguments carry int, float, str, bytes or bool elements")
        if typ["kind"] == "dict":
            key_t = typ.get("key") or {}
            val_t = typ.get("value") or {}
            if key_t.get("kind") in _ISLAND_SCALARS and val_t.get("kind") in _ISLAND_SCALARS:
                return
            self.fail(node, "PS1003", "island dict arguments carry scalar key and value elements")
        self.fail(node, "PS1003", f"island cannot marshal {_show(typ)}")

    def _island_args(self, node: ast.Call, span: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
        pos_args: list[dict[str, Any]] = []
        for arg_node in node.args:
            if isinstance(arg_node, ast.Starred):
                arg = self._expr(arg_node.value)
                self._island_check_arg(arg_node, arg)
                if arg["type"]["kind"] != "list" and not arg["type"].get("island"):
                    self.fail(arg_node, "PS1003", f"starred argument must be list, got {_show(arg['type'])}")
                pos_args.append({**arg, "island_starred": True})
            else:
                arg = self._expr(arg_node)
                self._island_check_arg(arg_node, arg)
                pos_args.append(arg)

        kw_args: list[dict[str, Any]] = []
        for kw in node.keywords:
            if kw.arg is not None:
                val = self._expr(kw.value)
                self._island_check_arg(kw, val)
                key = _const_str_node(kw.arg, span)
                kw_args.extend([key, val])
            else:
                val = self._expr(kw.value)
                self._island_check_arg(kw, val)
                if val["type"]["kind"] != "dict" and not val["type"].get("island"):
                    self.fail(kw, "PS1003", f"**kwargs argument must be dict, got {_show(val['type'])}")
                if val["type"]["kind"] == "dict":
                    key_t = val["type"].get("key") or {}
                    if key_t.get("kind") != "str":
                        self.fail(kw, "PS1003", f"**kwargs keys must be str, got {_show(key_t)}")
                marker = _const_str_node("", span, {"island_kwargs": True})
                kw_args.extend([marker, val])

        all_args = [*pos_args, *kw_args]
        return all_args, len(pos_args), len(node.keywords)

    def _island_module_handle(self, module: str, span: dict[str, Any]) -> dict[str, Any]:
        """The hidden local that owns one imported island module.

        The import runs once per function scope, in the statement prelude;
        the handle joins `handle_locals`, so the scope-exit free releases it
        like any other island value (issue #553).

        A peek outside a lowering scope (the boxed top-level probe, the
        call-site pin pass, issues #650 and #643) has nowhere to put the
        import: the peeked type is all the caller needs, so this returns an
        isolated handle node instead of raising `KeyError` on the missing
        hidden local.

        A mapping entry whose hidden local is not in the active `locals`
        belongs to a scope that already ended (the closure scope of
        `_build_callable` swaps `locals` without swapping `island_modules`,
        so a nested def re-enters the outer scope's module handle). The
        stale entry is dropped and the handle is bound again in the active
        scope, so the nested scope owns its own import (issue #702).
        """
        # boxed:begin
        name = self.island_modules.get(module)
        if name is not None and name not in self.locals:
            # The scope that owned this handle is gone: the hidden local is
            # not a local here, so the entry is stale re-entry across
            # scopes (issue #702). Bind a fresh handle in this scope.
            del self.island_modules[module]
            name = None
        if name is None and self.peeking:
            # The peek (issues #650, #643) runs outside a lowering scope: no
            # prelude to hold the import, no scope exit to free the handle.
            # The peeked type is all the caller needs, so return an isolated
            # handle node instead of binding a hidden local.
            return _island_peek_handle(module, span)
        if name is None:
            name = self._temp("imod").replace(".", "_")
            typ = _island_handle(f"{module} module")
            self.locals[name] = typ
            call = self._abi_call(
                module,
                "import",
                [_const_str_node(module, span)],
                typ,
                "ps_island_import",
                span,
            )
            self.prelude.append(_assign(name, call, span))
            self._own_handle(name, typ)
            self.island_modules[module] = name
        return _name(name, self.locals[name], span)
        # boxed:end

    def _island_validate_expect(self, node: ast.AST, expect: dict[str, Any]) -> None:
        if expect.get("island"):
            return
        opt_note = "island cannot unmarshal Optional; typed idiom: return a default scalar or handle None on the island"
        idiom_note = "typed idiom: use int, float, bool, str, bytes, list or dict of scalars across the island boundary"
        if expect.get("optional"):
            self.fail(node, "PS1003", opt_note)
        kind = expect.get("kind")
        is_tuple = (
            kind == "tuple"
            or expect.get("label", "").startswith("tuple[")
            or (kind == "class" and bool(self.classes.get(expect.get("name", ""), {}).get("tuple")))
        )
        if is_tuple:
            self.fail(
                node,
                "PS1003",
                "island cannot unmarshal tuple; typed idiom: use list[T] to cross the island boundary",
            )
        if kind in _ISLAND_SCALARS:
            return
        if kind == "list":
            elem = expect.get("elem") or {}
            elem_tuple = (
                elem.get("kind") == "tuple"
                or elem.get("label", "").startswith("tuple[")
                or (elem.get("kind") == "class" and bool(self.classes.get(elem.get("name", ""), {}).get("tuple")))
            )
            if elem_tuple:
                self.fail(
                    node,
                    "PS1003",
                    "island cannot unmarshal tuple; typed idiom: use list[T] to cross the island boundary",
                )
            if elem.get("optional"):
                self.fail(node, "PS1003", opt_note)
            if elem.get("kind") in _ISLAND_SCALARS:
                return
            self.fail(node, "PS1003", f"island cannot unmarshal {_show(expect)}; {idiom_note}")
        if kind == "dict":
            key_t = expect.get("key") or {}
            val_t = expect.get("value") or {}
            if key_t.get("kind") in _ISLAND_SCALARS and val_t.get("kind") in _ISLAND_SCALARS:
                return
            self.fail(node, "PS1003", f"island cannot unmarshal {_show(expect)}; {idiom_note}")
        self.fail(node, "PS1003", f"island cannot unmarshal {_show(expect)}; {idiom_note}")

    # boxed:begin
    def _island_floor_call(
        self, node: ast.Call, span: dict[str, Any], module: str, name: str, expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """`helper(x)` where `helper` rides another module's island floor.

        The per-module plane split: the same marshalling and `want`
        unboxing as `_island_fn_call`, with the defining module's name in
        front of the callee key (`ps_island_mod_fn_invoke`). The caller
        stays native; the arguments cross as copies (the seam is
        copy-in), the result unboxes through `want` like the
        single-namespace trampoline.
        """
        marshalled, npos, nkw = self._island_args(node, span)
        call_expr: dict[str, Any] = _island_call_node(
            "island",
            name,
            marshalled,
            _island_handle(f"{module}.{name}()"),
            INVOKE_SYMBOL,
            span,
            {"island_fn": {"name": name, "module": module}, "island_npos": npos, "island_nkw": nkw},
        )
        if expect is not None and not expect.get("island"):
            self._island_validate_expect(node, expect)
            call_expr["type"] = expect
            call_expr["island_fn"]["want"] = expect
        return call_expr

    def _island_floor_class_handle(
        self, local_name: str, module: str, name: str, span: dict[str, Any]
    ) -> dict[str, Any]:
        """`Cls` of another module as a value: a handle from its namespace.

        The class object lives in the defining module's island namespace;
        `ps_island_mod_def_handle(mod, name)` returns a new reference to it,
        the namespaced shape of `ps_island_def_handle` (issue #628). The
        hidden local owns the reference for the scope like the in-module
        handle does.
        """
        local = self.island_class_handles.get(local_name)
        if local is not None and local not in self.locals:
            del self.island_class_handles[local_name]
            local = None
        if local is None:
            local = self._temp("icls").replace(".", "_")
            typ = island_class_type(f"{module}.{name}")
            self.locals[local] = typ
            call = self._abi_call(module, name, [], typ, MOD_DEF_HANDLE_SYMBOL, span)
            call["island_mod_def"] = {"module": module, "name": name}
            self.prelude.append(_assign(local, call, span))
            self._own_handle(local, typ)
            self.island_class_handles[local_name] = local
        return _name(local, self.locals[local], span)

    def _island_floor_class_call(
        self, node: ast.Call, span: dict[str, Any], module: str, name: str, expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """`Cls(...)` where `Cls` rides another module's island floor.

        The class object lives in the defining module's island namespace,
        where it is callable, so the instantiation is the same namespaced
        trampoline as `_island_floor_call` with the class name as the callee
        key. The instance is an opaque handle of the class's own type (the
        class's spelling carries the defining module, issue #628).
        """
        marshalled, npos, nkw = self._island_args(node, span)
        call_expr: dict[str, Any] = _island_call_node(
            "island",
            name,
            marshalled,
            island_class_type(f"{module}.{name}"),
            INVOKE_SYMBOL,
            span,
            {"island_fn": {"name": name, "module": module}, "island_npos": npos, "island_nkw": nkw},
        )
        if expect is not None and not expect.get("island"):
            self._island_validate_expect(node, expect)
            call_expr["type"] = expect
            call_expr["island_fn"]["want"] = expect
        return call_expr

    def _island_fn_call(
        self, node: ast.Call, span: dict[str, Any], name: str, expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """`helper(x)`: a call to a fenced definition the planner routed to
        the island (issue #612).

        The fence the body produced dropped out of the module fence list, so
        the caller needs this call to lower instead. The result is an opaque
        handle when the call site has no expectation; `_expr`'s unmarshal
        pass (issue #602) unwraps it into an annotated destination through
        the `want` mechanism. An island function calling a compiled sibling
        stays fenced in this stage (Decision B): that call never reaches
        here because the sibling fence already routed the caller itself.
        """
        if self.function is not None and self.function in self.island_fns:
            self.fail(
                node,
                "PS2002",
                f"{self.function} runs on the island and cannot call the compiled function "
                f"{name}: the island-to-compiled seam is the `--emit binding` surface",
            )
        marshalled, npos, nkw = self._island_args(node, span)
        # An island class instantiation returns the class's one handle type,
        # so the destination annotation proves the same type (issue #628).
        result_type = island_class_type(name) if name in self.island_classes else _island_handle(f"{name}()")
        island_fn: dict[str, Any] = {"name": name}
        # The per-module plane split: the single island namespace does not
        # exist (every module's floor is its own namespace), so the call
        # names the owning module and rides the namespaced trampoline.
        if self._boxed_plane_split and self.module_name:
            island_fn["module"] = self.module_name
        call_expr: dict[str, Any] = _island_call_node(
            "island",
            name,
            marshalled,
            result_type,
            INVOKE_SYMBOL,
            span,
            {"island_fn": island_fn, "island_npos": npos, "island_nkw": nkw},
        )
        if expect is not None and not expect.get("island"):
            self._island_validate_expect(node, expect)
            call_expr["type"] = expect
            call_expr["island_fn"]["want"] = expect
        return call_expr

    def _island_class_handle(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        """`Version` as a value: a handle to the island's class object (issue #628).

        A class-level attribute read and a class object taken as a value need
        the object itself, which is what `ps_island_def_handle` returns. The
        hidden local owns the reference for the scope, exactly like an island
        module handle, so the scope exit frees it.
        """
        local = self.island_class_handles.get(name)
        if local is not None and local not in self.locals:
            # Stale cross-scope re-entry (issue #702, same shape as the
            # module handle): the owning scope ended, so bind fresh here.
            del self.island_class_handles[name]
            local = None
        if local is None:
            local = self._temp("icls").replace(".", "_")
            typ = island_class_type(name)
            self.locals[local] = typ
            if self._boxed_plane_split and self.module_name:
                # The per-module plane split: the class object lives in this
                # module's own island namespace, so the handle is fetched
                # through the namespaced trampoline.
                call = self._abi_call(self.module_name, name, [], typ, MOD_DEF_HANDLE_SYMBOL, span)
                call["island_mod_def"] = {"module": self.module_name, "name": name}
            else:
                call = self._abi_call("island", name, [], typ, DEF_HANDLE_SYMBOL, span)
                call["island"] = {"name": name}
                call["island_def"] = {"name": name}
            self.prelude.append(_assign(local, call, span))
            self._own_handle(local, typ)
            self.island_class_handles[name] = local
        return _name(local, self.locals[local], span)

    # boxed:end

    def _island_call(
        self, node: ast.Call, span: dict[str, Any], module: str, attr: str, expect: dict[str, Any] | None
    ) -> dict[str, Any]:
        """`requests.get(...)`: a trampoline call on the imported module handle."""
        marshalled, npos, nkw = self._island_args(node, span)
        args = [self._island_module_handle(module, span), *marshalled]
        call_expr: dict[str, Any] = _island_call_node(
            module,
            attr,
            args,
            _island_handle(f"{module}.{attr}()"),
            "ps_island_call",
            span,
            {"island": {"name": attr}, "island_npos": npos, "island_nkw": nkw},
        )
        if expect is not None and not expect.get("island"):
            self._island_validate_expect(node, expect)
            call_expr["type"] = expect
            call_expr["island"]["want"] = expect
        return call_expr

    def _island_method_call(
        self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], expect: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """`response.raise_for_status(...)`: a trampoline call on one handle."""
        assert isinstance(node.func, ast.Attribute)
        attr = node.func.attr
        marshalled, npos, nkw = self._island_args(node, span)
        args = [receiver, *marshalled]
        call_expr: dict[str, Any] = _island_call_node(
            receiver["type"].get("name", "island"),
            attr,
            args,
            _island_handle(f"{receiver['type'].get('name', 'island')}.{attr}()"),
            "ps_island_call",
            span,
            {"island": {"name": attr}, "island_npos": npos, "island_nkw": nkw},
        )
        if expect is not None and not expect.get("island"):
            self._island_validate_expect(node, expect)
            call_expr["type"] = expect
            call_expr["island"]["want"] = expect
        return call_expr

    def _island_unmarshal(
        self, obj: dict[str, Any], expect: dict[str, Any], span: dict[str, Any], node: ast.AST
    ) -> dict[str, Any]:
        """Unmarshal an island handle into a typed local value."""
        self._island_validate_expect(node, expect)
        return _island_call_node(
            "island",
            "unmarshal",
            [obj],
            expect,
            "ps_island_unmarshal",
            span,
            {"island": {"name": "unmarshal", "want": expect}},
        )

    def _island_getattr(
        self, obj: dict[str, Any], name: str, span: dict[str, Any], node: ast.AST, expect: dict[str, Any] | None
    ) -> dict[str, Any]:
        """`response.status_code`: one attribute out of an island handle.

        The value's type has to come from the destination annotation: the
        island returns a boxed value and the trampoline needs to know which
        scalar to ask for (`island_getattr(handle, name, want)`).
        """
        if expect is None or expect.get("island"):
            self.fail(
                node,
                "PS1001",
                f"{name} needs an annotation (int, float, bool, str, bytes or list[...]) before use",
            )
        self._island_validate_expect(node, expect)
        expr = self._abi_call(obj["type"].get("name", "island"), name, [obj], expect, "ps_island_getattr", span)
        expr["island"] = {"name": name, "want": expect}
        return expr

    def _island_getitem(
        self,
        node: ast.Subscript,
        obj: dict[str, Any],
        index: dict[str, Any],
        span: dict[str, Any],
        expect: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """`pair["x"]`: one subscript out of an island handle (issue #612).

        The island side has real dict semantics, so the key crosses as a
        string and the value unboxes through the `want` mechanism, exactly
        like `_island_getattr`; the want type comes from the destination
        annotation via `_expr`'s `expect` plumbed into `_subscript`.
        """
        if index["type"]["kind"] != "str":
            self.fail(node, "PS1003", "an island subscript key is str")
        if expect is None or expect.get("island"):
            self.fail(
                node,
                "PS1001",
                "an island subscript needs an annotation (int, float, bool, str, bytes or list[...]) before use",
            )
        self._island_validate_expect(node, expect)
        key = index["value"] if index["kind"] == "const" else None
        if key is None:
            self.fail(node, "PS2002", "an island subscript key is a string literal")
        return _island_call_node(
            "island",
            "getitem",
            [obj, _const_str_node(key, span)],
            expect,
            "ps_island_getitem",
            span,
            {"island": {"key": key, "want": expect}},
        )

    # boxed:end

    def _resolve_module(self, node: ast.Attribute) -> tuple[str, str, str, str] | None:
        """Dotted spelling, module, member, and status for an import chain.

        Status is `lowered`, `queued`, `missing`, or `value` when the chain
        names the module object itself.
        """

        attrs: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        binding = self.bindings.get(cur.id)
        if binding is None:
            return None
        attrs.reverse()
        dotted = ".".join([cur.id, *attrs])
        if binding.kind == "func" and alias_name(binding.module, binding.attr) is not None:
            if not attrs:
                return None
            member = ".".join([binding.attr, *attrs])
            status = "lowered" if member_callable(binding.module, member) else "missing"
            return self._finish_resolve(dotted, binding.module, member, status)
        if binding.kind == "erased":
            member = ".".join(attrs)
            return dotted, binding.module, member, "erased"
        if binding.kind == "pkg":
            module = binding.module
            for attr in attrs[:-1]:
                child = f"{module}.{attr}"
                if self._export_lookup(child, None) is None:
                    return dotted, module, ".".join(attrs), "missing"
                module = child
            if not attrs:
                return dotted, module, "", "value"
            return dotted, module, attrs[-1], "pkg"
        # boxed:begin
        if binding.kind == "island":
            if not attrs:
                return dotted, binding.module, "", "value"
            if binding.rooted:
                prefix = binding.module.split(".")[1:]
                if attrs[: len(prefix)] == prefix:
                    rest = attrs[len(prefix) :]
                    if not rest:
                        return dotted, binding.module, "", "value"
                    member = rest[-1]
                    extra = rest[:-1]
                    module = binding.module if not extra else binding.module + "." + ".".join(extra)
                    return dotted, module, member, "island"
            member = attrs[-1]
            extra = attrs[:-1]
            module = binding.module if not extra else binding.module + "." + ".".join(extra)
            return dotted, module, member, "island"
        if binding.kind == "island_member":
            if not attrs:
                return dotted, binding.module, binding.attr, "island"
            member = attrs[-1]
            extra = attrs[:-1]
            base = f"{binding.module}.{binding.attr}" if binding.attr else binding.module
            module = base if not extra else base + "." + ".".join(extra)
            return dotted, module, member, "island"
        # boxed:end
        if binding.kind != "module":
            return None
        if binding.rooted:
            prefix = binding.module.split(".")[1:]
            if attrs[: len(prefix)] != prefix:
                head = binding.module.split(".")[0]
                module = ".".join([head, *attrs[:-1]]) if len(attrs) > 1 else head
                member = attrs[-1] if attrs else ""
                if not member:
                    return dotted, module, "", "value"
                return self._finish_resolve(dotted, module, member, self._use_status(module))
            rest = attrs[len(prefix) :]
            if not rest:
                return dotted, binding.module, "", "value"
            member = rest[-1]
            extra = rest[:-1]
            module = binding.module if not extra else binding.module + "." + ".".join(extra)
            return self._finish_resolve(dotted, module, member, self._use_status(module))
        if not attrs:
            return dotted, binding.module, "", "value"
        member = attrs[-1]
        extra = attrs[:-1]
        module = binding.module if not extra else binding.module + "." + ".".join(extra)
        return self._finish_resolve(dotted, module, member, self._use_status(module))

    def _queued(self, node: ast.AST, dotted: str) -> NoReturn:
        self.fail(node, "PS2101", f"{dotted} is queued in the stdlib subset and is not lowered yet")

    def _is_environ(self, node: ast.AST) -> bool:
        """True when `node` spells `os.environ` through a module import chain.

        `os.environ` is not a materialized value: it lowers at each use site
        (subscript, `in`, `.get`) to a dedicated env call, so the receiver
        spelling is checked before the generic attribute path fences it.
        """
        if not isinstance(node, ast.Attribute):
            return False
        resolved = self._resolve_module(node)
        return resolved is not None and resolved[1] == "os" and resolved[2] == "environ" and resolved[3] == "lowered"

    def _is_mod(self, node: ast.expr, module: str, name: str) -> bool:
        if not isinstance(node, ast.Call) or node.keywords:
            return False
        func = node.func
        if isinstance(func, ast.Name):
            binding = self.bindings.get(func.id)
            return (
                binding is not None
                and binding.kind == "func"
                and binding.module == module
                and binding.attr == name
                and not binding.queued
            )
        if isinstance(func, ast.Attribute):
            resolved = self._resolve_module(func)
            return resolved is not None and resolved[1] == module and resolved[2] == name and resolved[3] == "lowered"
        return False

    def _named_temp_call(self, node: ast.expr) -> bool:
        """Same resolution as `_is_mod` for tempfile.NamedTemporaryFile, but
        keeps the `mode`/`delete` keywords (the call itself rejects any
        other keyword).
        """
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            binding = self.bindings.get(func.id)
            return (
                binding is not None
                and binding.kind == "func"
                and binding.module == "tempfile"
                and binding.attr == "NamedTemporaryFile"
                and not binding.queued
            )
        if isinstance(func, ast.Attribute):
            resolved = self._resolve_module(func)
            return (
                resolved is not None
                and resolved[1] == "tempfile"
                and resolved[2] == "NamedTemporaryFile"
                and resolved[3] == "lowered"
            )
        return False

    def _bind_module_assign(self, stmt: ast.stmt) -> None:
        try:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.value is None:
                    self._annotation(stmt.annotation)  # a bare declaration binds nothing
                    return
                typ = self._annotation(stmt.annotation)
                if typ["kind"] == "func" and not self._deferring and self._script_line is None:
                    # A Callable-annotated module name may bind a function
                    # value: signatures do not exist yet, so binding waits
                    # for the deferred pass after sigs. A scriptish module
                    # leaves it: the assignment lowers inside the entry.
                    self.deferred_module.append(stmt)
                    return
                if isinstance(stmt.value, ast.Set):
                    folded = self._module_set_literal(stmt.value)
                    if folded is not None:
                        self._expect(stmt, folded["type"], typ, stmt.target.id)
                        self._store_constant(stmt.target.id, folded, stmt)
                        return
                if isinstance(stmt.value, ast.Name) and (const := self._module_const_container(stmt.value)) is not None:
                    name, typ2 = const
                    self.fail(
                        stmt,
                        "PS2002",
                        f"{stmt.target.id} = {name} copies the module-scope constant {name}: a copy-on-alias "
                        f"is not representable — {_one_identity_note(name, typ2)}",
                    )
                value = self._module_fold(stmt.value)
                if value is None:
                    # An optional target keeps its raw constant: each read
                    # boxes it into the tagged cell.
                    value = self._expr(stmt.value, None if typ.get("optional") else typ)
                if (typ["kind"] == "class" and typ.get("name") in PTR_CLASSES) or (
                    module_needs_slot(value) and _module_closed(value)
                ):
                    # A runtime-pointer record (threading, queue) or a
                    # container (list, dict) must keep its identity: a stored
                    # constant re-emits the constructor at every read, so the
                    # value each read sees would be a fresh copy. It takes a
                    # global slot instead, one pointer for the process (#512).
                    # A value a name reaches into stays on the ordinary path:
                    # the temp it carries is not storable, so the constant
                    # check below keeps its fence.
                    self._bind_threading_global(stmt.target.id, value, typ, stmt)
                    return
                if not _module_closed(value):
                    self.fail(stmt, "PS2002", "a module assignment must be a constant")
                if typ.get("optional") and not (
                    value["type"]["kind"] == "none" or types_equal(value["type"], typ["elem"])
                ):
                    self.fail(
                        stmt, "PS1003", f"{stmt.target.id} is {_show(value['type'])}, annotation says {_show(typ)}"
                    )
                elif not typ.get("optional"):
                    self._expect(stmt, value["type"], typ, stmt.target.id)
                self._store_constant(stmt.target.id, value, stmt)
                return
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
                nt_call = self._namedtuple_call(stmt.value)
                if nt_call is not None:
                    self._namedtuple_decl(target, nt_call, stmt)
                    return
                tv_call = self._typevar_call(stmt.value)
                if tv_call is not None:
                    self._declare_typevar(target, tv_call, stmt)
                    return
                if isinstance(stmt.value, ast.Name) and (const := self._module_const_container(stmt.value)) is not None:
                    # `ys = xs` freezes a copy of the constant's literal at
                    # bind time: CPython would alias the one container, and a
                    # copy-on-alias is not representable (issue #525). In a
                    # script the fence retracts and the statement re-fences
                    # from the entry; in a definition-only module it stays.
                    name, typ = const
                    self.fail(
                        stmt,
                        "PS2002",
                        f"{target} = {name} copies the module-scope constant {name}: a copy-on-alias "
                        f"is not representable — {_one_identity_note(name, typ)}",
                    )
                if (
                    not self._deferring
                    and self._script_line is None
                    and isinstance(
                        stmt.value,
                        (ast.Name, ast.Lambda, ast.Call, ast.Attribute, ast.Subscript, ast.IfExp),
                    )
                ):
                    # `h = f` / `p = functools.partial(...)` may hold a
                    # callable: signatures do not exist yet, so binding waits
                    # for the deferred pass after sigs. A scriptish module
                    # leaves it: the assignment lowers inside the entry.
                    self.deferred_module.append(stmt)
                    return
                if target in self.global_slots and _is_none_expr(stmt.value):
                    self._activate_slot(target, stmt)
                    return
                if target in self.global_slots:
                    # `count = 0` under a `global count` declaration: the slot
                    # activates and the constant is emitted as a ps_global_set_*
                    # initializer at the top of main. In a script, only an
                    # initializer before the first entry statement may hoist;
                    # later ones keep source order inside the entry instead.
                    if self._script_line is not None and stmt.lineno > self._script_line:
                        self.fail(stmt, "PS2002", "a module global initializer must precede script statements")
                    value = self._module_fold(stmt.value)
                    if value is None:
                        value = self._expr(stmt.value)
                    if (
                        value["kind"] != "const"
                        or value["type"]["kind"] not in {"int", "str"}
                        or not _module_closed(value)
                    ):
                        self.fail(stmt, "PS2002", "a module global initializer must be a str or int constant")
                    slot = self.global_slots[target]
                    if slot.get("type") is None:
                        slot["type"] = value["type"]
                    elif not types_equal(slot["type"], value["type"]):
                        self.fail(stmt, "PS2002", f"global {target} is already bound")
                    if not slot.get("active"):
                        self._activate_slot(target, stmt)
                    init = self._global_set(target, value, span_of(stmt, self.filename))
                    self.global_inits.append(init["expr"])
                    return
                if isinstance(stmt.value, ast.Tuple):
                    self._store_tuple_constant(target, stmt.value, stmt)
                    return
                if isinstance(stmt.value, ast.Set):
                    folded = self._module_set_literal(stmt.value)
                    if folded is not None:
                        self._store_constant(target, folded, stmt)
                        return
                value = self._module_fold(stmt.value)
                if value is None:
                    value = self._expr(stmt.value)
                if module_needs_slot(value) and self._script_line is None:
                    # A definition-only module binds a container or parser the
                    # threading way: the slot keeps one pointer for the
                    # process (#512). A script leaves it: the assignment
                    # lowers inside the entry, a real store per run.
                    self._bind_threading_global(target, value, value["type"], stmt)
                    return
                # re.compile of one str constant is the Pattern call a function
                # already lowers. Store that call and re-emit it at each read.
                # It is not a const kind and not a runtime global. Flags, extra
                # arguments, and a non-constant pattern stay fenced here.
                if (
                    value["kind"] not in {"const", "list", "dict", "tuple-const"} or not _module_closed(value)
                ) and not self._one_str_pattern(stmt.value, value):
                    self.fail(stmt, "PS2002", "a module assignment must be a constant")
                self._store_constant(target, value, stmt)
                return
            if isinstance(stmt, ast.Assign) and len(stmt.targets) > 1:
                names = [target.id for target in stmt.targets if isinstance(target, ast.Name)]
                if len(names) == len(stmt.targets):
                    if (
                        isinstance(stmt.value, ast.Name)
                        and (const := self._module_const_container(stmt.value)) is not None
                    ):
                        name, typ = const
                        self.fail(
                            stmt,
                            "PS2002",
                            f"{names[0]} = {name} copies the module-scope constant {name}: a copy-on-alias "
                            f"is not representable — {_one_identity_note(name, typ)}",
                        )
                    value = self._module_fold(stmt.value)
                    if value is None:
                        value = self._expr(stmt.value)
                    if value["kind"] not in {"const", "list", "dict", "tuple-const"}:
                        self.fail(stmt, "PS2002", "a module assignment must be a constant")
                    for name in names:
                        self._store_constant(name, value, stmt)
                    return
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], (ast.Tuple, ast.List))
                and isinstance(stmt.value, ast.Tuple)
            ):
                elts = stmt.targets[0].elts
                names = [elt.id for elt in elts if isinstance(elt, ast.Name)]
                if len(stmt.value.elts) == len(elts) and len(names) == len(elts):
                    for name, part in zip(names, stmt.value.elts, strict=True):
                        if isinstance(part, ast.Name) and (const := self._module_const_container(part)) is not None:
                            cname, ctyp = const
                            self.fail(
                                stmt,
                                "PS2002",
                                f"{name} = {cname} copies the module-scope constant {cname}: a copy-on-alias "
                                f"is not representable — {_one_identity_note(cname, ctyp)}",
                            )
                        value = self._module_fold(part)
                        if value is None:
                            value = self._expr(part)
                        if value["kind"] != "const":
                            self.fail(stmt, "PS2002", "a module assignment must be a constant")
                        self._store_constant(name, value, stmt)
                    return
            self.fail(stmt, "PS2002", "module assignment is not lowered")
        except Fence as exc:
            self.caught.append(exc)

    def _module_fold(self, node: ast.expr) -> dict[str, Any] | None:
        """Fold a module-scope expression over constants into a typed constant value.

        Inside a function the operands lower through `if` prelude statements;
        module scope has no body to hold them, so the value folds here. A node
        that does not reduce to a closed constant returns None, and the
        ordinary path decides whether the assignment stores or fences.
        """
        if isinstance(node, ast.Constant):
            return _make_const(node.value, span_of(node, self.filename))
        if isinstance(node, ast.Name):
            if node.id in self.constants:
                found = self.constants[node.id]
                span = span_of(node, self.filename)
                if found.get("kind") == "const":
                    return {**found, "span": span}
                if found.get("kind") == "tuple-const":
                    return found
            if node.id == "__name__":
                return _const_str(self.module_name or "__main__", span_of(node, self.filename))
            binding = self.bindings.get(node.id)
            if binding is not None and binding.kind == "const":
                constant = module_constant(binding.module, binding.attr)
                if constant is not None:
                    val, _ = constant
                    span = span_of(node, self.filename)
                    return _make_const(val, span)
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            binding = self.bindings.get(node.value.id)
            if binding is not None and binding.kind == "module":
                constant = module_constant(binding.module, node.attr)
                if constant is not None:
                    val, _ = constant
                    span = span_of(node, self.filename)
                    return _make_const(val, span)
            class_name = self.class_aliases.get(node.value.id, node.value.id)
            const = self._class_const(class_name, node.attr)
            if const is not None:
                return {**const, "span": span_of(node, self.filename)}
            return None
        if isinstance(node, ast.IfExp):
            truthy = _const_truthy(self._module_const(node.test))
            if truthy is None:
                return None
            return self._module_const(node.body if truthy else node.orelse)
        if isinstance(node, ast.BoolOp):
            values = [self._module_const(value) for value in node.values]
            result = values[-1]
            for value in values:
                truthy = _const_truthy(value)
                if truthy is None:
                    return None
                if truthy != isinstance(node.op, ast.And):
                    result = value
                    break
            return result
        if isinstance(node, ast.UnaryOp):
            operand = self._module_fold(node.operand)
            val = _raw_const(operand)
            if val is not _MISSING:
                span = span_of(node, self.filename)
                if isinstance(node.op, ast.USub) and isinstance(val, (int, float)) and not isinstance(val, bool):
                    return _make_const(-val, span)
                if isinstance(node.op, ast.UAdd) and isinstance(val, (int, float)) and not isinstance(val, bool):
                    return _make_const(+val, span)
                if isinstance(node.op, ast.Invert) and isinstance(val, int) and not isinstance(val, bool):
                    return _make_const(~val, span)
                if isinstance(node.op, ast.Not):
                    truthy = _const_truthy(operand)
                    if truthy is not None:
                        return _const_bool(not truthy, span)
            return None
        if isinstance(node, ast.BinOp):
            left = self._module_fold(node.left)
            right = self._module_fold(node.right)
            lv = _raw_const(left)
            rv = _raw_const(right)
            if lv is not _MISSING and rv is not _MISSING:
                span = span_of(node, self.filename)
                is_lbool = isinstance(lv, bool)
                is_rbool = isinstance(rv, bool)
                if isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)) and is_lbool and is_rbool:
                    if isinstance(node.op, ast.BitAnd):
                        return _const_bool(lv & rv, span)
                    if isinstance(node.op, ast.BitOr):
                        return _const_bool(lv | rv, span)
                    if isinstance(node.op, ast.BitXor):
                        return _const_bool(lv ^ rv, span)
                if not is_lbool and not is_rbool:
                    if isinstance(node.op, ast.Add):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                            return _make_const(lv + rv, span)
                        if isinstance(lv, str) and isinstance(rv, str):
                            return _const_str(lv + rv, span)
                        if isinstance(lv, bytes) and isinstance(rv, bytes):
                            return _const_bytes(lv + rv, span)
                    elif isinstance(node.op, ast.Sub):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                            return _make_const(lv - rv, span)
                    elif isinstance(node.op, ast.Mult):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                            return _make_const(lv * rv, span)
                        if isinstance(lv, str) and isinstance(rv, int) and rv >= 0:
                            return _const_str(lv * rv, span)
                        if isinstance(lv, int) and isinstance(rv, str) and lv >= 0:
                            return _const_str(rv * lv, span)
                        if isinstance(lv, bytes) and isinstance(rv, int) and rv >= 0:
                            return _const_bytes(lv * rv, span)
                        if isinstance(lv, int) and isinstance(rv, bytes) and lv >= 0:
                            return _const_bytes(rv * lv, span)
                    elif isinstance(node.op, ast.Div):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) and rv != 0:
                            return _const_float(float(lv) / float(rv), span)
                    elif isinstance(node.op, ast.FloorDiv):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) and rv != 0:
                            return _make_const(lv // rv, span)
                    elif isinstance(node.op, ast.Mod):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) and rv != 0:
                            return _make_const(lv % rv, span)
                    elif isinstance(node.op, ast.Pow):
                        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                            try:
                                res = lv**rv
                                if isinstance(lv, int) and isinstance(rv, int) and rv >= 0:
                                    return _const_int(res, span)
                                return _const_float(float(res), span)
                            except OverflowError, ValueError:
                                pass
                    elif isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift)):
                        if isinstance(lv, int) and isinstance(rv, int):
                            if isinstance(node.op, ast.BitAnd):
                                return _const_int(lv & rv, span)
                            if isinstance(node.op, ast.BitOr):
                                return _const_int(lv | rv, span)
                            if isinstance(node.op, ast.BitXor):
                                return _const_int(lv ^ rv, span)
                            if isinstance(node.op, ast.LShift) and rv >= 0:
                                return _const_int(lv << rv, span)
                            if isinstance(node.op, ast.RShift) and rv >= 0:
                                return _const_int(lv >> rv, span)
            return None
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            left = self._module_fold(node.left)
            right = self._module_fold(node.comparators[0])
            lv = _raw_const(left)
            rv = _raw_const(right)
            if lv is not _MISSING and rv is not _MISSING:
                span = span_of(node, self.filename)
                op = node.ops[0]
                if isinstance(op, ast.Eq):
                    return _const_bool(lv == rv, span)
                if isinstance(op, ast.NotEq):
                    return _const_bool(lv != rv, span)
                if isinstance(op, ast.Lt) and type(lv) is type(rv):
                    return _const_bool(lv < rv, span)
                if isinstance(op, ast.LtE) and type(lv) is type(rv):
                    return _const_bool(lv <= rv, span)
                if isinstance(op, ast.Gt) and type(lv) is type(rv):
                    return _const_bool(lv > rv, span)
                if isinstance(op, ast.GtE) and type(lv) is type(rv):
                    return _const_bool(lv >= rv, span)
            return None
        if isinstance(node, ast.Tuple) and len(node.elts) >= 2:
            elems = [self._module_fold(elt) for elt in node.elts]
            if all(e is not None and e.get("kind") == "const" for e in elems):
                typ = self._tuple_type([elem["type"] for elem in elems], node)
                return {"kind": "tuple-const", "elems": elems, "type": typ}
            return None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in self.constants:
                found = self.constants[node.value.id]
                if found.get("kind") == "tuple-const":
                    idx_val = _raw_const(self._module_fold(node.slice))
                    if isinstance(idx_val, int):
                        elems = found["elems"]
                        if -len(elems) <= idx_val < len(elems):
                            return elems[idx_val]
            return None
        return None

    def _module_const(self, node: ast.expr) -> dict[str, Any] | None:
        """An operand of a module fold: a closed constant, else None.

        A failed attempt rolls the lowering state back: `prelude` temps never
        reach a body at module scope and a partial `locals`/`classes` update
        must not leak into the next statement.
        """
        folded = self._module_fold(node)
        if folded is not None:
            return folded
        saved_locals = dict(self.locals)
        saved_classes = dict(self.classes)
        saved_prelude = self.prelude
        self.prelude = []
        try:
            value = self._expr(node)
        except Fence:
            self.locals = saved_locals
            self.classes = saved_classes
            raise
        finally:
            self.prelude = saved_prelude
        if value["kind"] not in {"const", "list", "dict"} or not _module_closed(value) or module_needs_slot(value):
            self.locals = saved_locals
            self.classes = saved_classes
            return None
        return value

    def _module_set_literal(self, node: ast.Set) -> dict[str, Any] | None:
        """A set of const str or const int is the set list a module list constant already inlines.

        Any other element, a starred element, or a non-constant stays on the ordinary path.
        Duplicates keep the first occurrence. Each use copies the list, so it is a new object.
        """
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
        span = span_of(node, self.filename)
        return _set_list_literal(elems, kinds[0], span)
