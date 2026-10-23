"""Stdlib import binding and module dispatch for the lowerer.

Split out of `__init__.py` as the first E0 move commit. `Lowerer` composes
this mixin in. The area owns the closed import table: the `_bind_*` family,
module call dispatch, `_resolve_module`, and the fences that keep queued
stdlib modules out of the IR.

Shared module-level helpers (`Fence`, `Binding`, `_package_base`,
`_is_none_expr`) live in `_core.py`; binding-side helpers shared with the
static-statement walk live in `_stdlib_shared.py`. The boundary calls in both
directions stay `self._method()` so the move is behavior-identical.
"""

from __future__ import annotations

import ast
from typing import Any, NoReturn

from pyscriptc.coro import enabled
from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import handle_type, is_handle_type, type_ir
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _FUTURE_OK,
    Binding,
    Fence,
    _const_bool,
    _const_truthy,
    _is_none_expr,
    _module_closed,
    _name,
    _package_base,
    _show,
)
from pyscriptc.lower._stdlib_shared import _ERASED_MODULES
from pyscriptc.restlower import alias_name, rest_call
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    PACK_MODULES,
    SPECIAL_FUNCTIONS,
    canonical_module,
    dep_lanes,
    ffi_manifests,
    module_status,
    wave_function,
)
from pyscriptc.stdmore import (
    MODULE_CLASSES,
    PTR_CLASSES,
    REST_MODULES,
    member_callable,
    module_constant,
)


def _dep_lane_hint(module: str, message: str) -> str:
    """The fence message for an import that names a locked dependency.

    A dependency on a lane the compiler cannot take (issue #398) is refused
    with the distribution and the lane hint, so the user knows which
    dependency refused and what would unblock it. Any other import keeps
    today's message.
    """

    lanes = dep_lanes()
    if not lanes:
        return message
    head = module.split(".", 1)[0]
    entry = lanes.get(head)
    if entry is None:
        return message
    return f"{message} ({entry['distribution']} is on the {entry['lane']} lane: {entry['hint']})"


