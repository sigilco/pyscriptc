"""Lower CPython's ast into the spike IR.

Three gates are lowered: a closed import table and calls, containers
(`list`, `dict`, `str`/`bytes` methods, `for`, exceptions), and annotated
classes. `for` is desugared to `while` so the iterator is consumed once and
a write to the loop target does not affect the next step. The codec wave
(`re`, `json`, `urllib.parse`, `base64`, `hashlib`, `struct`) lowers a
closed set of calls. Unknown syntax and queued stdlib modules stay fences.
"""

from __future__ import annotations

import ast
import os
import sys
from typing import Any, NoReturn

from pyscriptc.errors import format_diagnostic, span_of
from pyscriptc.ir import SUBSET, cpython_version, types_equal
from pyscriptc.lower._calls import CallsMixin
from pyscriptc.lower._classes import ClassesMixin
from pyscriptc.lower._closures import ClosuresMixin
from pyscriptc.lower._core import (
    _CMP,
    _TRUTHY,
    Binding,
    Fence,
    _compare,
    _const_none,
    _declared_globals,
    _dotted,
    _is_none_expr,
    _name,
    _show,
    _tuple_assign,
)
from pyscriptc.lower._expr import ExprMixin
from pyscriptc.lower._stdlib import StdlibDispatchMixin
from pyscriptc.lower._stdlib_shared import (
    _ERASED_MODULES,
    _module_assigned,
    _release_erased_import,
)
from pyscriptc.lower._stmt import (
    StmtMixin,
    _always_returns,
    _is_docstring,
    _is_main_guard,
    _stmt_breaks_this_loop,
    _stmt_continues_this_loop,
)
from pyscriptc.stdlib_queue import canonical_module, module_status
from pyscriptc.stdmore import member_callable, module_constant


