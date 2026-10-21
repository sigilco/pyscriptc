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
import os
from typing import Any, NoReturn

from pyscriptc.errors import span_of
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _FUTURE_OK,
    Binding,
    Fence,
    _const_bool,
    _const_truthy,
    _is_none_expr,
    _module_closed,
    _package_base,
    _show,
)
from pyscriptc.lower._stdlib_shared import _ERASED_MODULES
from pyscriptc.restlower import alias_name, rest_call
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    canonical_module,
    module_status,
    wave_function,
)
from pyscriptc.stdmore import (
    MODULE_CLASSES,
    REST_MODULES,
    member_callable,
    module_constant,
)


class StdlibDispatchMixin:
    def _bind_import(self, stmt: ast.Import, fences: list[Fence]) -> None:
        for alias in stmt.names:
            root = alias.name.split(".", 1)[0]
            if root in _ERASED_MODULES:
                self.bindings[alias.asname or root] = Binding("erased", root, "", False)
                continue
            if alias.name == "nativeprobe" and os.environ.get("PYSCRIPTC_NATIVEPROBE") == "1":
                # Benchmark probe, not a stdlib module. Off unless the native
                # benchmark sets the env var, so a normal compile still fences.
                self.bindings[alias.asname or "nativeprobe"] = Binding("module", "nativeprobe", "", False)
                continue
            if self._is_own_package(alias.name) or self._is_program_module(alias.name):
                self._bind_own_import(alias, stmt, fences)
                continue
            status = module_status(alias.name)
            if status is None:
                fences.append(
                    Fence(
                        "PS2002",
                        f"{alias.name} is not in the closed import table",
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
        status = module_status(stmt.module)
        if status is None:
            fences.append(
                Fence(
                    "PS2002",
                    f"{stmt.module} is not in the closed import table",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        # Aliased spellings (os.path -> posixpath) bind against the canonical
        # table so later calls reach the lowered rows under the real module.
        module = canonical_module(stmt.module)
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
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
        for name, classes in MODULE_CLASSES.items():
            if module == name or module.startswith(name + "."):
                if module_status(name) == "lowered":
                    self.synthetics.update(classes)

    def _accept_module_function(self, fn: ast.FunctionDef, fences: list[Fence]) -> bool:
        """Module `@staticmethod` is the function the class decorator already emits.

        The call passes no instance. Other module decorators stay fenced.
        """
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
        if module == "nativeprobe":
            return self._nativeprobe_call(node, span, attr)
        if module == "math":
            self._reject_keywords(node)
            return self._math_call(node, span, attr)
        if module in REST_MODULES:
            return rest_call(self, node, span, module, attr, expect)
        if module == "json" and attr == "loads":
            self._reject_keywords(node)
            return self._json_loads(node, span, expect)
        if module == "json" and attr == "dumps":
            self._reject_keywords(node)
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