class StdlibDispatchMixin:
    def _bind_import(self, stmt: ast.Import, fences: list[Fence]) -> None:
        for alias in stmt.names:
            root = alias.name.split(".", 1)[0]
            if root in _ERASED_MODULES:
                self.bindings[alias.asname or root] = Binding("erased", root, "", False)
                continue
            if self._bind_ffi(alias.name, alias.asname or root):
                continue
            if self._is_own_package(alias.name) or self._is_program_module(alias.name):
                self._bind_own_import(alias, stmt, fences)
                continue
            if alias.name.split(".", 1)[0] in PACK_MODULES:
                self.bindings[alias.asname or alias.name] = Binding("module", alias.name, "", False)
                self._note_module(alias.name)
                continue
            status = module_status(alias.name)
            if status is None:
                fences.append(
                    Fence(
                        "PS2002",
                        _dep_lane_hint(alias.name, f"{alias.name} is not in the closed import table"),
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                continue
            parts = alias.name.split(".")
            if alias.asname or len(parts) == 1:
                bound = alias.asname or parts[0]
                rooted = False
            else:
                bound = parts[0]
                rooted = True
            self.bindings[bound] = Binding("module", alias.name, "", status == "queued", rooted=rooted)
            self._note_module(alias.name)

    def _bind_from(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        if stmt.module in _ERASED_MODULES and not stmt.level:
            for alias in stmt.names:
                if alias.name == "*":
                    fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                    continue
                self.type_aliases[alias.asname or alias.name] = alias.name
            return
        if stmt.module == "__future__" and not stmt.level:
            for alias in stmt.names:
                if alias.name not in _FUTURE_OK:
                    fences.append(
                        Fence(
                            "PS2002",
                            f"__future__ import {alias.name} is not a type import",
                            span_of(stmt, self.filename),
                            None,
                        )
                    )
            return
        if stmt.level or not stmt.module:
            if self.package is None:
                fences.append(Fence("PS2002", "relative import is not lowered", span_of(stmt, self.filename), None))
                return
            self._bind_package_import(stmt, fences)
            return
        if self._is_own_package(stmt.module) or self._is_program_module(stmt.module):
            self._bind_own_from(stmt, fences)
            return
        if canonical_module(stmt.module) in PACK_MODULES:
            for alias in stmt.names:
                local = alias.asname or alias.name
                if member_callable(stmt.module, alias.name):
                    self.bindings[local] = Binding("func", stmt.module, alias.name, False)
                    self._note_module(stmt.module)
                    continue
                fences.append(
                    Fence(
                        "PS2001",
                        f"{stmt.module}.{alias.name} is not in the lowered subset of {stmt.module}",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
            return
        status = module_status(stmt.module)
        if status is None:
            fences.append(
                Fence(
                    "PS2002",
                    _dep_lane_hint(stmt.module, f"{stmt.module} is not in the closed import table"),
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        # Aliased spellings (os.path -> posixpath) bind against the canonical
        # table so later calls reach the lowered rows under the real module.
        module = canonical_module(stmt.module)
        manifest = self._ffi_module(module)
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                continue
            if manifest is not None:
                if manifest.function(alias.name) is not None:
                    self.bindings[alias.asname or alias.name] = Binding("func", module, alias.name, False)
                    continue
                if manifest.handle(alias.name) is not None:
                    # A handle binds as a type name: annotations on it lower
                    # to the opaque ptr path (`from demo import Conn` then
                    # `c: Conn`). It is not a value (issue #224).
                    self.bindings[alias.asname or alias.name] = Binding(
                        "handle", module, alias.name, False, handle_type(alias.name)
                    )
                    continue
                if manifest.const(alias.name) is not None:
                    typ = type_ir(manifest.const(alias.name).type, manifest.handle_names)
                    self.bindings[alias.asname or alias.name] = Binding("const", module, alias.name, False, typ)
                    continue
                fences.append(
                    Fence(
                        "PS2001",
                        f"{module}.{alias.name} is not in the FFI manifest of {module}",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                continue
            local = alias.asname or alias.name
            if status == "queued":
                self.bindings[local] = Binding("func", module, alias.name, True)
                continue
            if module == "math" and alias.name in MATH_FUNCTIONS:
                self.bindings[local] = Binding("func", module, alias.name, False, MATH_FUNCTIONS[alias.name]["return"])
                continue
            if module == "math" and alias.name in MATH_CONSTANTS:
                self.bindings[local] = Binding("const", module, alias.name, False, {"kind": "float"})
                continue
            if wave_function(module, alias.name) is not None or alias.name in SPECIAL_FUNCTIONS.get(
                module, frozenset()
            ):
                self.bindings[local] = Binding("func", module, alias.name, False)
                self._note_module(module)
                continue
            constant = module_constant(module, alias.name)
            if constant is not None:
                self.bindings[local] = Binding("const", module, alias.name, False, constant[1])
                continue
            if member_callable(module, alias.name):
                self.bindings[local] = Binding("func", module, alias.name, False)
                self._note_module(module)
                continue
            child = f"{module}.{alias.name}"
            child_status = module_status(child)
            if child_status is not None:
                self.bindings[local] = Binding("module", child, "", child_status == "queued")
                self._note_module(child)
                continue
            fences.append(
                Fence(
                    "PS2001",
                    f"{module}.{alias.name} is not in the lowered subset of {module}",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _bind_package_import(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        if self.program and not self.is_package and "." not in self.module_name:
            # A program's plain sibling module is not a package: CPython
            # refuses a relative import there ("attempted relative import
            # with no known parent package"). Import the sibling by name.
            fences.append(
                Fence(
                    "PS2002",
                    f"relative import in {self.module_name} has no parent package; import the module by name",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        base = _package_base(self.module_name, self.is_package, stmt.level)
        if base is None:
            fences.append(Fence("PS2002", "relative import is not lowered", span_of(stmt, self.filename), None))
            return
        if stmt.module:
            module = ".".join([*base, *stmt.module.split(".")]) if base else stmt.module
            for alias in stmt.names:
                if alias.name == "*":
                    fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                    continue
                self._bind_one_export(module, alias.name, alias.asname or alias.name, stmt, fences)
            return
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                continue
            local = alias.asname or alias.name
            child = ".".join([*base, alias.name]) if base else alias.name
            package = self.package or {}
            if child in package:
                self._bind_pkg_module(local, child, stmt, fences)
                continue
            owner = ".".join(base)
            if owner and alias.name in package.get(owner, {}):
                if owner != self.module_name and self.program and owner in package:
                    # CPython runs the owner package's body before this
                    # import completes; the init call needs the name bound.
                    self._bind_pkg_module(owner.split(".")[-1], owner, stmt, fences)
                self._bind_one_export(owner, alias.name, local, stmt, fences)
                continue
            fences.append(
                Fence(
                    "PS2002",
                    f"{child} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _bind_pkg_module(self, local: str, module: str, stmt: ast.AST, fences: list[Fence]) -> None:
        existing = self.bindings.get(local)
        if existing is not None and existing.kind == "pkg" and existing.module == module:
            # The same module imported twice under the same name: CPython
            # keeps one binding, and so does this.
            return
        if local in self.bindings or local in self.constants or local in self.declared:
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        self.bindings[local] = Binding("pkg", module, "", False)
        self.declared.add(local)

    def _bind_own_import(self, alias: ast.alias, stmt: ast.Import, fences: list[Fence]) -> None:
        package = self.package or {}
        root = alias.name.split(".", 1)[0]
        if alias.asname:
            if alias.name not in package:
                fences.append(
                    Fence(
                        "PS2002",
                        f"{alias.name} is not a module in this package",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                return
            self._bind_pkg_module(alias.asname, alias.name, stmt, fences)
            return
        if root not in package or (alias.name != root and alias.name not in package):
            missing = alias.name if alias.name != root else root
            fences.append(
                Fence(
                    "PS2002",
                    f"{missing} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        self._bind_pkg_module(root, root, stmt, fences)

    def _bind_own_from(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        module = stmt.module or ""
        package = self.package or {}
        if self.program and module and module != "__main__":
            # A program's `from pkg import x` runs pkg's body before it binds
            # x, and that body's call is spelled `pkg.__init__()`: the module
            # name is bound here (CPython keeps the module object reachable
            # the same way; the name itself is this compiler's one addition).
            head = module.split(".")[0]
            self._bind_pkg_module(head, head, stmt, fences)
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                continue
            local = alias.asname or alias.name
            child = f"{module}.{alias.name}"
            if child in package:
                self._bind_pkg_module(local, child, stmt, fences)
                continue
            if module in package:
                self._bind_one_export(module, alias.name, local, stmt, fences)
                continue
            fences.append(
                Fence(
                    "PS2002",
                    f"{module} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _note_module(self, module: str) -> None:
        if module == "re" or module.startswith("re."):
            if module_status("re") == "lowered":
                self.synthetics.add("Pattern")
                self.synthetics.add("Match")
        if module == "urllib" or module == "urllib.parse" or module.startswith("urllib."):
            if module_status("urllib.parse") == "lowered":
                self.synthetics.add("SplitResult")
        if module == "urllib.request" and module_status("urllib.request") == "lowered":
            self.synthetics.add("HttpRequest")
            self.synthetics.add("HttpResponse")
        if module == "urllib.error" and module_status("urllib.error") == "lowered":
            self.synthetics.add("HTTPError")
            self.synthetics.add("URLError")
        for name, classes in MODULE_CLASSES.items():
            if module == name or module.startswith(name + "."):
                if module_status(name) == "lowered":
                    self.synthetics.update(classes)

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

    def _ffi_module(self, name: str) -> Any:
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
        return [{"kind": "expr", "expr": self._free_call(name, old, span), "span": span}]

    def _free_call(self, name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """The IR call that releases one owned handle.

        The free rides the manifest's own consuming thunk: the `[[fn]]` that
        declares `consumes` for this handle type is the one whose abi thunk
        releases it (the design doc's `frees` symbol is what that thunk
        calls, not what the compiler calls). Without such a thunk there is
        nothing to call, and the compiler refuses the program (PS4013)
        rather than leak the handle silently.
        """
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
        frees = [{"kind": "expr", "expr": self._free_call(name, typ, span), "span": span} for name, typ in owned]
        return [{"kind": "try", "body": body, "handlers": [], "finalbody": frees, "span": span}]

    def _module_decorator_fence(self, fn: ast.FunctionDef) -> str | None:
        if not fn.decorator_list:
            return None
        if len(fn.decorator_list) != 1:
            return "stacked decorators are not lowered"
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
        if module in REST_MODULES:
            return rest_call(self, node, span, module, attr, expect)
        if module == "json" and attr == "loads":
            self._reject_keywords(node)
            return self._json_loads(node, span, expect)
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
            package = self.package or {}
            for attr in attrs[:-1]:
                child = f"{module}.{attr}"
                if child not in package:
                    return dotted, module, ".".join(attrs), "missing"
                module = child
            if not attrs:
                return dotted, module, "", "value"
            return dotted, module, attrs[-1], "pkg"
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
                value = self._module_fold(stmt.value)
                if value is None:
                    # An optional target keeps its raw constant: each read
                    # boxes it into the tagged cell.
                    value = self._expr(stmt.value, None if typ.get("optional") else typ)
                if typ["kind"] == "class" and typ.get("name") in PTR_CLASSES:
                    # A runtime-pointer record (threading, queue) must keep
                    # its identity, so it takes a global slot instead of a
                    # stored constant that re-emits per read.
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
                # re.compile of one str constant is the Pattern call a function
                # already lowers. Store that call and re-emit it at each read.
                # It is not a const kind and not a runtime global. Flags, extra
                # arguments, and a non-constant pattern stay fenced here.
                if (
                    value["kind"] not in {"const", "list", "dict"} or not _module_closed(value)
                ) and not self._one_str_pattern(stmt.value, value):
                    self.fail(stmt, "PS2002", "a module assignment must be a constant")
                self._store_constant(target, value, stmt)
                return
            if isinstance(stmt, ast.Assign) and len(stmt.targets) > 1:
                names = [target.id for target in stmt.targets if isinstance(target, ast.Name)]
                if len(names) == len(stmt.targets):
                    value = self._expr(stmt.value)
                    if value["kind"] not in {"const", "list", "dict"}:
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
                        value = self._expr(part)
                        if value["kind"] != "const":
                            self.fail(stmt, "PS2002", "a module assignment must be a constant")
                        self._store_constant(name, value, stmt)
                    return
            self.fail(stmt, "PS2002", "module assignment is not lowered")
        except Fence as exc:
            self.caught.append(exc)

    def _module_fold(self, node: ast.expr) -> dict[str, Any] | None:
        """Fold a module-scope conditional or boolean operation over constants.

        Inside a function the operands lower through `if` prelude statements;
        module scope has no body to hold them, so the value folds here. A node
        that does not reduce to a closed constant returns None, and the
        ordinary path decides whether the assignment stores or fences.
        """
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
        return None

    def _module_const(self, node: ast.expr) -> dict[str, Any] | None:
        """An operand of a module fold: a closed constant, else None.

        A failed attempt rolls the lowering state back: `prelude` temps never
        reach a body at module scope and a partial `locals`/`classes` update
        must not leak into the next statement.
        """
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            return self._module_fold(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand = self._module_const(node.operand)
            truthy = _const_truthy(operand)
            if truthy is None:
                return None
            return _const_bool(not truthy, span_of(node, self.filename))
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
        if value["kind"] not in {"const", "list", "dict"} or not _module_closed(value):
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
        typ = {"kind": "list", "elem": {"kind": kinds[0]}, "set": True}
        return {"kind": "list", "elems": elems, "type": typ, "span": span}