class Lowerer(StdlibDispatchMixin, ExprMixin, StmtMixin, CallsMixin, ClassesMixin, ClosuresMixin):
    def __init__(self, filename: str, source: str) -> None:
        self.filename = filename
        self.source = source
        self.function: str | None = None
        self.sigs: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        self.locals: dict[str, dict[str, Any]] = {}
        self.bindings: dict[str, Binding] = {}
        self.classes: dict[str, dict[str, Any]] = {}
        self.declared: set[str] = set()
        self.known_classes: set[str] = set()
        self.synthetics: set[str] = set()
        self.temp_n = 0
        self.caught: list[Fence] = []
        self.prelude: list[dict[str, Any]] = []
        self.constants: dict[str, dict[str, Any]] = {}
        self.type_aliases: dict[str, str] = {}
        self.comp_elt: ast.expr | None = None
        self.comp_key: ast.expr | None = None
        self.comp_kind = "list"
        self.comp_gens: list[ast.comprehension] = []
        self.comp_ifs: list[ast.expr] = []
        self.comp_dest: str | None = None
        self.comp_init: dict[str, Any] | None = None
        self.comp_expect: dict[str, Any] | None = None
        self.comp_ready = False
        self.break_flag: str | None = None
        self.continue_flag: str | None = None
        self.broke_flag = None
        # Active except handlers: bound name, or a bare-raise temp once made.
        self.exc_stack: list[str] = []
        # `except ... as name` deletes the name at handler end (CPython del).
        self.dead_names: set[str] = set()
        self.module_name = ""
        self.is_package = False
        self.cataloguing = False
        self.package: dict[str, dict[str, dict[str, Any]]] | None = None
        self.pkg_funcs: dict[str, dict[str, Any]] = {}
        self.class_aliases: dict[str, str] = {}
        self.global_slots: dict[str, dict[str, Any]] = {}
        self.fn_globals: set[str] = set()
        # `nonlocal` declarations for the function being lowered.
        self.fn_nonlocals: set[str] = set()
        # Env-bound names for the function being lowered (its captured outer
        # slots). Top-level functions have none.
        self.captures: dict[str, dict[str, Any]] = {}
        # Env-first nested functions collected while lowering a module.
        self.nested_fns: list[dict[str, Any]] = []
        self.nested_n = 0
        # `name = const` initializers for active global slots, emitted at the
        # top of main as ps_global_set_* calls.
        self.global_inits: list[dict[str, Any]] = []
        # Line of the first true script statement; global inits before it may
        # hoist, later ones lower inside the entry to keep source order.
        self._script_line: int | None = None
        # Name of the synthesized entry function (`main`/`__entry__`), if any.
        self.entry_fn_name: str | None = None
        # `global` declarations per function body, cached by body-list identity:
        # the scan pass and the lowering pass would otherwise walk each function
        # tree twice for the same `ast.Global` nodes. Bodies are not mutated
        # after parsing, so identity is a stable key within one lower_file run.
        self._globals_cache: dict[int, frozenset[str]] = {}
        # The function body being lowered, so `del name` can see a later load.
        self.fn_body: list[ast.stmt] = []
        # Names bound to frozenset(...). The value is still the set list. .add and
        # .remove on these names stay fenced. A plain set name is not in this set.
        self.frozen_names: set[str] = set()
        # User exception classes take codes after the builtin table (1–7);
        # builtins.USER_EXC_BASE pins the same floor.
        self.exc_n = 8

    def fail(self, node: ast.AST, code: str, message: str) -> NoReturn:
        raise Fence(code, message, span_of(node, self.filename), self.function)

    def _desugar(self, tree: ast.Module) -> ast.Module:
        """Pre-lowering syntax pass. Runs once, before any statement is
        lowered (called from `module`).

        A desugar pass receives the parsed module tree and returns the tree
        to lower: a replacement `ast.Module` whose statements the lowering
        below accepts. It must not lower anything itself, touch IR state
        (`self.bindings`, `self.prelude`, `self.sigs`, ...), or collect
        fences; those belong to the lowering passes that run after it.

        Today lowering happens inline (`for` becomes `while` inside
        `_for`/`_for_range`, not before it), so this hook is the identity.
        E1 replaces the body with the first real AST-to-AST pass.
        """
        return tree

    def module(self, tree: ast.Module) -> dict[str, Any]:
        tree = self._desugar(tree)
        fences: list[Fence] = []
        functions: list[ast.FunctionDef] = []
        class_nodes: list[ast.ClassDef] = []
        live = self._live_statements(list(tree.body), fences)
        self.global_slots = self._scan_global_slots(live)
        entry_stmts = self._split_entry(live, functions, class_nodes, fences)
        entry_fn = self._entry_function(entry_stmts, functions)
        entry_name = entry_fn[0].name if entry_fn else None
        self.entry_fn_name = entry_name
        functions.extend(entry_fn)
        self._install_synthetics(class_nodes, fences)
        imported = {name for name, info in self.classes.items() if info.get("imported")}
        imported.update(self.class_aliases)
        self.known_classes = {node.name for node in class_nodes}
        self.known_classes.update(name for name, info in self.classes.items() if info.get("synthetic"))
        self.known_classes.update(imported)
        for node in class_nodes:
            self.function = node.name
            self.caught = []
            try:
                self._collect_class(node)
            except Fence as exc:
                fences.append(exc)
            fences.extend(self.caught)
        for fn in functions:
            self.function = fn.name
            if fn.name in self.declared:
                fences.append(Fence("PS2002", f"{fn.name} is already bound", span_of(fn, self.filename), fn.name))
                continue
            try:
                kind = "static" if self._is_module_staticmethod(fn) else "method"
                self.sigs[fn.name] = self._signature(fn, None, kind)
            except Fence as exc:
                fences.append(exc)
        if self.cataloguing:
            self.function = None
            return {
                "irVersion": 1,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": self.filename,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": [fence.as_dict() for fence in fences],
            }
        lowered_classes: list[dict[str, Any]] = []
        for node in class_nodes:
            if node.name not in self.classes or self.classes[node.name].get("synthetic"):
                continue
            self.function = node.name
            self.caught = []
            try:
                lowered = self._lower_class(node)
            except Fence as exc:
                fences.append(exc)
                fences.extend(self.caught)
                continue
            fences.extend(self.caught)
            if lowered is not None and not self.caught:
                lowered_classes.append(lowered)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        lowered: list[dict[str, Any]] = []
        for fn in functions:
            if fn.name not in self.sigs:
                continue
            self.function = fn.name
            self.temp_n = 0
            self.prelude = []
            self.caught = []
            self.fn_globals = self._globals_for(fn.body)
            self.fn_nonlocals = set()
            self.captures = {}
            params, ret = self.sigs[fn.name]
            self.locals = {param["name"]: param["type"] for param in params}
            self.frozen_names = set()
            self.fn_body = list(fn.body)
            # Function-local imports rebind module names: snapshot the
            # compile-time binding tables so they restore when the body ends.
            saved_bindings = dict(self.bindings)
            saved_aliases = dict(self.type_aliases)
            saved_declared = set(self.declared)
            try:
                body = self._body(list(fn.body))
                if self.caught:
                    fences.extend(self.caught)
                    # The entry is not a user function: its fences are already
                    # in the module list and block emission, so the partial
                    # body still carries the flip signal in the IR.
                    if entry_name is None or fn.name != entry_name:
                        continue
                if ret["kind"] != "none" and not _always_returns(body):
                    self.fail(fn, "PS1004", f"{fn.name} can fall off the end and return None")
                lowered.append(
                    {
                        "name": self._ir_name(fn.name),
                        "params": params,
                        "returnType": ret,
                        "locals": [{"name": name, "type": typ} for name, typ in self.locals.items()],
                        "body": body,
                        "span": span_of(fn, self.filename),
                    }
                )
            except Fence as exc:
                fences.append(exc)
            finally:
                self.bindings = saved_bindings
                self.type_aliases = saved_aliases
                self.declared = saved_declared
        lowered.extend(self.nested_fns)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        self.function = None
        names = [item["name"] for item in lowered]
        module = {
            "irVersion": 1,
            "subset": SUBSET,
            "cpython": cpython_version(),
            "sourceFile": self.filename,
            # A definition-only module keeps the old shape: entry is the first
            # function. Only a synthesized entry overrides that.
            "entry": entry_name if entry_name in names else lowered[0]["name"] if lowered else None,
            "functions": lowered,
            "classes": lowered_classes,
            "fences": [fence.as_dict() for fence in fences],
        }
        if self.global_inits:
            module["globals"] = self.global_inits
        return module

    def _split_entry(
        self,
        live: list[ast.stmt],
        functions: list[ast.FunctionDef],
        class_nodes: list[ast.ClassDef],
        fences: list[Fence],
    ) -> list[ast.stmt]:
        """Walk the live statements: definitions stay where they are, script
        statements collect into the entry body, in source order.

        A module assignment tries the constant binding first. When the module
        also carries live script statements the failed binding rolls back and
        the assignment lowers inside the entry instead; a definition-only
        module keeps the constant fence it always raised. `pass` is inert
        (C3) and never makes a script.
        """
        entry_stmts: list[ast.stmt] = []
        failed_assigns: list[tuple[ast.stmt, int]] = []
        bare_decls: list[ast.AnnAssign] = []
        definitions = (ast.Import, ast.ImportFrom, ast.Pass, ast.FunctionDef, ast.ClassDef, ast.AnnAssign, ast.Assign)
        self._script_line = min(
            (stmt.lineno for stmt in live if not isinstance(stmt, definitions) and not _is_docstring(stmt)),
            default=None,
        )
        scriptish = self._script_line is not None
        divert = self._diverted_literals(live) if scriptish else set()
        for stmt in live:
            if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
                continue
            if isinstance(stmt, ast.FunctionDef):
                if not self._accept_module_function(stmt, fences):
                    continue
                functions.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                class_nodes.append(stmt)
            elif isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id in divert for t in targets):
                    entry_stmts.append(stmt)
                    continue
                snapshot = (dict(self.constants), set(self.declared), len(fences))
                self.caught = []
                self._bind_module_assign(stmt)
                # A failed binding rolls back at once, so later assignments
                # bind against the pre-statement state. Its fences stay for
                # now; when the module turns out to be a script they are
                # retracted and the statement lowers inside the entry.
                if self.caught:
                    self.constants = snapshot[0]
                    self.declared = snapshot[1]
                    fences.extend(self.caught)
                    failed_assigns.append((stmt, snapshot[2]))
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is None:
                    # Bound nothing, but a script may still need the
                    # declaration: the name's real assignment lowers inside
                    # the entry and reads `locals` for its annotation.
                    bare_decls.append(stmt)
                self.caught = []
            elif _is_docstring(stmt):
                continue
            else:
                entry_stmts.append(stmt)
        if entry_stmts:
            # This module is a script: retract the fences the failed constant
            # bindings raised; those assignments lower inside the entry
            # instead, in source order. Bindings that did not fence stay
            # bound - a script may still carry true constants.
            retract = min((at for _, at in failed_assigns), default=len(fences))
            if retract < len(fences):
                del fences[retract:]
            entry_stmts.extend(stmt for stmt, _ in failed_assigns)
            # A bare declaration follows its name into the entry unless already bound.
            for stmt in bare_decls:
                if isinstance(stmt.target, ast.Name) and stmt.target.id not in self.declared:
                    entry_stmts.append(stmt)
            entry_stmts.sort(key=lambda stmt: stmt.lineno)
        return entry_stmts

    def _diverted_literals(self, live: list[ast.stmt]) -> set[str]:
        """Names bound to container literals whose entry mutates them by subscript.

        Module constants re-emit their literal on every read, so a script's
        `counts[k] = v` would write into a throwaway dict; the binding lowers
        inside the entry instead unless the name hides inside a def/class.
        """
        bound: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(stmt.value, (ast.Dict, ast.List, ast.Set)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                bound.update(t.id for t in targets if isinstance(t, ast.Name))
        if not bound:
            return set()
        hidden: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hidden.update(n.id for n in ast.walk(stmt) if isinstance(n, ast.Name) and n.id in bound)
        mutated: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in ast.walk(stmt):
                if (
                    isinstance(n, ast.Subscript)
                    and isinstance(n.value, ast.Name)
                    and n.value.id in bound
                    and isinstance(n.ctx, (ast.Store, ast.Del))
                ):
                    mutated.add(n.value.id)
        return mutated - hidden

    def _entry_function(self, stmts: list[ast.stmt], functions: list[ast.FunctionDef]) -> list[ast.FunctionDef]:
        """The synthesized entry for module-level statements.

        A definition-only module (imports, constants, functions, classes,
        docstrings) synthesizes nothing: the IR is exactly what it was before
        entry lowering existed. Scripts get one function named `main`, or
        `__entry__` when the module defines its own `main` - the user's name
        always wins - carrying the live statements in source order.

        Package units never synthesize one: a package is a library, its
        modules are namespaced, and `sigs` feeds the export table.
        """
        if not stmts or self.module_name or self.cataloguing:
            return []
        taken = {fn.name for fn in functions} | self.declared
        name = "main" if "main" not in taken else "__entry__"
        fn = ast.FunctionDef(
            name=name,
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=stmts,
            decorator_list=[],
            returns=ast.Constant(value=None),
            type_comment=None,
            type_params=[],
        )
        for stmt in stmts:
            ast.copy_location(fn, stmt)
        fn.lineno = stmts[0].lineno
        fn.col_offset = 0
        fn.end_lineno = stmts[-1].end_lineno
        fn.end_col_offset = 0
        return [fn]

    def _ir_name(self, name: str) -> str:
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _bind_one_export(self, module: str, name: str, local: str, stmt: ast.AST, fences: list[Fence]) -> None:
        package = self.package or {}
        item = package.get(module, {}).get(name)
        if item is None:
            fences.append(
                Fence(
                    "PS2002",
                    f"{module}.{name} is not exported by this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        if local in self.bindings or local in self.constants or local in self.declared or local in self.sigs:
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        if item["kind"] == "const":
            self.constants[local] = item["value"]
            self.declared.add(local)
            return
        if item["kind"] == "func":
            self.pkg_funcs[local] = item
            self.bindings[local] = Binding("pyfunc", module, name, False, item["return"])
            self.declared.add(local)
            return
        if item["kind"] == "class":
            self._install_class_export(local, item, stmt, fences)
            return
        fences.append(
            Fence("PS2002", f"{module}.{name} is not a function or constant", span_of(stmt, self.filename), None)
        )

    def _package_root(self) -> str | None:
        if self.package is None or not self.module_name:
            return None
        return self.module_name.split(".", 1)[0]

    def _is_own_package(self, module: str | None) -> bool:
        root = self._package_root()
        if root is None or not module:
            return False
        return module == root or module.startswith(root + ".")

    def _pkg_item(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = (self.package or {}).get(module, {}).get(member)
        if item is None:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _pkg_export(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = self._pkg_item(node, module, member)
        if item["kind"] not in {"func", "const"}:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _linked_call(self, node: ast.Call, span: dict[str, Any], item: dict[str, Any], what: str) -> dict[str, Any]:
        if item["kind"] != "func":
            self.fail(node, "PS2002", f"{what} is not a function")
        args = self._fill_args(node, item["params"], what)
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": item["ir_name"]},
            "args": args,
            "type": item["return"],
            "span": span,
        }

    def _body(self, statements: list[ast.stmt]) -> list[dict[str, Any]]:
        body: list[dict[str, Any]] = []
        for index, stmt in enumerate(statements):
            if _is_docstring(stmt):
                continue
            try:
                lowered = self._stmt(stmt)
            except Fence as exc:
                self.caught.append(exc)
                continue
            body.extend(lowered)
            skips = (self.break_flag is not None and _stmt_breaks_this_loop(stmt)) or (
                self.continue_flag is not None and _stmt_continues_this_loop(stmt)
            )
            if skips:
                rest = self._body(statements[index + 1 :])
                if rest:
                    body.append(self._skip_rest(rest, span_of(stmt, self.filename)))
                return body
        return body

    def _setitem(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Subscript)
        if isinstance(target.slice, ast.Slice):
            # `xs[a:b] = ys` splices a list, growing or shrinking it; a step
            # stays fenced with the slice-read message.
            slc = target.slice
            container = self._expr(target.value)
            kind = container["type"]["kind"]
            if kind == "list" and container["type"].get("set"):
                self.fail(target, "PS2002", "a set has no item assignment")
            if kind != "list":
                self.fail(target, "PS2002", "slice assignment targets a list")
            if slc.step is not None:
                self.fail(target, "PS2002", "slices are not lowered")
            value = self._expr(stmt.value)
            if value["type"]["kind"] != "list" or not types_equal(value["type"]["elem"], container["type"]["elem"]):
                self.fail(stmt, "PS1003", "slice assignment takes a list of the same element type")
            start, has_start = self._slice_bound(slc.lower, span)
            stop, has_stop = self._slice_bound(slc.upper, span)
            flags = [
                {"kind": "const", "value": str(has_start), "type": {"kind": "int"}, "span": span},
                {"kind": "const", "value": str(has_stop), "type": {"kind": "int"}, "span": span},
            ]
            call = self._abi_call(
                "builtins",
                "list.setslice",
                [container, start, stop, *flags, value],
                {"kind": "none"},
                "ps_seq_splice",
                span,
            )
            return {"kind": "expr", "expr": call, "span": span}
        container = self._expr(target.value)
        index = self._expr(target.slice)
        kind = container["type"]["kind"]
        if kind == "list" and container["type"].get("set"):
            self.fail(target, "PS2002", "a set has no item assignment")
        if kind == "list":
            if index["type"]["kind"] != "int":
                self.fail(target, "PS1003", "a list index is int")
            value = self._expr(stmt.value, container["type"]["elem"])
            self._expect(stmt, value["type"], container["type"]["elem"], "list item")
        elif kind == "dict":
            self._expect(target, index["type"], container["type"]["key"], "dict key")
            value = self._expr(stmt.value, container["type"]["value"])
            self._expect(stmt, value["type"], container["type"]["value"], "dict value")
        else:
            self.fail(target, "PS2002", "item assignment targets a list or dict")
        return {"kind": "setitem", "container": container, "index": index, "value": value, "span": span}

    def _setattr(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        obj = self._expr(target.value)
        if obj["type"]["kind"] != "class":
            self.fail(target, "PS2001", f"attribute assignment is not lowered ({_dotted(target)})")
        class_name = obj["type"]["name"]
        field = next((item for item in self.classes[class_name]["fields"] if item["name"] == target.attr), None)
        if field is None:
            self.fail(target, "PS2001", f"{class_name}.{target.attr} is not a field")
        value = self._expr(stmt.value, field["type"])
        self._expect(stmt, value["type"], field["type"], target.attr)
        return {"kind": "setattr", "object": obj, "name": target.attr, "value": value, "span": span}

    def _bool_cond(self, node: ast.expr) -> dict[str, Any]:
        if isinstance(node, ast.BoolOp):
            cond = self._boolop(node, span_of(node, self.filename), as_cond=True)
        else:
            cond = self._expr(node)
        kind = cond["type"]["kind"]
        if kind == "bool":
            return cond
        if kind in _TRUTHY:
            return self._truthy(cond, span_of(node, self.filename))
        self.fail(node, "PS2004", "condition must be a bool, not a truthy value")
        raise AssertionError("unreachable")

    def _expect(self, node: ast.AST, got: dict[str, Any], want: dict[str, Any], what: str) -> None:
        if not types_equal(got, want):
            self.fail(node, "PS1003", f"{what} is {_show(got)}, annotation says {_show(want)}")

    def _use_status(self, module: str) -> str:
        if module == "nativeprobe" and os.environ.get("PYSCRIPTC_NATIVEPROBE") == "1":
            return "lowered"
        status = module_status(module)
        if status == "lowered":
            return "lowered"
        if status == "queued":
            return "queued"
        parts = module.split(".")
        for index in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:index])
            parent_status = module_status(parent)
            if parent_status == "queued":
                return "queued"
            if parent_status == "lowered":
                return "missing"
        return "missing"

    def _finish_resolve(self, dotted: str, module: str, member: str, status: str) -> tuple[str, str, str, str]:
        # Aliased spellings (os.path -> posixpath) resolve against the
        # canonical module table; `dotted` keeps the user's spelling for
        # fence messages.
        module = canonical_module(module)
        if status != "missing":
            return dotted, module, member, status
        parts = module.split(".")
        best = 0
        for index in range(1, len(parts) + 1):
            parent = ".".join(parts[:index])
            if module_status(parent) == "lowered":
                best = index
        if best == 0:
            return dotted, module, member, status
        parent = canonical_module(".".join(parts[:best]))
        rest = parts[best:]
        if member:
            rest.append(member)
        joined = ".".join(rest)
        if not joined:
            return dotted, parent, "", "value"
        if member_callable(parent, joined) or module_constant(parent, joined) is not None:
            return dotted, parent, joined, "lowered"
        return dotted, parent, joined, "missing"

    def _set_display(self, node: ast.Set, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            self.fail(node, "PS1001", "an empty set needs a set annotation")
        if any(isinstance(elt, ast.Starred) for elt in node.elts):
            self.fail(node, "PS2002", "set unpacking is not lowered")
        elem_expect = expect["elem"] if expect and expect.get("set") else None
        first = self._expr(node.elts[0], elem_expect)
        elem = first["type"]
        if elem["kind"] not in {"int", "str"}:
            self.fail(node, "PS2002", "set elements are int or str")
        typ = {"kind": "list", "elem": elem, "set": True}
        if expect and expect.get("set"):
            self._expect(node, typ, expect, "set")
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, {"kind": "list", "elems": [], "type": typ, "span": span}, span))
        self.prelude.extend(self._set_insert_stmts(dest, typ, first, span))
        for elt in node.elts[1:]:
            value = self._expr(elt, elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", "set elements have one type")
            self.prelude.extend(self._set_insert_stmts(dest, typ, value, span))
        return _name(dest, typ, span)

    def _comp(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
        span: dict[str, Any],
        expect: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(node, ast.ListComp):
            kind = "list"
        elif isinstance(node, ast.SetComp):
            kind = "set"
        else:
            kind = "dict"
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
            ) = saved
        if init is None:
            self.fail(node, "PS2002", f"{kind} comprehension is not lowered")
        self.prelude.append(init)
        self.prelude.extend(lowered)
        return _name(dest, self.locals[dest], span)

    def _store_constant(self, name: str, value: dict[str, Any], node: ast.AST) -> None:
        if name in self.bindings or name in self.declared or name in self.constants or name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is already bound")
        self.constants[name] = value
        self.declared.add(name)

    def _store_tuple_constant(self, name: str, node: ast.Tuple, stmt: ast.AST) -> None:
        if len(node.elts) < 2:
            self.fail(node, "PS2002", "tuples need at least two fields")
        elems: list[dict[str, Any]] = []
        for elt in node.elts:
            value = self._expr(elt)
            if value["kind"] != "const":
                self.fail(stmt, "PS2002", "a module assignment must be a constant")
            elems.append(value)
        self._tuple_type([elem["type"] for elem in elems], node)
        self._store_constant(name, {"kind": "tuple-const", "elems": elems}, stmt)

    def _activate_slot(self, name: str, node: ast.AST) -> None:
        slot = self.global_slots[name]
        if slot.get("conflict") or slot.get("type") is None:
            self.fail(node, "PS1001", f"{name} needs a type")
        if name in self.bindings or name in self.declared or name in self.constants or name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is already bound")
        slot["active"] = True
        self.declared.add(name)

    def _assign_global(self, stmt: ast.Assign, target: str, span: dict[str, Any]) -> list[dict[str, Any]]:
        slot = self.global_slots.get(target)
        if slot is None or not slot.get("active"):
            self.fail(stmt, "PS2002", f"global {target} is not a module slot")
        value = self._expr(stmt.value, slot["type"])
        self._expect(stmt, value["type"], slot["type"], target)
        return [self._global_set(target, value, span)]

    def _slot_key(self, name: str) -> str:
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _global_get(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        slot = self.global_slots[name]
        typ = slot["type"]
        key = _text_ir(self._slot_key(name), "str", span)
        if typ["kind"] == "str":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_str", span)
        if typ["kind"] == "int":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_i64", span)
        if typ["kind"] == "class" and typ.get("name") == "Path":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_path", span)
        self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        raise AssertionError("unreachable")

    def _global_set(self, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        typ = value["type"]
        key = _text_ir(self._slot_key(name), "str", span)
        if typ["kind"] == "str":
            symbol = "ps_global_set_str"
        elif typ["kind"] == "int":
            symbol = "ps_global_set_i64"
        elif typ["kind"] == "class" and typ.get("name") == "Path":
            symbol = "ps_global_set_path"
        else:
            self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        return {
            "kind": "expr",
            "expr": self._abi_call("builtins", "global_set", [key, value], {"kind": "none"}, symbol, span),
            "span": span,
        }

    def _global_is_set(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        key = _text_ir(self._slot_key(name), "str", span)
        return self._abi_call("builtins", "global_is_set", [key], {"kind": "bool"}, "ps_global_is_set", span)

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

    def _slot_is_unset(self, name: str, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        flag = self._global_is_set(name, span)
        if negated:
            return flag
        return {"kind": "unary", "op": "not", "operand": flag, "type": {"kind": "bool"}, "span": span}

    def _compare_none(
        self,
        node: ast.AST,
        value: dict[str, Any],
        negated: bool,
        span: dict[str, Any],
        *,
        none_on_left: bool,
    ) -> dict[str, Any]:
        kind = value["type"]["kind"]
        if kind not in {"none", "int", "float", "bool", "str", "bytes"}:
            self.fail(node, "PS2004", f"{_show(value['type'])} cannot compare with None")
        none = _const_none(span)
        op = "!=" if negated else "=="
        left, right = (none, value) if none_on_left else (value, none)
        return _compare(op, left, right, span)

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

    def _live_statements(
        self,
        statements: list[ast.stmt],
        fences: list[Fence],
        rebound: set[str] | None = None,
    ) -> list[ast.stmt]:
        # Names assigned by user code. A later `if TYPE_CHECKING` is the typing
        # constant only while that name is still the erased import.
        if rebound is None:
            rebound = set()
        live: list[ast.stmt] = []
        for stmt in statements:
            if isinstance(stmt, ast.Import):
                self._bind_import(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if isinstance(stmt, ast.ImportFrom):
                self._bind_from(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if _is_main_guard(stmt):
                # C3: the canonical guard's body is live script text. It joins
                # the entry in source order; an `else` under a `__name__` test
                # never runs as `__main__`, so it stays dropped.
                live.extend(self._live_statements(list(stmt.body), fences, rebound))
                continue
            if _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.If):
                taken = self._static_branch(stmt, rebound)
                if taken is None:
                    live.append(stmt)
                    rebound.update(_module_assigned(stmt))
                    continue
                # The other branch is not lowered. Its imports and annotations stay unread.
                live.extend(self._live_statements(taken, fences, rebound))
                continue
            live.append(stmt)
            rebound.update(_module_assigned(stmt))
        return live

    def _static_branch(self, stmt: ast.If, rebound: set[str]) -> list[ast.stmt] | None:
        held = self._version_holds(stmt.test)
        if held is None:
            held = self._type_checking_value(stmt.test, rebound)
        if held is None:
            return None
        return list(stmt.body if held else stmt.orelse)

    def _type_checking_value(self, node: ast.expr, rebound: set[str]) -> bool | None:
        """typing.TYPE_CHECKING is False. `not TYPE_CHECKING` is True. Anything else is not this fold."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self._type_checking_value(node.operand, rebound)
            if inner is None:
                return None
            return not inner
        if isinstance(node, ast.Name):
            if node.id in rebound:
                return None
            if self.type_aliases.get(node.id) == "TYPE_CHECKING":
                return False
            return None
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id not in rebound
        ):
            binding = self.bindings.get(node.value.id)
            if binding is not None and binding.kind == "erased" and binding.module in _ERASED_MODULES:
                return False
        return None

    def _version_holds(self, test: ast.expr) -> bool | None:
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            return None
        op = _CMP.get(type(test.ops[0]))
        if op is None:
            return None
        left = self._version_operand(test.left)
        right = self._version_operand(test.comparators[0])
        if left == "version" and isinstance(right, tuple):
            return _cmp_version(op, right)
        if right == "version" and isinstance(left, tuple):
            flipped = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "==": "==", "!=": "!="}[op]
            return _cmp_version(flipped, left)
        return None

    def _version_operand(self, node: ast.expr) -> str | tuple[int, ...] | None:
        if self._is_version_info(node):
            return "version"
        if isinstance(node, ast.Tuple) and node.elts and all(_is_int_const(elt) for elt in node.elts):
            return tuple(int(elt.value) for elt in node.elts if isinstance(elt, ast.Constant))
        return None

    def _is_version_info(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Attribute) or node.attr != "version_info" or not isinstance(node.value, ast.Name):
            return False
        binding = self.bindings.get(node.value.id)
        return (
            binding is not None
            and binding.kind == "module"
            and binding.module == "sys"
            and not binding.rooted
            and not binding.queued
        )

    def _globals_for(self, body: list[ast.stmt]) -> frozenset[str]:
        """`global`-declared names for one function body, walked once.

        `_scan_global_slots` and the per-function lowering pass see the same
        body objects, so the result is cached by body-list identity. Bodies
        are immutable after parsing, so identity is a stable key within one
        `lower_file` run; the cache lives on the `Lowerer` and dies with it.
        """
        key = id(body)
        cached = self._globals_cache.get(key)
        if cached is None:
            cached = frozenset(_declared_globals(list(body)))
            self._globals_cache[key] = cached
        return cached

    def _scan_global_slots(self, statements: list[ast.stmt]) -> dict[str, dict[str, Any]]:
        slots: dict[str, dict[str, Any]] = {}
        for stmt in statements:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            declared: set[str] = set()
            assigns: list[ast.Assign] = []
            for node in ast.walk(stmt):
                if isinstance(node, ast.Global):
                    declared.update(node.names)
                elif isinstance(node, ast.Assign):
                    assigns.append(node)
            for name in declared:
                slots.setdefault(name, {"type": None, "active": False})
            # Prime the lowering-time cache: `declared` equals
            # `_declared_globals(list(stmt.body))` because a `global` statement
            # can only appear in a body, and this walk covers the same body.
            self._globals_cache[id(stmt.body)] = frozenset(declared)
            for node in assigns:
                if len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or target.id not in declared:
                    continue
                typ = self._infer_slot_type(node.value)
                if typ is None:
                    continue
                existing = slots[target.id]["type"]
                if existing is not None and not types_equal(existing, typ):
                    slots[target.id]["conflict"] = True
                else:
                    slots[target.id]["type"] = typ
        return slots

    def _infer_slot_type(self, node: ast.expr) -> dict[str, Any] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {"kind": "str"}
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return {"kind": "int"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
            return {"kind": "str"}
        if self._is_mod(node, "importlib.resources", "as_file"):
            return {"kind": "class", "name": "Path"}
        return None

    def _temp(self, hint: str) -> str:
        self.temp_n += 1
        return f".ps_{hint}_{self.temp_n}"


def lower_source(source: str, filename: str = "<stdin>") -> dict[str, Any]:
    tree = ast.parse(source, filename=filename)
    return Lowerer(filename, source).module(tree)


def lower_file(path: str) -> tuple[dict[str, Any], str]:
    text = open(path, encoding="utf-8").read()
    return lower_source(text, path), text


def render_fences(module: dict[str, Any], source: str) -> str:
    parts: list[str] = []
    for fence in module["fences"]:
        text = source
        path = fence.get("span", {}).get("file")
        if path and path != module.get("sourceFile"):
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                text = source
        parts.append(format_diagnostic(fence, text))
    return "\n".join(parts)


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _const_str_join(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level f-string pieces that are already str constants are one str constant."""

    if any(part.get("kind") != "const" or part.get("type", {}).get("kind") != "str" for part in parts):
        return None
    text = "".join(str(part["value"]) for part in parts)
    return {"kind": "const", "value": text, "type": {"kind": "str"}, "span": span}


def _concat_str(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    if not parts:
        return {"kind": "const", "value": "", "type": {"kind": "str"}, "span": span}
    acc = parts[0]
    for part in parts[1:]:
        acc = {"kind": "binary", "op": "+", "left": acc, "right": part, "type": {"kind": "str"}, "span": span}
    return acc


def _const_list_concat(left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """Two constant lists of one element type are one list. A runtime list stays fenced."""

    if left.get("kind") != "list" or right.get("kind") != "list":
        return None
    if left["type"]["kind"] != "list" or right["type"]["kind"] != "list":
        return None
    if left["type"].get("set") or right["type"].get("set"):
        return None
    if not types_equal(left["type"]["elem"], right["type"]["elem"]):
        return None
    return {
        "kind": "list",
        "elems": [*left["elems"], *right["elems"]],
        "type": {"kind": "list", "elem": left["type"]["elem"]},
        "span": span,
    }


def _static_int(expr: dict[str, Any]) -> int | None:
    if expr.get("kind") == "const" and expr.get("type", {}).get("kind") == "int":
        value = expr.get("value")
        if isinstance(value, str) and _int_text(value):
            return int(value)
    if expr.get("kind") == "unary" and expr.get("op") == "-" and isinstance(expr.get("operand"), dict):
        inner = _static_int(expr["operand"])
        if inner is not None:
            return -inner
    return None


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


def _text_ir(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span}


def _is_int_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _cmp_version(op: str, parts: tuple[int, ...]) -> bool:
    current = sys.version_info
    if op == ">=":
        return current >= parts
    if op == ">":
        return current > parts
    if op == "<=":
        return current <= parts
    if op == "<":
        return current < parts
    if op == "==":
        return current == parts
    if op == "!=":
        return current != parts
    return False


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None
