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
import copy
import os
import sys
from typing import Any, NoReturn

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.errors import format_diagnostic, span_of
from pyscriptc.ir import SUBSET, cpython_version, type_key, types_equal
from pyscriptc.lower._calls import CallsMixin
from pyscriptc.lower._core import (
    _CMP,
    _DICT_FORMS,
    _LIST_FORMS,
    _SET_FORMS,
    _TRUTHY,
    Binding,
    Fence,
    _compare,
    _const_none,
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
from pyscriptc.restlower import alias_name, path_attr
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.stdlib_queue import module_status
from pyscriptc.stdmore import ALWAYS_CLASSES, CLASS_FIELDS, member_callable, module_constant

_UNWRAP_FORMS = frozenset({"Final", "ClassVar", "Annotated", "Required", "NotRequired"})
_DYNAMIC_ANNOTATIONS = frozenset({"object", "Any", "Protocol", "TypeVar", "Generic", "Callable", "ABC"})
_IO_HANDLES = {"BinaryIO": "BytesIO", "TextIO": "TextIO"}


class Lowerer(StdlibDispatchMixin, ExprMixin, StmtMixin, CallsMixin):
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
        self.comp_dest: str | None = None
        self.comp_init: dict[str, Any] | None = None
        self.comp_expect: dict[str, Any] | None = None
        self.comp_ready = False
        self.comp_if: ast.expr | None = None
        self.break_flag: str | None = None
        self.continue_flag: str | None = None
        self.module_name = ""
        self.is_package = False
        self.cataloguing = False
        self.package: dict[str, dict[str, dict[str, Any]]] | None = None
        self.pkg_funcs: dict[str, dict[str, Any]] = {}
        self.class_aliases: dict[str, str] = {}
        self.global_slots: dict[str, dict[str, Any]] = {}
        self.fn_globals: set[str] = set()
        # The function body being lowered, so `del name` can see a later load.
        self.fn_body: list[ast.stmt] = []
        # Names bound to frozenset(...). The value is still the set list. .add and
        # .remove on these names stay fenced. A plain set name is not in this set.
        self.frozen_names: set[str] = set()
        # User exception classes take codes after the builtin table (1–7).
        self.exc_n = 8

    def fail(self, node: ast.AST, code: str, message: str) -> NoReturn:
        raise Fence(code, message, span_of(node, self.filename), self.function)

    def module(self, tree: ast.Module) -> dict[str, Any]:
        fences: list[Fence] = []
        functions: list[ast.FunctionDef] = []
        class_nodes: list[ast.ClassDef] = []
        live = self._live_statements(list(tree.body), fences)
        self.global_slots = self._scan_global_slots(live)
        for stmt in live:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(stmt, ast.FunctionDef):
                if not self._accept_module_function(stmt, fences):
                    continue
                functions.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                class_nodes.append(stmt)
            elif isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                self.caught = []
                self._bind_module_assign(stmt)
                fences.extend(self.caught)
            elif _is_main_guard(stmt) or _is_docstring(stmt):
                continue
            else:
                fences.append(
                    Fence(
                        "PS2002",
                        f"{type(stmt).__name__} at module scope is not lowered",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
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
            self.fn_globals = _declared_globals(list(fn.body))
            params, ret = self.sigs[fn.name]
            self.locals = {param["name"]: param["type"] for param in params}
            self.frozen_names = set()
            self.fn_body = list(fn.body)
            try:
                body = self._body(list(fn.body))
                if self.caught:
                    fences.extend(self.caught)
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
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        self.function = None
        return {
            "irVersion": 1,
            "subset": SUBSET,
            "cpython": cpython_version(),
            "sourceFile": self.filename,
            "entry": lowered[0]["name"] if lowered else None,
            "functions": lowered,
            "classes": lowered_classes,
            "fences": [fence.as_dict() for fence in fences],
        }

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

    def _install_class_export(self, local: str, item: dict[str, Any], stmt: ast.AST, fences: list[Fence]) -> None:
        name = str(item["name"])
        if name in self.classes and not self.classes[name].get("imported"):
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        info = copy.deepcopy(item["info"])
        info["imported"] = True
        self.classes[name] = info
        self.class_aliases[local] = name
        self.known_classes.add(local)
        self.known_classes.add(name)
        self.declared.add(local)
        self.declared.add(name)

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

    def _install_synthetics(self, class_nodes: list[ast.ClassDef], fences: list[Fence]) -> None:
        self.synthetics.update(ALWAYS_CLASSES)
        specs: dict[str, list[dict[str, Any]]] = {}
        if "Pattern" in self.synthetics:
            specs["Pattern"] = [{"name": "pattern", "type": {"kind": "str"}}]
        if "SplitResult" in self.synthetics:
            specs["SplitResult"] = [
                {"name": name, "type": {"kind": "str"}} for name in ("scheme", "netloc", "path", "query", "fragment")
            ]
        for name in self.synthetics:
            if name in CLASS_FIELDS:
                specs[name] = CLASS_FIELDS[name]
        user_names = {node.name for node in class_nodes}
        for name, fields in specs.items():
            if name in user_names or name in self.declared:
                clash = next((node for node in class_nodes if node.name == name), None)
                fences.append(
                    Fence(
                        "PS2002",
                        f"{name} clashes with a lowered stdlib type",
                        span_of(clash if clash is not None else ast.Pass(), self.filename),
                        None,
                    )
                )
                continue
            self.declared.add(name)
            self.classes[name] = {
                "fields": fields,
                "own_fields": list(fields),
                "methods": {},
                "method_kind": {},
                "method_owner": {},
                "synthetic": True,
                "base": None,
            }

    def _collect_class(self, node: ast.ClassDef) -> None:
        if node.keywords:
            self.fail(node, "PS1203", "class keywords are not lowered")
        if len(node.bases) > 1:
            self.fail(node, "PS1203", "multiple inheritance is not lowered")
        if node.name in self.bindings or node.name in self.sigs or node.name in self.declared:
            self.fail(node, "PS2002", f"{node.name} is already bound")
        deco = self._class_decorator(node)
        # class C(object) and class C(object,) are the same class as class C.
        # object.__init__ is not emitted. Two or more bases already fenced above.
        base = None
        if node.bases and not self._is_builtin_object(node.bases[0]):
            base = self._resolve_base(node.bases[0])
        if deco == "dataclass" and base is not None:
            self.fail(node, "PS2002", "a dataclass has no base")
        if base is not None and base["kind"] == "enum":
            self._collect_enum(node, str(base["name"]))
            return
        self.declared.add(node.name)
        parent_name = base["name"] if base is not None else None
        parent = self.classes.get(parent_name) if parent_name and base and base["kind"] == "class" else None
        inherited = list(parent["fields"]) if parent is not None else []
        inherited_names = {field["name"] for field in inherited}
        fields: list[dict[str, Any]] = []
        methods: list[tuple[ast.FunctionDef, str]] = []
        seen: set[str] = set()
        seen_default = False
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None and deco != "dataclass":
                        self.fail(stmt, "PS1204", "class fields are declared with an annotation and no value")
                    if stmt.target.id in seen or stmt.target.id in inherited_names:
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                    typ = self._annotation(stmt.annotation)
                    if typ["kind"] == "bool":
                        self.fail(stmt, "PS2002", "bool fields are not lowered")
                    field: dict[str, Any] = {"name": stmt.target.id, "type": typ}
                    if stmt.value is not None:
                        saved_prelude = self.prelude
                        self.prelude = []
                        try:
                            default = self._expr(stmt.value, typ)
                        finally:
                            self.prelude = saved_prelude
                        self._expect(stmt, default["type"], typ, stmt.target.id)
                        if default["kind"] != "const":
                            self.fail(stmt, "PS2002", "a dataclass default must be a constant")
                        field["default"] = default
                        seen_default = True
                    elif seen_default:
                        self.fail(stmt, "PS2002", "a non-default field follows a default")
                    seen.add(stmt.target.id)
                    fields.append(field)
                    continue
                if isinstance(stmt, ast.FunctionDef):
                    kind = self._method_decorator(stmt)
                    if stmt.name == "__init__" and kind != "method":
                        self.fail(stmt, "PS1201", "decorators are not lowered")
                    methods.append((stmt, kind))
                    continue
                self.fail(stmt, "PS1204", f"{type(stmt).__name__} in a class body is not lowered")
            except Fence as exc:
                self.caught.append(exc)
        method_sigs: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        kinds: dict[str, str] = {}
        owners: dict[str, str] = {}
        if parent is not None:
            method_sigs.update(parent["methods"])
            kinds.update(parent["method_kind"])
            owners.update(parent["method_owner"])
        wrote_init = False
        # A field fence leaves the field set unknown; a method signature fence
        # only drops that one method. Count each kind so the catalog pass can
        # tell them apart.
        body_fences = len(self.caught)
        sig_fences = 0
        for method, kind in methods:
            try:
                method_sigs[method.name] = self._signature(method, node.name, kind)
                kinds[method.name] = kind
                owners[method.name] = node.name
                if method.name == "__init__":
                    wrote_init = True
            except Fence as exc:
                self.caught.append(exc)
                sig_fences += 1
        # The catalog pass still exports the shape when only method signatures
        # fenced: the fenced methods drop out and the rest resolves, the same
        # rule a fenced module function follows (it does not export, its
        # siblings still do). Without this, a mixin or base class with one
        # unlowerable annotation is invisible to every module that subclasses
        # it, and the import fences as "not exported by this package". The
        # lowering pass keeps the strict abort so an emitted class stays
        # all-or-nothing.
        if len(self.caught) > body_fences + sig_fences or (sig_fences and not self.cataloguing):
            return
        generated: str | None = None
        if deco == "dataclass":
            if wrote_init:
                self.fail(node, "PS2002", "a dataclass does not take a written __init__")
            params = [{"name": "self", "type": {"kind": "class", "name": node.name}}]
            for field in fields:
                param: dict[str, Any] = {"name": field["name"], "type": field["type"]}
                if "default" in field:
                    param["default"] = field["default"]
                params.append(param)
            method_sigs["__init__"] = (params, {"kind": "none"})
            kinds["__init__"] = "method"
            owners["__init__"] = node.name
            generated = "dataclass"
        elif parent is not None and not wrote_init and "__init__" in parent["methods"]:
            if fields:
                self.fail(node, "PS1001", f"{node.name} needs __init__")
            parent_params = parent["methods"]["__init__"][0]
            params = [{"name": "self", "type": {"kind": "class", "name": node.name}}, *parent_params[1:]]
            method_sigs["__init__"] = (params, {"kind": "none"})
            kinds["__init__"] = "method"
            owners["__init__"] = node.name
            generated = "forward"
        elif parent is not None and fields and not wrote_init:
            self.fail(node, "PS1001", f"{node.name} needs __init__")
        exception = bool(base and (base["kind"] == "exception" or (parent and parent.get("exception"))))
        info: dict[str, Any] = {
            "fields": [*inherited, *fields],
            "own_fields": fields,
            "methods": method_sigs,
            "method_kind": kinds,
            "method_owner": owners,
            "base": parent_name,
            "node": node,
        }
        if generated is not None:
            info["generated_init"] = generated
        if exception:
            info["exception"] = True
            info["exc_code"] = self.exc_n
            self.exc_n += 1
        self.classes[node.name] = info

    def _class_decorator(self, node: ast.ClassDef) -> str | None:
        if not node.decorator_list:
            return None
        if len(node.decorator_list) != 1 or not self._is_dataclass_deco(node.decorator_list[0]):
            self.fail(node, "PS1201", "decorators are not lowered")
        return "dataclass"

    def _is_dataclass_deco(self, dec: ast.expr) -> bool:
        target = dec
        if isinstance(dec, ast.Call):
            if dec.args or dec.keywords:
                return False
            target = dec.func
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            return binding is not None and binding.module == "dataclasses" and binding.attr == "dataclass"
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            return resolved is not None and resolved[1] == "dataclasses" and resolved[2] == "dataclass"
        return False

    def _method_decorator(self, fn: ast.FunctionDef) -> str:
        if not fn.decorator_list:
            return "method"
        if len(fn.decorator_list) != 1:
            self.fail(fn, "PS1201", "decorators are not lowered")
        dec = fn.decorator_list[0]
        target = dec
        if isinstance(dec, ast.Call):
            if dec.args or dec.keywords:
                self.fail(fn, "PS1201", "decorators are not lowered")
            target = dec.func
        if isinstance(target, ast.Name) and target.id in {"property", "staticmethod", "classmethod"}:
            return {"property": "property", "staticmethod": "static", "classmethod": "class"}[target.id]
        self.fail(fn, "PS1201", "decorators are not lowered")
        raise AssertionError("unreachable")

    def _is_builtin_object(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Name) or node.id != "object":
            return False
        if node.id in self.classes or node.id in self.bindings or node.id in self.sigs or node.id in self.constants:
            return False
        if node.id in self.declared:
            return False
        return True

    def _resolve_base(self, node: ast.expr) -> dict[str, str]:
        enum = self._enum_base_name(node)
        if enum is not None:
            return {"kind": "enum", "name": enum}
        if isinstance(node, ast.Name) and node.id in EXCEPTIONS:
            return {"kind": "exception", "name": node.id}
        if isinstance(node, ast.Name) and node.id in self.classes:
            info = self.classes[node.id]
            if info.get("synthetic"):
                self.fail(node, "PS2002", "subclassing a stdlib type is not lowered")
            if info.get("enum"):
                self.fail(node, "PS2002", "an enum is not a base")
            return {"kind": "class", "name": node.id}
        self.fail(node, "PS2002", "this class base is not lowered")
        raise AssertionError("unreachable")

    def _enum_base_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None and binding.module == "enum" and binding.attr in {"Enum", "IntEnum"}:
                return binding.attr
            return None
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[1] == "enum" and resolved[2] in {"Enum", "IntEnum"}:
                return resolved[2]
        return None

    def _collect_enum(self, node: ast.ClassDef, which: str) -> None:
        self.declared.add(node.name)
        members: dict[str, int] = {}
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                target, value = self._enum_assign(stmt)
                number = _const_int_index(value)
                if number is None:
                    self.fail(stmt, "PS2002", "an enum member is an integer constant")
                if target in members:
                    self.fail(stmt, "PS2002", f"duplicate enum member {target}")
                members[target] = number
            except Fence as exc:
                self.caught.append(exc)
        if self.caught:
            return
        if not members:
            self.fail(node, "PS2002", "an enum needs a member")
        self.classes[node.name] = {
            "fields": [],
            "own_fields": [],
            "methods": {},
            "method_kind": {},
            "method_owner": {},
            "base": which,
            "enum": True,
            "members": members,
            "node": node,
        }

    def _enum_assign(self, stmt: ast.stmt) -> tuple[str, ast.expr]:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            return stmt.targets[0].id, stmt.value
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            return stmt.target.id, stmt.value
        self.fail(stmt, "PS2002", "an enum body is integer constants")
        raise AssertionError("unreachable")

    def _lower_class(self, node: ast.ClassDef) -> dict[str, Any] | None:
        info = self.classes[node.name]
        if info.get("enum"):
            return None
        own_names = {field["name"] for field in info.get("own_fields", info["fields"])}
        methods: list[dict[str, Any]] = []
        for stmt in node.body:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            if info["method_owner"].get(stmt.name) != node.name:
                continue
            self.function = f"{node.name}.{stmt.name}"
            self.temp_n = 0
            self.prelude = []
            self.fn_globals = _declared_globals(list(stmt.body))
            params, ret = info["methods"][stmt.name]
            kind = info["method_kind"].get(stmt.name, "method")
            self.locals = {param["name"]: param["type"] for param in params}
            self.frozen_names = set()
            self.fn_body = list(stmt.body)
            if kind in {"method", "property"}:
                self.locals["self"] = {"kind": "class", "name": node.name}
            body = self._body(list(stmt.body))
            if self.caught:
                continue
            if stmt.name == "__init__":
                assigned = _assigned_fields(stmt.body)
                missing = sorted(own_names - assigned)
                if missing:
                    self.fail(stmt, "PS1001", f"__init__ does not assign {', '.join(missing)}")
            if ret["kind"] != "none" and not _always_returns(body):
                self.fail(stmt, "PS1004", f"{stmt.name} can fall off the end and return None")
            methods.append(self._method_ir(stmt.name, params, ret, body, span_of(stmt, self.filename)))
        if self.caught:
            return None
        generated = self._generated_method(node, info)
        if generated is not None:
            methods.append(generated)
        return {
            "name": node.name,
            "fields": info["fields"],
            "methods": methods,
            "span": span_of(node, self.filename),
        }

    def _method_ir(
        self,
        name: str,
        params: list[dict[str, Any]],
        ret: dict[str, Any],
        body: list[dict[str, Any]],
        span: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "name": name,
            "params": params,
            "returnType": ret,
            "locals": [{"name": local, "type": typ} for local, typ in self.locals.items()]
            if self.locals
            else [{"name": param["name"], "type": param["type"]} for param in params],
            "body": body,
            "span": span,
        }

    def _generated_method(self, node: ast.ClassDef, info: dict[str, Any]) -> dict[str, Any] | None:
        mode = info.get("generated_init")
        if mode is None:
            return None
        params, ret = info["methods"]["__init__"]
        span = span_of(node, self.filename)
        self_type = {"kind": "class", "name": node.name}
        if mode == "dataclass":
            body = [
                {
                    "kind": "setattr",
                    "object": _name("self", self_type, span),
                    "name": field["name"],
                    "value": _name(field["name"], field["type"], span),
                    "span": span,
                }
                for field in info["own_fields"]
            ]
        else:
            parent = info["base"]
            args = [_name(param["name"], param["type"], span) for param in params]
            body = [
                {
                    "kind": "expr",
                    "expr": {
                        "kind": "call",
                        "callee": {"kind": "function", "name": f"cls.{parent}.__init__"},
                        "args": args,
                        "type": {"kind": "none"},
                        "span": span,
                    },
                    "span": span,
                }
            ]
        self.locals = {param["name"]: param["type"] for param in params}
        return self._method_ir("__init__", params, ret, body, span)

    def _signature(
        self, fn: ast.FunctionDef, class_name: str | None, kind: str = "method"
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if fn.args.kwarg or fn.args.kwonlyargs or fn.args.posonlyargs:
            self.fail(fn, "PS1202", f"{fn.name} uses a parameter form that is not lowered")
        args = list(fn.args.args)
        params: list[dict[str, Any]] = []
        if kind == "property":
            if fn.args.vararg is not None or len(args) != 1 or args[0].arg != "self":
                self.fail(fn, "PS2002", "a property's only parameter is self")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "self is not annotated")
            if class_name is None:
                self.fail(fn, "PS1201", "decorators are not lowered")
            params.append({"name": "self", "type": {"kind": "class", "name": class_name}})
            args = []
        elif kind == "class":
            if not args or args[0].arg != "cls":
                self.fail(fn, "PS2002", "a classmethod's first parameter must be cls")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "cls is not annotated")
            # cls is not a value. Class.method(...) does not pass the class object.
            args = args[1:]
        elif kind == "static":
            pass
        elif class_name is not None:
            if not args or args[0].arg != "self":
                self.fail(fn, "PS2002", "a method's first parameter must be self")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "self is not annotated")
            params.append({"name": "self", "type": {"kind": "class", "name": class_name}})
            args = args[1:]
        defaults = list(fn.args.defaults)
        missing = len(args) - len(defaults)
        for index, arg in enumerate(args):
            param = _param(self, arg)
            if index >= missing:
                default_node = defaults[index - missing]
                value = self._expr(default_node, param["type"])
                self._expect(default_node, value["type"], param["type"], arg.arg)
                if value["kind"] != "const":
                    self.fail(default_node, "PS2002", "a default must be a constant")
                param["default"] = value
            params.append(param)
        if fn.args.vararg is not None:
            vararg = fn.args.vararg
            if vararg.annotation is None:
                self.fail(vararg, "PS1001", f"parameter {vararg.arg} needs an annotation")
            elem = self._annotation(vararg.annotation)
            if elem["kind"] == "bool":
                self.fail(vararg, "PS2002", "list[bool] is not lowered")
            params.append({"name": vararg.arg, "type": {"kind": "list", "elem": elem}, "vararg": True})
        if fn.returns is None:
            self.fail(fn, "PS1001", f"{fn.name} needs a return annotation")
        ret = self._annotation(fn.returns)
        if class_name is not None and fn.name == "__init__" and ret["kind"] != "none":
            self.fail(fn, "PS1003", "__init__ returns None")
        return params, ret

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
            self.fail(target, "PS2002", "slices are not lowered")
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

    def _construct(self, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
        info = self.classes[name]
        init = info["methods"].get("__init__")
        if init is None:
            if node.args or node.keywords:
                self.fail(node, "PS2003", f"{name} takes no arguments")
            return {
                "kind": "call",
                "callee": {"kind": "class", "name": name},
                "args": [],
                "type": {"kind": "class", "name": name},
                "span": span,
            }
        params = [param for param in init[0] if param["name"] != "self"]
        args = self._fill_args(node, params, name)
        return {
            "kind": "call",
            "callee": {"kind": "class", "name": name},
            "args": args,
            "type": {"kind": "class", "name": name},
            "span": span,
        }

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

    def _reject_banned_type(self, node: ast.AST, typ: dict[str, Any]) -> None:
        if typ["kind"] == "list" and typ["elem"]["kind"] == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        if typ["kind"] == "dict":
            if typ["key"]["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "dict keys are int or str")
            if typ["value"]["kind"] == "bool":
                self.fail(node, "PS2002", "dict values of bool are not lowered")

    def _annotation(self, node: ast.AST) -> dict[str, Any]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            self.fail(node, "PS1101", "union types are not in the IR")
        formed = self._typing_annotation(node)
        if formed is not None:
            return formed
        if isinstance(node, ast.Name) and node.id in {"int", "float", "bool", "str", "bytes"}:
            return {"kind": node.id}
        if isinstance(node, ast.Constant) and node.value is None:
            return {"kind": "none"}
        if isinstance(node, ast.Name) and (node.id in self.declared or node.id in self.known_classes):
            return {"kind": "class", "name": self.class_aliases.get(node.id, node.id)}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "list":
            typ = {"kind": "list", "elem": self._annotation(node.slice)}
            self._reject_banned_type(node, typ)
            return typ
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "set":
            elem = self._annotation(node.slice)
            if elem["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "set elements are int or str")
            return {"kind": "list", "elem": elem, "set": True}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "tuple":
            return self._tuple_annotation(node)
        aliased = self._aliased_annotation(node)
        if aliased is not None:
            return aliased
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "dict":
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "dict annotations are dict[key, value]")
            typ = {
                "kind": "dict",
                "key": self._annotation(node.slice.elts[0]),
                "value": self._annotation(node.slice.elts[1]),
            }
            self._reject_banned_type(node, typ)
            return typ
        named = self._annotation_class(node)
        if named is not None:
            return {"kind": "class", "name": named}
        self._fail_annotation(node)

    def _fail_annotation(self, node: ast.AST) -> NoReturn:
        """Fence an annotation the subset does not lower, with the code that names why."""
        text = ast.unparse(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.fail(node, "PS1105", f"the forward reference {text} is not lowered")
        if isinstance(node, ast.Subscript):
            self.fail(node, "PS1103", f"the generic annotation {text} is not lowered")
        tail = text.rsplit(".", 1)[-1]
        if tail in _DYNAMIC_ANNOTATIONS:
            self.fail(node, "PS1104", f"{text} is a dynamic or structural type and is not lowered")
        if isinstance(node, (ast.Name, ast.Attribute)):
            self.fail(node, "PS1106", f"the annotation {text} names a type that is not lowered")
        self.fail(node, "PS1001", "this annotation is not in the lowered subset")

    def _finish_resolve(self, dotted: str, module: str, member: str, status: str) -> tuple[str, str, str, str]:
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
        parent = ".".join(parts[:best])
        rest = parts[best:]
        if member:
            rest.append(member)
        joined = ".".join(rest)
        if not joined:
            return dotted, parent, "", "value"
        if member_callable(parent, joined) or module_constant(parent, joined) is not None:
            return dotted, parent, joined, "lowered"
        return dotted, parent, joined, "missing"

    def _class_binary(
        self, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        op = type(node.op)
        left_name = left["type"].get("name")
        right_name = right["type"].get("name")
        if op in {ast.Add, ast.Sub}:
            sign = "+" if op is ast.Add else "-"
            if left_name in {"Date", "DateTime"} and right_name == "TimeDelta":
                return {"kind": "binary", "op": sign, "left": left, "right": right, "type": left["type"], "span": span}
            if left_name == "TimeDelta" and right_name == "TimeDelta":
                return {"kind": "binary", "op": sign, "left": left, "right": right, "type": left["type"], "span": span}
            if op is ast.Sub and left_name == right_name and left_name in {"Date", "DateTime"}:
                return {
                    "kind": "binary",
                    "op": "-",
                    "left": left,
                    "right": right,
                    "type": {"kind": "class", "name": "TimeDelta"},
                    "span": span,
                }
        self.fail(node, "PS2004", f"{op.__name__} on {left_name} and {right_name} is not lowered")
        raise AssertionError("unreachable")

    def _lookup_attr(self, obj: dict[str, Any], name: str, span: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        if obj["type"]["kind"] != "class":
            self.fail(node, "PS2001", f"attribute access is not lowered ({name})")
        class_name = obj["type"]["name"]
        info = self.classes.get(class_name)
        if info is None:
            self.fail(node, "PS2001", f"{class_name} has no fields")
        for field in info["fields"]:
            if field["name"] == name:
                return {"kind": "attr", "value": obj, "name": name, "type": field["type"], "span": span}
        if info.get("method_kind", {}).get(name) == "property":
            sig = info["methods"][name]
            owner = info.get("method_owner", {}).get(name, class_name)
            return {
                "kind": "call",
                "callee": {"kind": "function", "name": f"cls.{owner}.{name}"},
                "args": [obj],
                "type": sig[1],
                "span": span,
            }
        attr = path_attr(name) if class_name == "Path" else None
        if attr is not None:
            typ, symbol = attr
            return self._abi_call("pathlib", name, [obj], typ, symbol, span)
        if synth_method(class_name, name) is not None:
            self.fail(node, "PS2002", f"{class_name}.{name} is a function and is not a value")
        self.fail(node, "PS2001", f"{class_name}.{name} is not a field")
        raise AssertionError("unreachable")

    def _has_attr(self, obj: dict[str, Any], name: str) -> bool:
        if obj["type"]["kind"] != "class":
            return False
        class_name = obj["type"]["name"]
        info = self.classes.get(class_name)
        if info is None:
            return False
        if any(field["name"] == name for field in info["fields"]):
            return True
        if info.get("method_kind", {}).get(name) == "property":
            return True
        if class_name == "Path" and path_attr(name) is not None:
            return True
        return synth_method(class_name, name) is not None

    def _isinstance_type(self, node: ast.expr) -> dict[str, Any]:
        if isinstance(node, ast.Name) and node.id in {"int", "float", "bool", "str", "bytes"}:
            return {"kind": node.id}
        if isinstance(node, ast.Name) and node.id in {"list", "dict"}:
            return {"kind": node.id, "bare": True}
        if isinstance(node, ast.Name) and (
            node.id in self.classes or node.id in self.class_aliases or node.id in EXCEPTIONS
        ):
            return {"kind": "class", "name": self.class_aliases.get(node.id, node.id)}
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None:
                alias = alias_name(binding.module, binding.attr)
                if alias is not None:
                    return {"kind": "class", "name": alias}
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None:
                alias = alias_name(resolved[1], resolved[2])
                if alias is not None:
                    return {"kind": "class", "name": alias}
        self.fail(node, "PS2001", "isinstance type is not in the lowered subset")
        raise AssertionError("unreachable")

    def _isinstance_holds(self, got: dict[str, Any], want: dict[str, Any]) -> bool:
        if want.get("bare") and got["kind"] == want["kind"]:
            return True
        if want["kind"] == "int" and got["kind"] == "bool":
            return True
        if want["kind"] == "class" and got["kind"] == "class":
            got_name = got.get("name")
            want_name = want.get("name")
            if isinstance(got_name, str) and isinstance(want_name, str) and self._is_subclass_name(got_name, want_name):
                return True
        return types_equal(got, want)

    def _is_subclass_name(self, got: str, want: str) -> bool:
        current: str | None = got
        seen: set[str] = set()
        while current and current not in seen:
            if current == want:
                return True
            seen.add(current)
            info = self.classes.get(current)
            if info is None:
                return False
            parent = info.get("base")
            current = parent if isinstance(parent, str) else None
        return False

    def _exception_codes(self, name: str) -> list[int]:
        found: list[int] = []
        if name in EXCEPTIONS:
            found.append(EXCEPTIONS[name])
        else:
            info = self.classes.get(name)
            code = info.get("exc_code") if info is not None else None
            if isinstance(code, int):
                found.append(code)
        for class_name, info in self.classes.items():
            code = info.get("exc_code")
            if class_name == name or not isinstance(code, int):
                continue
            if self._is_subclass_name(class_name, name):
                found.append(code)
        return found

    def _aliased_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        if not isinstance(node, ast.Subscript):
            return None
        alias = self._annotation_alias(node.value)
        if alias == "Deque":
            elem = self._annotation(node.slice)
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            return {"kind": "class", "name": "Deque", "elem": elem}
        if alias == "OrderedDict":
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "OrderedDict annotations are OrderedDict[key, value]")
            typ = {
                "kind": "dict",
                "key": self._annotation(node.slice.elts[0]),
                "value": self._annotation(node.slice.elts[1]),
            }
            self._reject_banned_type(node, typ)
            return typ
        return None

    def _annotation_class(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is None:
                return None
            return alias_name(binding.module, binding.attr)
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is None:
                return None
            if resolved[3] == "erased":
                return _IO_HANDLES.get(resolved[2])
            return alias_name(resolved[1], resolved[2])
        return None

    def _annotation_alias(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is None:
                return None
            if binding.module == "collections" and binding.attr == "deque":
                return "Deque"
            if binding.module == "collections" and binding.attr == "OrderedDict":
                return "OrderedDict"
            return alias_name(binding.module, binding.attr)
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is None or resolved[3] != "lowered":
                return None
            if resolved[1] == "collections" and resolved[2] == "deque":
                return "Deque"
            if resolved[1] == "collections" and resolved[2] == "OrderedDict":
                return "OrderedDict"
        return None

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

    def _listcomp(self, node: ast.ListComp, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if any(gen.is_async for gen in node.generators):
            self.fail(node, "PS2002", "async comprehensions are not lowered")
        if any(gen.ifs and index < len(node.generators) - 1 for index, gen in enumerate(node.generators)):
            self.fail(node, "PS2002", "an if before the for is not lowered")
        if len(node.generators) != 1:
            self.fail(node, "PS2002", "list comprehensions have one for")
        gen = node.generators[0]
        if len(gen.ifs) > 1:
            self.fail(node, "PS2002", "list comprehensions have one if")
        if not isinstance(gen.target, ast.Name):
            self.fail(node, "PS2002", "for targets a single name")
        dest = self._temp("comp")
        saved = (
            self.comp_elt,
            self.comp_dest,
            self.comp_init,
            self.comp_expect,
            self.comp_ready,
            self.comp_if,
        )
        self.comp_elt = node.elt
        self.comp_dest = dest
        self.comp_init = None
        self.comp_expect = expect
        self.comp_ready = False
        self.comp_if = gen.ifs[0] if gen.ifs else None
        loop = ast.For(target=gen.target, iter=gen.iter, body=[ast.Pass()], orelse=[], type_comment=None)
        ast.copy_location(loop, node)
        try:
            lowered = self._for(loop, span)
        finally:
            init = self.comp_init
            self.comp_elt, self.comp_dest, self.comp_init, self.comp_expect, self.comp_ready, self.comp_if = saved
        if init is None:
            self.fail(node, "PS2002", "list comprehension is not lowered")
        self.prelude.append(init)
        self.prelude.extend(lowered)
        return _name(dest, self.locals[dest], span)

    def _typing_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        if isinstance(node, ast.Name) and node.id in self.type_aliases:
            alias = self.type_aliases[node.id]
            handle = _IO_HANDLES.get(alias)
            if handle is not None:
                return {"kind": "class", "name": handle}
            code = "PS1104" if alias in _DYNAMIC_ANNOTATIONS else "PS1103"
            self.fail(node, code, f"{alias} is not a value type in the IR")
        if not isinstance(node, ast.Subscript):
            return None
        alias = self._typing_alias(node.value)
        if alias is None:
            return None
        if alias == "IO":
            handle = _io_parameter(node.slice)
            if handle is None:
                self.fail(node, "PS1001", "IO is not a value type in the IR")
            return {"kind": "class", "name": handle}
        if alias in _LIST_FORMS:
            typ = {"kind": "list", "elem": self._annotation(node.slice)}
            self._reject_banned_type(node, typ)
            return typ
        if alias in _SET_FORMS:
            elem = self._annotation(node.slice)
            if elem["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "set elements are int or str")
            return {"kind": "list", "elem": elem, "set": True}
        if alias in _DICT_FORMS:
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "Dict annotations are Dict[key, value]")
            typ = {
                "kind": "dict",
                "key": self._annotation(node.slice.elts[0]),
                "value": self._annotation(node.slice.elts[1]),
            }
            self._reject_banned_type(node, typ)
            return typ
        if alias == "Tuple":
            return self._tuple_annotation(node)
        if alias in _UNWRAP_FORMS:
            inner = node.slice
            if alias == "Annotated":
                if isinstance(inner, ast.Tuple) and inner.elts:
                    inner = inner.elts[0]
                else:
                    self.fail(node, "PS1001", "Annotated needs a type")
            return self._annotation(inner)
        if alias == "Optional":
            self.fail(node, "PS1102", "optional types are not in the IR")
        if alias == "Union":
            self.fail(node, "PS1101", "union types are not in the IR")
        self.fail(node, "PS1103", f"{alias} is not a value type in the IR")

    def _typing_alias(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name) and node.id in self.type_aliases:
            return self.type_aliases[node.id]
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[3] == "erased":
                return resolved[2]
        return None

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
        self.fail(node, "PS2004", "is is not lowered")
        raise AssertionError("unreachable")

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
            if _is_main_guard(stmt) or _is_docstring(stmt):
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

    def _scan_global_slots(self, statements: list[ast.stmt]) -> dict[str, dict[str, Any]]:
        slots: dict[str, dict[str, Any]] = {}
        for stmt in statements:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            declared = _declared_globals(list(stmt.body))
            for name in declared:
                slots.setdefault(name, {"type": None, "active": False})
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
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

    def _synthetic_classes(self, seen: set[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for name, info in self.classes.items():
            if name in seen or not info.get("synthetic"):
                continue
            found.append(
                {
                    "name": name,
                    "fields": info["fields"],
                    "methods": [],
                    "span": {"file": self.filename, "line": 1, "col": 1, "endLine": 1, "endCol": 1},
                }
            )
        return found

    def _tuple_annotation(self, node: ast.Subscript) -> dict[str, Any]:
        elts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if any(_ellipsis_const(elt) for elt in elts):
            # tuple[T, ...] is a homogeneous sequence of zero or more T. That is the
            # list[T] struct.unpack already returns. A synthetic class would be a new
            # shape. Mixed or nested ellipsis stays the current fence.
            if len(elts) == 2 and _ellipsis_const(elts[1]) and not _node_has_ellipsis(elts[0]):
                elem = self._annotation(elts[0])
                typ = {"kind": "list", "elem": elem}
                self._reject_banned_type(node, typ)
                return typ
            self.fail(node, "PS1001", "tuple[T, ...] is not in the IR")
        if len(elts) < 2:
            self.fail(node, "PS1001", "tuples need at least two fields")
        return self._tuple_type([self._annotation(elt) for elt in elts], node)

    def _tuple_type(self, fields: list[dict[str, Any]], node: ast.AST) -> dict[str, Any]:
        label = "tuple[" + ", ".join(_show(field) for field in fields) + "]"
        name = "tuple_" + str(len(fields)) + "_" + "_".join(_type_slug(field) for field in fields)
        existing = self.classes.get(name)
        if existing is None:
            if name in self.declared:
                self.fail(node, "PS2002", f"{name} clashes with a tuple type")
            self.classes[name] = {
                "fields": [{"name": f"_{index}", "type": field} for index, field in enumerate(fields)],
                "methods": {},
                "synthetic": True,
                "tuple": True,
            }
            self.declared.add(name)
        elif not existing.get("tuple"):
            self.fail(node, "PS2002", f"{name} clashes with a tuple type")
        return {"kind": "class", "name": name, "label": label}

    def _tuple_expr(self, node: ast.Tuple, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.elts) < 2:
            self.fail(node, "PS2002", "tuples need at least two fields")
        elems = [self._expr(elt) for elt in node.elts]
        return self._materialize_tuple(elems, span, node)

    def _tuple_subscript(self, node: ast.Subscript, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        index = _const_int_index(node.slice)
        if index is None:
            self.fail(node, "PS2002", "a tuple index must be a constant")
        fields = self.classes[value["type"]["name"]]["fields"]
        if index < 0:
            index += len(fields)
        if index < 0 or index >= len(fields):
            self.fail(node, "PS2002", "tuple index is out of range")
        field = fields[index]
        return {"kind": "attr", "value": value, "name": field["name"], "type": field["type"], "span": span}

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


def _param(lowerer: Lowerer, arg: ast.arg) -> dict[str, Any]:
    if arg.annotation is None:
        lowerer.fail(arg, "PS1001", f"parameter {arg.arg} needs an annotation")
    return {"name": arg.arg, "type": lowerer._annotation(arg.annotation)}


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _ellipsis_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _node_has_ellipsis(node: ast.AST) -> bool:
    return any(_ellipsis_const(child) for child in ast.walk(node))


def _io_parameter(node: ast.AST) -> str | None:
    """IO[bytes] and IO[str] are the handles open() already returns. Any other parameter stays fenced."""

    if isinstance(node, ast.Name) and node.id == "bytes":
        return "BytesIO"
    if isinstance(node, ast.Name) and node.id == "str":
        return "TextIO"
    return None


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


def _percent_pieces(fmt: str) -> list[tuple[str, str]] | None:
    pieces: list[tuple[str, str]] = []
    buf: list[str] = []
    index = 0
    size = len(fmt)
    while index < size:
        if fmt[index] != "%":
            buf.append(fmt[index])
            index += 1
            continue
        if index + 1 >= size:
            return None
        nxt = fmt[index + 1]
        if nxt == "%":
            buf.append("%")
            index += 2
            continue
        if nxt not in {"s", "d"}:
            return None
        pieces.append(("lit", "".join(buf)))
        buf = []
        pieces.append((nxt, ""))
        index += 2
    pieces.append(("lit", "".join(buf)))
    return pieces


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


def _declared_globals(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Global):
                names.update(node.names)
    return names


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


def _type_slug(typ: dict[str, Any]) -> str:
    raw = type_key(typ)
    return "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")


def _const_int_index(node: ast.expr) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _assigned_fields(body: list[ast.stmt]) -> set[str]:
    found: set[str] = set()
    for stmt in body:
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
        elif isinstance(stmt, ast.AugAssign):
            target = stmt.target
        elif isinstance(stmt, ast.AnnAssign):
            target = stmt.target
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            found.add(target.attr)
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.stmt):
                found |= _assigned_fields([child])
    return found


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None
