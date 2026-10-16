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

from pyscriptc.builtins import EXCEPTIONS, builtin_spec, method_spec
from pyscriptc.errors import format_diagnostic, span_of
from pyscriptc.ir import SUBSET, cpython_version, type_key, types_equal
from pyscriptc.lower._core import Binding, Fence, _is_none_expr
from pyscriptc.lower._stdlib import StdlibDispatchMixin
from pyscriptc.lower._stdlib_shared import (
    _ERASED_MODULES,
    _mod_call,
    _module_assigned,
    _release_erased_import,
    _synth_module,
)
from pyscriptc.restlower import alias_name, builtin_call, path_attr
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    module_status,
    wave_function,
)
from pyscriptc.stdmore import (
    ALWAYS_CLASSES,
    CLASS_FIELDS,
    COMPARE_CLASSES,
    member_callable,
    module_constant,
)

_CMP = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}
_INT_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//"}
_FLOAT_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
_SCALAR = {"int", "float", "str", "bytes"}
_TRUTHY = frozenset({"str", "bytes", "int", "list", "dict"})
_LIST_FORMS = frozenset({"List"})
_DICT_FORMS = frozenset({"Dict"})
_SET_FORMS = frozenset({"Set"})
_UNWRAP_FORMS = frozenset({"Final", "ClassVar", "Annotated", "Required", "NotRequired"})
_DYNAMIC_ANNOTATIONS = frozenset({"object", "Any", "Protocol", "TypeVar", "Generic", "Callable", "ABC"})
_IO_HANDLES = {"BinaryIO": "BytesIO", "TextIO": "TextIO"}


class Lowerer(StdlibDispatchMixin):
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

    def _install_class_export(
        self, local: str, item: dict[str, Any], stmt: ast.AST, fences: list[Fence]
    ) -> None:
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
        for method, kind in methods:
            try:
                method_sigs[method.name] = self._signature(method, node.name, kind)
                kinds[method.name] = kind
                owners[method.name] = node.name
                if method.name == "__init__":
                    wrote_init = True
            except Fence as exc:
                self.caught.append(exc)
        if self.caught:
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

    def _loop_flag(self, body: list[ast.stmt]) -> str | None:
        if not any(_stmt_breaks_this_loop(stmt) for stmt in body):
            return None
        flag = self._temp("brk")
        self.locals[flag] = {"kind": "bool"}
        return flag

    def _continue_loop_flag(self, body: list[ast.stmt]) -> str | None:
        if not any(_stmt_continues_this_loop(stmt) for stmt in body):
            return None
        flag = self._temp("cont")
        self.locals[flag] = {"kind": "bool"}
        return flag

    def _continue_reset(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.continue_flag is None:
            return []
        return [_assign(self.continue_flag, _const_bool(False, span), span)]

    def _skip_rest(self, stmts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
        """Skip the rest of this loop body after `break` or `continue`.

        `break` and `continue` each have a flag. The for-loop step stays outside
        this guard, so `continue` still advances and `break` does not.
        """
        guarded = stmts
        if self.continue_flag is not None:
            guarded = [
                {"kind": "if", "cond": _not_bool(self.continue_flag, span), "then": guarded, "else": [], "span": span}
            ]
        if self.break_flag is not None:
            guarded = [
                {"kind": "if", "cond": _not_bool(self.break_flag, span), "then": guarded, "else": [], "span": span}
            ]
        return guarded[0]

    def _break_init(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return []
        return [_assign(self.break_flag, _const_bool(False, span), span)]

    def _skip_on_break(self, stmts: list[dict[str, Any]], span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return stmts
        return [{"kind": "if", "cond": _not_bool(self.break_flag, span), "then": stmts, "else": [], "span": span}]

    def _user_while(
        self,
        cond: dict[str, Any],
        body: list[dict[str, Any]],
        span: dict[str, Any],
        *,
        init: bool = True,
        cond_pre: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """`while cond: body`, and `break` sets a flag the existing while already tests.

        The flag starts false. The loop runs while it is false and `cond` is
        true. `break` sets the flag and the rest of that body is an `if`.
        A false `cond` sets the flag too, so the condition is not tested again.
        Statements that compute `cond` run at the start of each iteration.
        """
        flag = self.break_flag
        pre = cond_pre or []
        if flag is None and not pre:
            return [{"kind": "while", "cond": cond, "body": body, "span": span}]
        own_flag = False
        if flag is None:
            flag = self._temp("whl")
            self.locals[flag] = {"kind": "bool"}
            own_flag = True
        loop = {
            "kind": "while",
            "cond": _not_bool(flag, span),
            "body": [
                *pre,
                {
                    "kind": "if",
                    "cond": cond,
                    "then": body,
                    "else": [_assign(flag, _const_bool(True, span), span)],
                    "span": span,
                },
            ],
            "span": span,
        }
        if not init and not own_flag:
            return [loop]
        return [_assign(flag, _const_bool(False, span), span), loop]

    def _stmt(self, stmt: ast.stmt) -> list[dict[str, Any]]:
        saved = self.prelude
        self.prelude = []
        try:
            produced = self._stmt_core(stmt)
        finally:
            pre = self.prelude
            self.prelude = saved
        return [*pre, *produced]

    def _stmt_core(self, stmt: ast.stmt) -> list[dict[str, Any]]:
        span = span_of(stmt, self.filename)
        if isinstance(stmt, ast.Pass):
            return [{"kind": "pass", "span": span}]
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute):
            return [self._setattr(stmt, span)]
        if _is_setitem(stmt):
            return [self._setitem(stmt, span)]
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            typ = self._annotation(stmt.annotation)
            if stmt.value is None:
                self.fail(stmt, "PS1001", f"{stmt.target.id} needs an initial value")
            value = self._expr(stmt.value, typ)
            self._expect(stmt, value["type"], typ, stmt.target.id)
            self.locals[stmt.target.id] = typ
            self._note_frozen(stmt.target.id, stmt.value)
            return [_assign(stmt.target.id, value, span)]
        if isinstance(stmt, ast.Global):
            for name in stmt.names:
                slot = self.global_slots.get(name)
                if slot is None or not slot.get("active"):
                    self.fail(stmt, "PS2002", f"global {name} is not a module slot")
            return [{"kind": "pass", "span": span}]
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                self.fail(stmt, "PS2002", "only a single name can be assigned")
            target = stmt.targets[0].id
            if target in self.fn_globals:
                return self._assign_global(stmt, target, span)
            if target not in self.locals:
                self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
            value = self._expr(stmt.value, self.locals[target])
            self._expect(stmt, value["type"], self.locals[target], target)
            self._note_frozen(target, stmt.value)
            return [_assign(target, value, span)]
        if isinstance(stmt, ast.AugAssign):
            return self._aug_assign(stmt, span)
        if isinstance(stmt, ast.If):
            return [
                {
                    "kind": "if",
                    "cond": self._bool_cond(stmt.test),
                    "then": self._body(list(stmt.body)),
                    "else": self._body(list(stmt.orelse)),
                    "span": span,
                }
            ]
        if isinstance(stmt, ast.Assert):
            return self._assert(stmt, span)
        if isinstance(stmt, ast.While):
            if stmt.orelse:
                self.fail(stmt, "PS2002", "while-else is not lowered")
            saved_break = self.break_flag
            saved_cont = self.continue_flag
            self.break_flag = self._loop_flag(list(stmt.body))
            self.continue_flag = self._continue_loop_flag(list(stmt.body))
            try:
                saved_prelude = self.prelude
                self.prelude = []
                try:
                    cond = self._bool_cond(stmt.test)
                    cond_pre = self.prelude
                finally:
                    self.prelude = saved_prelude
                body = [*self._continue_reset(span), *self._body(list(stmt.body))]
                return self._user_while(cond, body, span, cond_pre=cond_pre)
            finally:
                self.break_flag = saved_break
                self.continue_flag = saved_cont
        if isinstance(stmt, ast.Break):
            if self.break_flag is None:
                self.fail(stmt, "PS2002", "break outside a loop is not lowered")
            return [_assign(self.break_flag, _const_bool(True, span), span)]
        if isinstance(stmt, ast.Continue):
            if self.continue_flag is None:
                self.fail(stmt, "PS2002", "continue outside a loop is not lowered")
            return [_assign(self.continue_flag, _const_bool(True, span), span)]
        if isinstance(stmt, ast.For):
            return self._for(stmt, span)
        if isinstance(stmt, ast.Return):
            _params, ret = self.sigs[self.function or ""] if self.function in self.sigs else self._current_method()
            if stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None):
                if ret["kind"] != "none":
                    self.fail(stmt, "PS1003", "bare return is None, and this function does not return None")
                return [{"kind": "return", "value": _const_none(span), "span": span}]
            value = self._expr(stmt.value, ret)
            self._expect(stmt, value["type"], ret, "return")
            return [{"kind": "return", "value": value, "span": span}]
        if isinstance(stmt, ast.Expr):
            return [{"kind": "expr", "expr": self._expr(stmt.value), "span": span}]
        if isinstance(stmt, ast.Raise):
            return [self._raise(stmt, span)]
        if isinstance(stmt, ast.Try):
            return [self._try(stmt, span)]
        if isinstance(stmt, ast.With):
            return self._with(stmt, span)
        if isinstance(stmt, ast.Match):
            return self._match(stmt, span)
        if isinstance(stmt, ast.Delete):
            return self._delete(stmt, span)
        self.fail(stmt, "PS2002", f"{type(stmt).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _current_method(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.function and "." in self.function:
            class_name, method = self.function.split(".", 1)
            return self.classes[class_name]["methods"][method]
        self.fail(ast.Pass(), "PS2002", "return is outside a function")
        raise AssertionError("unreachable")

    def _match(self, stmt: ast.Match, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`match subject: case literal:` is `if subject == literal`.

        The subject is stored once. Each later literal case sits in the else of
        the previous one, so a match does not test the rest. Any other pattern
        stays fenced.
        """
        subject_expr = self._expr(stmt.subject)
        temp = self._temp("subj")
        self.locals[temp] = subject_expr["type"]
        self.prelude.append(_assign(temp, subject_expr, span))
        chain: list[dict[str, Any]] = []
        for case in reversed(stmt.cases):
            cond = self._match_case(case, temp, span)
            chain = [
                {
                    "kind": "if",
                    "cond": cond,
                    "then": self._body(list(case.body)),
                    "else": chain,
                    "span": span,
                }
            ]
        return chain

    def _match_case(self, case: ast.match_case, subject: str, span: dict[str, Any]) -> dict[str, Any]:
        if case.guard is not None:
            self.fail(case.guard, "PS2002", "a match guard is not lowered")
        pat = case.pattern
        if isinstance(pat, ast.MatchValue) and isinstance(pat.value, ast.Constant):
            left = ast.Name(id=subject, ctx=ast.Load())
            ast.copy_location(left, pat)
            compare = ast.Compare(left=left, ops=[ast.Eq()], comparators=[pat.value])
            ast.copy_location(compare, pat)
            return self._compare(compare, span)
        self._refuse_pattern(pat)
        raise AssertionError("unreachable")

    def _refuse_pattern(self, pat: ast.pattern) -> None:
        if isinstance(pat, ast.MatchAs):
            if pat.name is None:
                self.fail(pat, "PS2002", "a wildcard pattern is not lowered")
            self.fail(pat, "PS2002", "a capture pattern is not lowered")
        if isinstance(pat, ast.MatchOr):
            self.fail(pat, "PS2002", "an or pattern is not lowered")
        if isinstance(pat, ast.MatchSequence) or isinstance(pat, ast.MatchStar):
            self.fail(pat, "PS2002", "a sequence pattern is not lowered")
        if isinstance(pat, ast.MatchClass):
            self.fail(pat, "PS2002", "a class pattern is not lowered")
        if isinstance(pat, ast.MatchMapping):
            self.fail(pat, "PS2002", "a mapping pattern is not lowered")
        if isinstance(pat, ast.MatchSingleton):
            self.fail(pat, "PS2002", "a singleton pattern is not lowered")
        if isinstance(pat, ast.MatchValue):
            self.fail(pat, "PS2002", "a match value that is not a literal is not lowered")
        self.fail(pat, "PS2002", f"{type(pat).__name__} is not lowered")

    def _delete(self, stmt: ast.Delete, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`del` of one unread local is dropped.

        The name stays in the local table so the earlier assignment still
        type-checks, and nothing reads it. A later load stays fenced: this
        compiler cannot raise UnboundLocalError, and an empty `for` target
        already reads as 0. Other targets stay fenced.
        """
        if len(stmt.targets) != 1:
            self.fail(stmt, "PS2002", "del of more than one name is not lowered")
        target = stmt.targets[0]
        if isinstance(target, ast.Attribute):
            self.fail(stmt, "PS2002", "del of an attribute is not lowered")
        if isinstance(target, ast.Subscript):
            if isinstance(target.slice, ast.Slice):
                self.fail(stmt, "PS2002", "del of a slice is not lowered")
            self.fail(stmt, "PS2002", "del of a subscript is not lowered")
        if isinstance(target, (ast.Tuple, ast.List)):
            self.fail(stmt, "PS2002", "del of a tuple of names is not lowered")
        if not isinstance(target, ast.Name):
            self.fail(stmt, "PS2002", "del of this target is not lowered")
        name = target.id
        if name in self.fn_globals:
            self.fail(stmt, "PS2002", "del of a global is not lowered")
        if name not in self.locals:
            self.fail(stmt, "PS2002", "del of an unbound name is not lowered")
        if self._name_read_after(stmt, name):
            self.fail(stmt, "PS2002", "del of a name that is read later is not lowered")
        return [{"kind": "pass", "span": span}]

    def _name_read_after(self, delete: ast.Delete, name: str) -> bool:
        state = {"seen": False, "hit": False}

        def visit(node: ast.AST) -> None:
            if state["hit"]:
                return
            if node is delete:
                state["seen"] = True
                return
            if (
                state["seen"]
                and isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == name
            ):
                state["hit"] = True
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for stmt in self.fn_body:
            visit(stmt)
        return (not state["seen"]) or state["hit"]

    def _aug_assign(self, stmt: ast.AugAssign, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "augmented assignment is only lowered for a name")
        target = stmt.target.id
        if target not in self.locals:
            self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
        left = ast.Name(id=target, ctx=ast.Load())
        ast.copy_location(left, stmt)
        node = ast.BinOp(left=left, op=stmt.op, right=stmt.value)
        ast.copy_location(node, stmt)
        value = self._expr(node)
        self._expect(stmt, value["type"], self.locals[target], target)
        return [_assign(target, value, span)]

    def _for(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        if stmt.orelse:
            self.fail(stmt, "PS2002", "for-else is not lowered")
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "for targets a single name")
        saved_break = self.break_flag
        saved_cont = self.continue_flag
        self.break_flag = self._loop_flag(list(stmt.body))
        self.continue_flag = self._continue_loop_flag(list(stmt.body))
        try:
            return self._for_dispatch(stmt, span)
        finally:
            self.break_flag = saved_break
            self.continue_flag = saved_cont

    def _for_dispatch(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self._is_mod(stmt.iter, "itertools", "repeat"):
            return self._for_repeat(stmt, span)
        if self._is_mod(stmt.iter, "itertools", "chain"):
            return self._for_chain(stmt, span)
        if _is_range_call(stmt.iter):
            return self._for_range(stmt, span)
        if _is_call_name(stmt.iter, "enumerate"):
            return self._for_enumerate(stmt, span)
        if _is_call_name(stmt.iter, "zip"):
            return self._for_zip(stmt, span)
        iterable = self._expr(stmt.iter)
        if iterable["type"]["kind"] != "list":
            self.fail(stmt.iter, "PS2002", "for iterates over a list or range")
        elem = iterable["type"]["elem"]
        if elem["kind"] not in _SCALAR:
            self.fail(stmt, "PS2002", f"for over list[{_show(elem)}] is not lowered")
        target = stmt.target.id
        fresh = self._bind_loop_target(target, elem, stmt.target)
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        self.locals[seq] = iterable["type"]
        self.locals[idx] = {"kind": "int"}
        self.locals[length] = {"kind": "int"}
        int_t = {"kind": "int"}
        body = self._loop_body(stmt)
        step = [
            _assign(
                target,
                {
                    "kind": "subscript",
                    "value": _name(seq, iterable["type"], span),
                    "index": _name(idx, int_t, span),
                    "type": elem,
                    "span": span,
                },
                span,
            ),
            *body,
            *self._skip_on_break(
                [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                span,
            ),
        ]
        prefix = []
        if fresh:
            prefix.append(_assign(target, _zero(elem, span), span))
        prefix.extend(
            [
                _assign(seq, iterable, span),
                _assign(
                    length,
                    {
                        "kind": "call",
                        "callee": {"kind": "builtin", "name": "len"},
                        "args": [_name(seq, iterable["type"], span)],
                        "type": int_t,
                        "span": span,
                    },
                    span,
                ),
                _assign(idx, _const_int(0, span), span),
                *self._user_while(
                    _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    step,
                    span,
                ),
            ]
        )
        return prefix

    def _for_range(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords:
            self.fail(call, "PS2003", "keyword arguments are not lowered")
        args = [self._expr(arg, {"kind": "int"}) for arg in call.args]
        for arg in args:
            if arg["type"]["kind"] != "int":
                self.fail(call, "PS1003", "range arguments are int")
        if len(args) == 1:
            start, stop, step = _const_int(0, span), args[0], _const_int(1, span)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], _const_int(1, span)
        elif len(args) == 3:
            start, stop, step = args
        else:
            self.fail(call, "PS2003", "range takes 1, 2, or 3 arguments")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, {"kind": "int"}, stmt.target)
        cur = self._temp("cur")
        stop_n = self._temp("stop")
        step_n = self._temp("step")
        int_t = {"kind": "int"}
        self.locals[cur] = int_t
        self.locals[stop_n] = int_t
        self.locals[step_n] = int_t
        body = self._loop_body(stmt)
        advance = self._skip_on_break(
            [_assign(cur, _binary_int("+", _name(cur, int_t, span), _name(step_n, int_t, span), span), span)],
            span,
        )
        up = [_assign(target, _name(cur, int_t, span), span), *body, *advance]
        down = [_assign(target, _name(cur, int_t, span), span), *body, *advance]
        prefix = []
        if fresh:
            prefix.append(_assign(target, _const_int(0, span), span))
        prefix.extend(
            [
                *self._break_init(span),
                _assign(cur, start, span),
                _assign(stop_n, stop, span),
                _assign(step_n, step, span),
                {
                    "kind": "if",
                    "cond": _compare("==", _name(step_n, int_t, span), _const_int(0, span), span),
                    "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                {
                    "kind": "if",
                    "cond": _compare(">", _name(step_n, int_t, span), _const_int(0, span), span),
                    "then": self._user_while(
                        _compare("<", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        up,
                        span,
                        init=False,
                    ),
                    "else": self._user_while(
                        _compare(">", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        down,
                        span,
                        init=False,
                    ),
                    "span": span,
                },
            ]
        )
        return prefix

    def _bind_loop_target(self, name: str, typ: dict[str, Any], node: ast.AST) -> bool:
        if name in self.bindings or name in self.classes:
            self.fail(node, "PS2002", f"{name} is already bound and cannot be a for target")
        if name not in self.locals:
            self.locals[name] = typ
            return True
        if not types_equal(self.locals[name], typ):
            self.fail(node, "PS1003", f"{name} is {_show(self.locals[name])}, and the for target is {_show(typ)}")
        return False

    def _assert(self, stmt: ast.Assert, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`assert cond` is `if not cond: raise Exception`, and a message sits on that raise.

        The condition is whatever `if` already accepts. AssertionError is not a
        lowered exception, so the failure is the existing `Exception` raise.
        The message is a `str`, same as `raise`, and it is lowered only in the
        failing branch.
        """
        cond = self._bool_cond(stmt.test)
        then: list[dict[str, Any]] = []
        message: dict[str, Any] | None = None
        if stmt.msg is not None:
            saved = self.prelude
            self.prelude = []
            try:
                message = self._expr(stmt.msg, {"kind": "str"})
                if message["type"]["kind"] != "str":
                    self.fail(stmt.msg, "PS1003", "an exception message is a str")
                then = self.prelude
            finally:
                self.prelude = saved
        then.append({"kind": "raise", "exc": "Exception", "message": message, "span": span})
        negated = {"kind": "unary", "op": "not", "operand": cond, "type": {"kind": "bool"}, "span": span}
        return [{"kind": "if", "cond": negated, "then": then, "else": [], "span": span}]

    def _raise(self, stmt: ast.Raise, span: dict[str, Any]) -> dict[str, Any]:
        if stmt.cause is not None:
            self.fail(stmt, "PS2002", "raise-from is not lowered")
        if stmt.exc is None:
            self.fail(stmt, "PS2002", "bare raise is not lowered")
        exc: ast.expr = stmt.exc
        message: dict[str, Any] | None = None
        if isinstance(exc, ast.Call):
            if exc.keywords:
                self.fail(exc, "PS2003", "keyword arguments are not lowered")
            if len(exc.args) > 1:
                self.fail(exc, "PS2003", "raise takes an exception name and an optional message")
            if exc.args:
                message = self._expr(exc.args[0], {"kind": "str"})
                if message["type"]["kind"] != "str":
                    self.fail(exc, "PS1003", "an exception message is a str")
            exc = exc.func
        if not isinstance(exc, ast.Name):
            self.fail(stmt, "PS2002", "raise names a lowered exception")
        if exc.id in EXCEPTIONS:
            return {"kind": "raise", "exc": exc.id, "message": message, "span": span}
        resolved = self.class_aliases.get(exc.id, exc.id)
        info = self.classes.get(resolved)
        if info is not None and info.get("exception"):
            # A user exception is a code plus an optional str. raise does not call __init__.
            return {"kind": "raise", "exc": resolved, "code": info["exc_code"], "message": message, "span": span}
        self.fail(stmt, "PS2002", "raise names a lowered exception")
        raise AssertionError("unreachable")

    def _try(self, stmt: ast.Try, span: dict[str, Any]) -> dict[str, Any]:
        if stmt.orelse:
            self.fail(stmt, "PS2002", "try-else is not lowered")
        if stmt.finalbody:
            self.fail(stmt, "PS2002", "finally is not lowered")
        handlers = []
        for handler in stmt.handlers:
            if handler.name is not None:
                self.fail(handler, "PS1205", "except-as is not lowered")
            if handler.type is None:
                self.fail(handler, "PS1205", "bare except is not lowered")
            names = _handler_names(self, handler.type)
            codes: list[int] = []
            if "Exception" not in names:
                for name in names:
                    codes.extend(self._exception_codes(name))
            handlers.append({"exc": names, "codes": codes, "body": self._body(list(handler.body))})
        return {"kind": "try", "body": self._body(list(stmt.body)), "handlers": handlers, "span": span}

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

    def _expr(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        span = span_of(node, self.filename)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return self._name(node, span)
        if isinstance(node, ast.Constant):
            return self._const(node, span)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node, span)
        if isinstance(node, ast.BinOp):
            return self._binary(node, span)
        if isinstance(node, ast.Compare):
            return self._compare(node, span)
        if isinstance(node, ast.Call):
            return self._call(node, span, expect)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, span)
        if isinstance(node, ast.List):
            return self._list(node, span, expect)
        if isinstance(node, ast.Set):
            return self._set_display(node, span, expect)
        if isinstance(node, ast.Dict):
            return self._dict(node, span, expect)
        if isinstance(node, ast.Tuple):
            return self._tuple_expr(node, span)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, span)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, span)
        if isinstance(node, ast.JoinedStr):
            return self._joined(node, span)
        if isinstance(node, ast.ListComp):
            return self._listcomp(node, span, expect)
        if isinstance(node, ast.IfExp):
            return self._ifexp(node, span)
        self.fail(node, "PS2002", f"{type(node).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _name(self, node: ast.Name, span: dict[str, Any]) -> dict[str, Any]:
        if node.id in self.locals:
            return _name(node.id, self.locals[node.id], span)
        slot = self.global_slots.get(node.id)
        if slot is not None and slot.get("active"):
            return self._global_get(node.id, span)
        if node.id in self.constants:
            found = self.constants[node.id]
            if found.get("kind") == "tuple-const":
                return self._materialize_tuple(found["elems"], span)
            return {**found, "span": span}
        if node.id in self.type_aliases:
            self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
        binding = self.bindings.get(node.id)
        if binding is not None:
            if binding.queued:
                self._queued(node, f"{binding.module}.{binding.attr}" if binding.attr else binding.module)
            if binding.kind == "erased":
                self.fail(node, "PS2002", f"{node.id} is a typing name and is not a value")
            if binding.kind == "const":
                if binding.module == "math":
                    value: Any = MATH_CONSTANTS[binding.attr]
                    typ: dict[str, Any] = {"kind": "float"}
                else:
                    found = module_constant(binding.module, binding.attr)
                    if found is None:
                        self.fail(node, "PS2001", f"{binding.module}.{binding.attr} is not a constant")
                    value, typ = found
                return {"kind": "const", "value": value, "type": typ, "span": span}
            if binding.kind in {"module", "pkg"}:
                self.fail(node, "PS2002", f"module {node.id} is not a value in the static subset")
            self.fail(node, "PS2002", f"{node.id} is a function and is not a value")
        if node.id in self.classes:
            self.fail(node, "PS2002", f"class {node.id} is not a value")
        if node.id in EXCEPTIONS:
            self.fail(node, "PS2002", f"{node.id} is only lowered in raise and except")
        self.fail(node, "PS2002", f"{node.id} is not a local or parameter")
        raise AssertionError("unreachable")

    def _const(self, node: ast.Constant, span: dict[str, Any]) -> dict[str, Any]:
        value = node.value
        if isinstance(value, bool):
            return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}
        if isinstance(value, int):
            return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}
        if isinstance(value, float):
            return {"kind": "const", "value": repr(value), "type": {"kind": "float"}, "span": span}
        if isinstance(value, str):
            if any(ord(ch) > 255 for ch in value):
                self.fail(node, "PS2005", "string literals outside Latin-1 are not lowered")
            return {"kind": "const", "value": value, "type": {"kind": "str"}, "span": span}
        if isinstance(value, bytes):
            return {"kind": "const", "value": value.decode("latin1"), "type": {"kind": "bytes"}, "span": span}
        if value is None:
            return _const_none(span)
        self.fail(node, "PS2002", f"literal {type(value).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _unary(self, node: ast.UnaryOp, span: dict[str, Any]) -> dict[str, Any]:
        operand = self._expr(node.operand)
        kind = operand["type"]["kind"]
        if isinstance(node.op, ast.USub) and kind == "int":
            return {"kind": "unary", "op": "-", "operand": operand, "type": {"kind": "int"}, "span": span}
        if isinstance(node.op, ast.USub) and kind == "float":
            return {"kind": "unary", "op": "-", "operand": operand, "type": {"kind": "float"}, "span": span}
        if isinstance(node.op, ast.Not) and kind == "bool":
            return {"kind": "unary", "op": "not", "operand": operand, "type": {"kind": "bool"}, "span": span}
        if isinstance(node.op, ast.Not) and kind in _TRUTHY:
            inner = self._truthy(operand, span)
            return {"kind": "unary", "op": "not", "operand": inner, "type": {"kind": "bool"}, "span": span}
        if isinstance(node.op, ast.USub) and kind == "class" and operand["type"].get("name") == "TimeDelta":
            return {"kind": "unary", "op": "-", "operand": operand, "type": operand["type"], "span": span}
        self.fail(node, "PS2004", f"unary {type(node.op).__name__} on {kind} is not lowered")
        raise AssertionError("unreachable")

    def _binary(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        if isinstance(node.op, ast.Mod):
            return self._percent(node, span)
        left = self._expr(node.left)
        right = self._expr(node.right)
        left_kind = left["type"]["kind"]
        right_kind = right["type"]["kind"]
        if left_kind == "bool" or right_kind == "bool":
            self.fail(node, "PS2004", "bool arithmetic is not lowered (bool is a subclass of int in Python)")
        if isinstance(node.op, ast.Pow):
            return self._pow(node, left, right, span)
        op_type = type(node.op)
        if left_kind == "int" and right_kind == "int" and op_type in _INT_BIN:
            return _binary_int(_INT_BIN[op_type], left, right, span)
        if left_kind == "float" and right_kind == "float" and op_type in _FLOAT_BIN:
            return {
                "kind": "binary",
                "op": _FLOAT_BIN[op_type],
                "left": left,
                "right": right,
                "type": {"kind": "float"},
                "span": span,
            }
        if left_kind == right_kind and left_kind in {"str", "bytes"} and isinstance(node.op, ast.Add):
            return {
                "kind": "binary",
                "op": "+",
                "left": left,
                "right": right,
                "type": {"kind": left_kind},
                "span": span,
            }
        if left_kind == "class" or right_kind == "class":
            return self._class_binary(node, left, right, span)
        joined = _const_list_concat(left, right, span)
        if joined is not None and isinstance(node.op, ast.Add):
            return joined
        self.fail(node, "PS2004", f"{op_type.__name__} on {left_kind} and {right_kind} is not lowered")
        raise AssertionError("unreachable")

    def _pow(
        self, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        """`base ** exp` is a loop of the existing int `*`.

        Both operands are ints. The base and the exponent are stored once, then
        the accumulator starts at 1 and is multiplied `exp` times. `0 ** 0` is
        that 1. A negative exponent stays fenced: a constant is refused here,
        and a computed negative raises ValueError instead of becoming a float.
        """
        if left["type"]["kind"] != "int" or right["type"]["kind"] != "int":
            self.fail(node, "PS2004", "** is only lowered for int")
        static = _static_int(right)
        if static is not None and static < 0:
            self.fail(node, "PS2004", "a negative exponent is not lowered")
        int_t = {"kind": "int"}
        base = self._temp("powb")
        exp = self._temp("powe")
        acc = self._temp("powa")
        idx = self._temp("powi")
        for name in (base, exp, acc, idx):
            self.locals[name] = int_t
        base_n = _name(base, int_t, span)
        exp_n = _name(exp, int_t, span)
        acc_n = _name(acc, int_t, span)
        idx_n = _name(idx, int_t, span)
        self.prelude.extend(
            [
                _assign(base, left, span),
                _assign(exp, right, span),
                {
                    "kind": "if",
                    "cond": _compare("<", exp_n, _const_int(0, span), span),
                    "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                _assign(acc, _const_int(1, span), span),
                _assign(idx, _const_int(0, span), span),
                {
                    "kind": "while",
                    "cond": _compare("<", idx_n, exp_n, span),
                    "body": [
                        _assign(acc, _binary_int("*", acc_n, base_n, span), span),
                        _assign(idx, _binary_int("+", idx_n, _const_int(1, span), span), span),
                    ],
                    "span": span,
                },
            ]
        )
        return _name(acc, int_t, span)

    def _compare(self, node: ast.Compare, span: dict[str, Any]) -> dict[str, Any]:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return self._compare_chain(node, span)
        if isinstance(node.ops[0], (ast.In, ast.NotIn)):
            return self._contains(node, span, isinstance(node.ops[0], ast.NotIn))
        if isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            return self._is_none(node, span, isinstance(node.ops[0], ast.IsNot))
        op = _CMP.get(type(node.ops[0]))
        if op is None:
            self.fail(node, "PS2004", f"{type(node.ops[0]).__name__} is not lowered")
        left = self._expr(node.left)
        right = self._expr(node.comparators[0])
        if left["type"].get("ordered") or right["type"].get("ordered"):
            self.fail(node, "PS2004", "OrderedDict equality is not lowered")
        if (
            left["type"]["kind"] == "class"
            and right["type"]["kind"] == "class"
            and left["type"].get("name") == right["type"].get("name")
            and left["type"].get("name") in COMPARE_CLASSES
        ):
            return _compare(op, left, right, span)
        if left["type"]["kind"] != right["type"]["kind"] or left["type"]["kind"] not in {
            "int",
            "float",
            "bool",
            "str",
            "bytes",
        }:
            self.fail(node, "PS2004", "comparisons need two values of the same scalar type")
        if left["type"]["kind"] == "bool" and op not in {"==", "!="}:
            self.fail(node, "PS2004", "bool ordering is not lowered")
        return _compare(op, left, right, span)

    def _compare_chain(self, node: ast.Compare, span: dict[str, Any]) -> dict[str, Any]:
        """`a < b < c` is the existing compares joined by the existing `and`.

        Evaluate the left, then the first middle, and compare. Each later
        middle is stored once, inside the `and` branch, so a false link does
        not evaluate the rest. `is` stays fenced. A link the one-op compare
        rejects still fences.
        """
        if len(node.ops) != len(node.comparators) or len(node.ops) < 2:
            self.fail(node, "PS2004", "chained comparisons are not lowered")
        if any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            self.fail(node, "PS2004", "is is not lowered")
        bool_t = {"kind": "bool"}
        result = self._temp("chain")
        self.locals[result] = bool_t
        left_ref = self._chain_pin(node.left)
        prev_ref: ast.expr = self._chain_pin(node.comparators[0])
        first = self._one_link(node, node.ops[0], left_ref, prev_ref)
        self.prelude.append(_assign(result, first, span))
        for index in range(1, len(node.ops)):
            saved = self.prelude
            self.prelude = []
            try:
                operand = node.comparators[index]
                if index + 1 < len(node.ops):
                    right_ref: ast.expr = self._chain_pin(operand)
                else:
                    right_ref = operand
                link = self._one_link(node, node.ops[index], prev_ref, right_ref)
                if index + 1 < len(node.ops):
                    prev_ref = right_ref
            finally:
                inner = self.prelude
                self.prelude = saved
            inner.append(_assign(result, link, span))
            cond = self._truthy(_name(result, bool_t, span), span)
            self.prelude.append({"kind": "if", "cond": cond, "then": inner, "else": [], "span": span})
        return _name(result, bool_t, span)

    def _chain_pin(self, expr: ast.expr) -> ast.Name:
        value = self._expr(expr)
        temp = self._temp("cmp")
        self.locals[temp] = value["type"]
        pinned = ast.Name(id=temp, ctx=ast.Load())
        ast.copy_location(pinned, expr)
        self.prelude.append(_assign(temp, value, span_of(expr, self.filename)))
        return pinned

    def _one_link(self, origin: ast.Compare, op: ast.cmpop, left: ast.expr, right: ast.expr) -> dict[str, Any]:
        compare = ast.Compare(left=left, ops=[op], comparators=[right])
        ast.copy_location(compare, origin)
        return self._compare(compare, span_of(origin, self.filename))

    def _contains(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        folded = self._const_tuple_membership(node, span, negated)
        if folded is not None:
            return folded
        needle = self._expr(node.left)
        hay = self._expr(node.comparators[0])
        hay_kind = hay["type"]["kind"]
        if hay_kind in {"str", "bytes"}:
            if needle["type"]["kind"] != hay_kind:
                self.fail(node, "PS1003", f"in on {hay_kind} takes {hay_kind}")
        elif hay_kind == "list":
            if hay["type"]["elem"]["kind"] not in _SCALAR or not types_equal(needle["type"], hay["type"]["elem"]):
                self.fail(node, "PS1003", "in on a list takes an element of that list")
        elif hay_kind == "dict":
            if not types_equal(needle["type"], hay["type"]["key"]):
                self.fail(node, "PS1003", "in on a dict takes a key")
        else:
            self.fail(node, "PS2002", "in is lowered for str, bytes, list, and dict")
        if hay_kind == "list" and not hay["type"].get("set") and hay["type"]["elem"]["kind"] == "float":
            return self._float_list_membership(needle, hay, span, negated)
        return {
            "kind": "contains",
            "hay": hay,
            "needle": needle,
            "negated": negated,
            "type": {"kind": "bool"},
            "span": span,
        }

    def _float_list_membership(
        self, needle: dict[str, Any], hay: dict[str, Any], span: dict[str, Any], negated: bool
    ) -> dict[str, Any]:
        """Float list membership is ==, so NaN is not a member of a list of NaNs.

        Int and str lists stay on the contains helper. A set stays there too.
        """
        int_t = {"kind": "int"}
        float_t = {"kind": "float"}
        bool_t = {"kind": "bool"}
        held = self._temp("fneedle")
        src = self._temp("fseq")
        idx = self._temp("fidx")
        length = self._temp("flen")
        found = self._temp("fhas")
        item = self._temp("felt")
        self.locals[held] = float_t
        self.locals[src] = hay["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        self.locals[found] = bool_t
        self.locals[item] = float_t
        self.prelude.extend(
            [
                _assign(held, needle, span),
                _assign(src, hay, span),
                _assign(length, _len_call(_name(src, hay["type"], span), span), span),
                _assign(idx, _const_int(0, span), span),
                _assign(found, _const_bool(False, span), span),
                {
                    "kind": "while",
                    "cond": _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    "body": [
                        _assign(
                            item,
                            {
                                "kind": "subscript",
                                "value": _name(src, hay["type"], span),
                                "index": _name(idx, int_t, span),
                                "type": float_t,
                                "span": span,
                            },
                            span,
                        ),
                        {
                            "kind": "if",
                            "cond": _compare(
                                "==", _name(item, float_t, span), _name(held, float_t, span), span
                            ),
                            "then": [_assign(found, _const_bool(True, span), span)],
                            "else": [],
                            "span": span,
                        },
                        _assign(
                            idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span
                        ),
                    ],
                    "span": span,
                },
            ]
        )
        result = _name(found, bool_t, span)
        if negated:
            return {"kind": "unary", "op": "not", "operand": result, "type": bool_t, "span": span}
        return result

    def _const_tuple_membership(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any] | None:
        """`value in (c0, c1, ...)` of one const type is `==` joined by `or`.

        The tuple is not a class. Empty is false. One element is one `==`.
        `not in` is `not` of that test. A name, a list, a mix, or a non-constant
        element stays on the ordinary path.
        """
        hay = node.comparators[0]
        if not isinstance(hay, ast.Tuple) or any(isinstance(elt, ast.Starred) for elt in hay.elts):
            return None
        kinds = [_plain_const_kind(elt) for elt in hay.elts]
        if any(kind is None for kind in kinds) or len(set(kinds)) > 1:
            return None
        needle = self._expr(node.left)
        if not hay.elts:
            self.prelude.append({"kind": "expr", "expr": needle, "span": span})
            return _const_bool(negated, span)
        elem = kinds[0]
        if needle["type"]["kind"] != elem:
            if len(hay.elts) < 2:
                self.fail(hay, "PS2002", "tuples need at least two fields")
            self.fail(node, "PS2002", "in is lowered for str, bytes, list, and dict")
        temp = self._temp("in")
        self.locals[temp] = needle["type"]
        self.prelude.append(_assign(temp, needle, span))
        compares: list[ast.expr] = []
        for elt in hay.elts:
            left = ast.Name(id=temp, ctx=ast.Load())
            ast.copy_location(left, node.left)
            compare = ast.Compare(left=left, ops=[ast.Eq()], comparators=[elt])
            ast.copy_location(compare, node)
            compares.append(compare)
        if len(compares) == 1:
            test = self._expr(compares[0])
        else:
            chain = ast.BoolOp(op=ast.Or(), values=compares)
            ast.copy_location(chain, node)
            test = self._boolop(chain, span)
        if negated:
            return {"kind": "unary", "op": "not", "operand": test, "type": {"kind": "bool"}, "span": span}
        return test

    def _list(self, node: ast.List, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            if expect is None or expect["kind"] != "list":
                self.fail(node, "PS1001", "an empty list needs a list annotation")
            return {"kind": "list", "elems": [], "type": expect, "span": span}
        elem_expect = expect["elem"] if expect and expect["kind"] == "list" else None
        elems = [self._expr(elt, elem_expect) for elt in node.elts]
        elem = elems[0]["type"]
        if elem["kind"] == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        for item in elems[1:]:
            if not types_equal(item["type"], elem):
                self.fail(node, "PS1003", "list elements have one type")
        typ = {"kind": "list", "elem": elem}
        if expect and expect["kind"] == "list":
            self._expect(node, typ, expect, "list")
        return {"kind": "list", "elems": elems, "type": typ, "span": span}

    def _dict(self, node: ast.Dict, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.keys or any(key is None for key in node.keys):
            if expect is None or expect["kind"] != "dict":
                self.fail(node, "PS1001", "an empty dict needs a dict annotation")
            if any(key is None for key in node.keys):
                self.fail(node, "PS2002", "dict unpacking is not lowered")
            return {"kind": "dict", "pairs": [], "type": expect, "span": span}
        key_expect = expect["key"] if expect and expect["kind"] == "dict" else None
        val_expect = expect["value"] if expect and expect["kind"] == "dict" else None
        pairs = []
        for key_node, val_node in zip(node.keys, node.values, strict=True):
            assert key_node is not None
            key = self._expr(key_node, key_expect)
            value = self._expr(val_node, val_expect)
            pairs.append({"key": key, "value": value})
        key_t = pairs[0]["key"]["type"]
        val_t = pairs[0]["value"]["type"]
        if key_t["kind"] not in {"int", "str"}:
            self.fail(node, "PS2002", "dict keys are int or str")
        if val_t["kind"] == "bool":
            self.fail(node, "PS2002", "dict values of bool are not lowered")
        for pair in pairs[1:]:
            if not types_equal(pair["key"]["type"], key_t) or not types_equal(pair["value"]["type"], val_t):
                self.fail(node, "PS1003", "dict entries have one key type and one value type")
        typ = {"kind": "dict", "key": key_t, "value": val_t}
        if expect and expect["kind"] == "dict":
            self._expect(node, typ, expect, "dict")
        return {"kind": "dict", "pairs": pairs, "type": typ, "span": span}

    def _subscript(self, node: ast.Subscript, span: dict[str, Any]) -> dict[str, Any]:
        if isinstance(node.slice, ast.Slice):
            return self._slice(node, span)
        value = self._expr(node.value)
        index = self._expr(node.slice)
        kind = value["type"]["kind"]
        if kind == "class" and self.classes.get(value["type"]["name"], {}).get("tuple"):
            return self._tuple_subscript(node, value, span)
        if kind == "list" and value["type"].get("set"):
            self.fail(node, "PS2002", "a set has no subscript")
        if kind == "list":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a list index is int")
            result = value["type"]["elem"]
        elif kind == "dict":
            self._expect(node, index["type"], value["type"]["key"], "dict key")
            result = value["type"]["value"]
        elif kind == "str":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a str index is int")
            result = {"kind": "str"}
        elif kind == "bytes":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a bytes index is int")
            result = {"kind": "int"}
        elif kind == "class" and value["type"].get("name") == "Deque":
            if index["type"]["kind"] != "int":
                self.fail(node, "PS1003", "a deque index is int")
            if "elem" not in value["type"]:
                self.fail(node, "PS2002", "this deque has no element type")
            result = value["type"]["elem"]
        else:
            self.fail(node, "PS2002", "subscript is lowered for list, dict, str, and bytes")
        return {"kind": "subscript", "value": value, "index": index, "type": result, "span": span}

    def _attribute(self, node: ast.Attribute, span: dict[str, Any]) -> dict[str, Any]:
        if (
            isinstance(node.value, ast.Call)
            and node.attr == "name"
            and self._is_mod(node.value, "threading", "current_thread")
        ):
            return self._abi_call(
                "threading", "current_thread.name", [], {"kind": "str"}, "ps_island_thread_name", span
            )
        resolved = self._resolve_module(node)
        if resolved is not None:
            dotted, module, member, status = resolved
            if status == "erased":
                self.fail(node, "PS2002", f"{dotted} is a typing name and is not a value")
            if status == "pkg":
                item = self._pkg_item(node, module, member)
                if item["kind"] == "const":
                    value = item["value"]
                    if value.get("kind") == "tuple-const":
                        return self._materialize_tuple(value["elems"], span)
                    return {**value, "span": span}
                if item["kind"] == "class":
                    self.fail(node, "PS2002", f"class {dotted} is not a value")
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            if status == "queued":
                self._queued(node, dotted)
            if status == "value":
                self.fail(node, "PS2002", f"module {dotted} is not a value in the static subset")
            if module == "math" and member in MATH_CONSTANTS:
                return {"kind": "const", "value": MATH_CONSTANTS[member], "type": {"kind": "float"}, "span": span}
            constant = module_constant(module, member)
            if constant is not None:
                value, typ = constant
                return {"kind": "const", "value": value, "type": typ, "span": span}
            if (module == "math" and member in MATH_FUNCTIONS) or wave_function(module, member) is not None:
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            if member in SPECIAL_FUNCTIONS.get(module, frozenset()) or member_callable(module, member):
                self.fail(node, "PS2002", f"{dotted} is a function and is not a value")
            self.fail(node, "PS2001", f"{dotted} is not in the lowered subset of {module}")
        class_name = self.class_aliases.get(node.value.id, node.value.id) if isinstance(node.value, ast.Name) else None
        if class_name is not None and class_name in self.classes:
            info = self.classes[class_name]
            if info.get("enum"):
                member = info["members"].get(node.attr)
                if member is None:
                    self.fail(node, "PS2001", f"{node.value.id}.{node.attr} is not an enum member")
                return {"kind": "const", "value": str(member), "type": {"kind": "int"}, "span": span}
        value = self._expr(node.value)
        return self._lookup_attr(value, node.attr, span, node)

    def _call(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(node.func, ast.Name):
            return self._call_name(node, span, expect)
        if isinstance(node.func, ast.Attribute):
            return self._call_attr(node, span, expect)
        self.fail(node, "PS2002", "this call is not lowered")
        raise AssertionError("unreachable")

    def _call_name(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        if name == "super":
            self.fail(node, "PS2002", "super is not lowered")
        if name == "all":
            return self._all_any(node, span, True)
        if name == "any":
            return self._all_any(node, span, False)
        if name == "enumerate":
            self.fail(node, "PS2002", "enumerate is only lowered as the iterator of a for")
        if name == "zip":
            self.fail(node, "PS2002", "zip is only lowered as the iterator of a for")
        if name == "set":
            return self._set_call(node, span, expect)
        if name == "frozenset":
            return self._frozenset_call(node, span, expect)
        if name == "bool":
            return self._bool_call(node, span)
        if name == "list":
            return self._list_call(node, span, expect)
        if name == "range":
            self.fail(node, "PS2002", "range is only lowered as the iterator of a for")
        found = builtin_call(self, node, span)
        if found is not None:
            return found
        if name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is a typing name and is not a value")
        if name in {"int", "float", "len", "str"}:
            if name == "float":
                special = _float_special_arg(node)
                if special is not None:
                    return {"kind": "const", "value": special, "type": {"kind": "float"}, "span": span}
            self._reject_keywords(node)
            args = [self._expr(arg) for arg in node.args]
            spec = builtin_spec(name, [arg["type"] for arg in args])
            if spec is None:
                self.fail(node, "PS2003", f"{name} is not lowered for these arguments")
            if name == "float":
                return {"kind": "cast", "op": "int_to_float", "value": args[0], "type": spec, "span": span}
            if name == "str" and args[0]["type"]["kind"] == "str":
                return args[0]
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
            if binding.kind == "func":
                return self._module_call(node, span, binding.module, binding.attr, expect)
            if binding.kind == "pyfunc":
                return self._linked_call(node, span, self.pkg_funcs[name], name)
            self.fail(node, "PS2002", f"{name} is not a function")
        if name in self.class_aliases or name in self.classes:
            return self._construct(node, span, self.class_aliases.get(name, name))
        if name in self.sigs:
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

    def _call_attr(self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        resolved = self._resolve_module(node.func)
        if resolved is not None:
            dotted, module, member, status = resolved
            if status == "erased":
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
                self.fail(node, "PS2001", f"{dotted} is not in the lowered subset of {module}")
            return self._module_call(node, span, module, member, expect)
        named = None
        if isinstance(node.func.value, ast.Name):
            named = self.class_aliases.get(node.func.value.id, node.func.value.id)
        if named is not None and named in self.classes:
            return self._named_class_call(node, span, named)
        receiver = self._expr(node.func.value)
        if receiver["type"]["kind"] == "class" and receiver["type"].get("name") == "Path":
            if node.func.attr in {"__enter__", "__exit__"}:
                return self._path_context(node, span, receiver)
        if receiver["type"]["kind"] == "list" and receiver["type"].get("set"):
            return self._set_method(node, span, receiver)
        kind = receiver["type"]["kind"]
        if kind in {"str", "bytes"}:
            self._reject_keywords(node)
            args = [self._expr(arg) for arg in node.args]
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
        if kind == "class" and synth_method(receiver["type"]["name"], node.func.attr) is not None:
            return self._synth_call(node, span, receiver)
        if kind == "dict" and node.func.attr == "keys":
            return self._dict_keys(node, span, receiver)
        if kind == "dict" and receiver["type"].get("ordered") and node.func.attr in {"move_to_end", "popitem"}:
            self.fail(node, "PS2001", f"OrderedDict.{node.func.attr} is not lowered")
        if kind == "class":
            return self._method_call(node, span, receiver)
        self.fail(node, "PS2001", f"call of attribute is not lowered ({_dotted(node.func)})")
        raise AssertionError("unreachable")

    def _nativeprobe_call(self, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
        specs = {
            "crc32": ("ps_mix_crc32", [{"kind": "bytes"}]),
            "crc32_island": ("ps_island_mix_crc32", [{"kind": "bytes"}]),
            "crc32_n": ("ps_mix_crc32_n", [{"kind": "bytes"}, {"kind": "int"}]),
            "crc32_n_island": ("ps_island_mix_crc32_n", [{"kind": "bytes"}, {"kind": "int"}]),
        }
        found = specs.get(attr)
        if found is None:
            self.fail(node, "PS2001", f"nativeprobe.{attr} is not in the lowered subset of nativeprobe")
        symbol, params = found
        self._reject_keywords(node)
        if len(node.args) != len(params):
            self.fail(node, "PS2003", f"nativeprobe.{attr} expects {len(params)} argument(s)")
        args = []
        for arg_node, want in zip(node.args, params, strict=True):
            arg = self._expr(arg_node, want)
            if not types_equal(arg["type"], want):
                self.fail(node, "PS1003", f"nativeprobe.{attr} argument is {_show(arg['type'])}")
            args.append(arg)
        return self._abi_call("nativeprobe", attr, args, {"kind": "int"}, symbol, span)

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
        return _mod_call("json", "dumps", [arg], {"kind": "str"}, span)

    def _json_type(self, node: ast.AST, typ: dict[str, Any], what: str) -> None:
        kind = typ["kind"]
        if kind == "float":
            self.fail(node, "PS2001", "float JSON is not lowered")
        if kind in {"int", "bool", "str"}:
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
        if name == "findall":
            if len(node.args) != 1:
                self.fail(node, "PS2003", "Pattern.findall expects 1 argument(s)")
            ret, symbol = self._group_tuple(node, node.func.value)
            text = self._expr(node.args[0])
            if text["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Pattern.findall argument is " + _show(text["type"]) + ", expected str")
            expr = _mod_call("re", "findall", [pattern, text], ret, span)
            if symbol:
                expr["symbol"] = symbol
            return expr
        if name == "sub":
            if len(node.args) != 2:
                self.fail(node, "PS2003", "Pattern.sub expects 2 argument(s)")
            repl = self._expr(node.args[0])
            text = self._expr(node.args[1])
            if repl["type"]["kind"] != "str" or text["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Pattern.sub arguments are str")
            return _mod_call("re", "sub", [pattern, repl, text], {"kind": "str"}, span)
        if name == "split":
            if len(node.args) != 1:
                self.fail(node, "PS2003", "Pattern.split expects 1 argument(s)")
            text = self._expr(node.args[0])
            if text["type"]["kind"] != "str":
                self.fail(node, "PS1003", "Pattern.split argument is " + _show(text["type"]) + ", expected str")
            return _mod_call("re", "split", [pattern, text], {"kind": "list", "elem": {"kind": "str"}}, span)
        self.fail(node, "PS2001", f"Pattern.{name} is not lowered")
        raise AssertionError("unreachable")

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
        owner = info.get("method_owner", {}).get(method, class_name)
        if kind in {"static", "class"}:
            args = self._fill_args(node, sig[0], f"{class_name}.{method}")
        else:
            params = [param for param in sig[0] if param["name"] != "self"]
            args = [receiver, *self._fill_args(node, params, f"{class_name}.{method}")]
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{owner}.{method}"},
            "args": args,
            "type": sig[1],
            "span": span,
        }

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

    def _with(self, stmt: ast.With, span: dict[str, Any]) -> list[dict[str, Any]]:
        if len(stmt.items) != 1:
            self.fail(stmt, "PS2002", "with lowers one contextlib item")
        item = stmt.items[0]
        ctx = item.context_expr
        if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "open":
            return self._with_open(stmt, item, span)
        if self._is_mod(ctx, "importlib.resources", "as_file"):
            return self._with_as_file(stmt, item, span)
        if item.optional_vars is not None:
            self.fail(stmt, "PS1206", "with-as is not lowered")
        if self._is_mod(ctx, "contextlib", "suppress"):
            assert isinstance(ctx, ast.Call)
            if len(ctx.args) != 1 or not isinstance(ctx.args[0], ast.Name) or ctx.args[0].id not in EXCEPTIONS:
                self.fail(ctx, "PS2002", "suppress takes one lowered exception")
            return [
                {
                    "kind": "try",
                    "body": self._body(list(stmt.body)),
                    "handlers": [{"exc": [ctx.args[0].id], "body": []}],
                    "span": span,
                }
            ]
        if self._is_mod(ctx, "contextlib", "nullcontext"):
            assert isinstance(ctx, ast.Call)
            if ctx.args:
                self.fail(ctx, "PS2003", "nullcontext takes no arguments")
            return self._body(list(stmt.body))
        self.fail(
            stmt,
            "PS2002",
            "with is lowered for contextlib.suppress, contextlib.nullcontext, open, and importlib.resources.as_file",
        )
        raise AssertionError("unreachable")

    def _for_repeat(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if len(call.args) != 2:
            self.fail(call, "PS2003", "itertools.repeat takes a value and a count")
        value = self._expr(call.args[0])
        count = self._expr(call.args[1])
        if count["type"]["kind"] != "int":
            self.fail(call, "PS1003", "itertools.repeat count is int")
        if value["type"]["kind"] not in _SCALAR:
            self.fail(call, "PS2002", "itertools.repeat takes a scalar")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, value["type"], stmt.target)
        held = self._temp("rep")
        idx = self._temp("idx")
        stop = self._temp("stop")
        int_t = {"kind": "int"}
        self.locals[held] = value["type"]
        self.locals[idx] = int_t
        self.locals[stop] = int_t
        body = self._loop_body(stmt)
        prefix: list[dict[str, Any]] = []
        if fresh:
            prefix.append(_assign(target, _zero(value["type"], span), span))
        prefix.extend(
            [
                _assign(held, value, span),
                _assign(idx, _const_int(0, span), span),
                _assign(stop, count, span),
                *self._user_while(
                    _compare("<", _name(idx, int_t, span), _name(stop, int_t, span), span),
                    [
                        _assign(target, _name(held, value["type"], span), span),
                        *body,
                        *self._skip_on_break(
                            [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                            span,
                        ),
                    ],
                    span,
                ),
            ]
        )
        return prefix

    def _for_chain(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if not 2 <= len(call.args) <= 8:
            self.fail(call, "PS2003", "itertools.chain takes 2 to 8 lists")
        lists = [self._expr(arg) for arg in call.args]
        if any(item["type"]["kind"] != "list" for item in lists):
            self.fail(call, "PS2002", "itertools.chain takes lists")
        elem = lists[0]["type"]["elem"]
        if elem["kind"] not in _SCALAR:
            self.fail(call, "PS2002", f"for over list[{_show(elem)}] is not lowered")
        for item in lists[1:]:
            if not types_equal(item["type"]["elem"], elem):
                self.fail(call, "PS1003", "itertools.chain lists have one element type")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, elem, stmt.target)
        body = self._loop_body(stmt)
        prefix: list[dict[str, Any]] = []
        if fresh:
            prefix.append(_assign(target, _zero(elem, span), span))
        prefix.extend(self._break_init(span))
        for item in lists:
            prefix.extend(self._list_loop(target, item, body, span))
        return prefix

    def _list_loop(
        self, target: str, iterable: dict[str, Any], body: list[dict[str, Any]], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = iterable["type"]["elem"]
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        int_t = {"kind": "int"}
        self.locals[seq] = iterable["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        return [
            _assign(seq, iterable, span),
            _assign(
                length,
                {
                    "kind": "call",
                    "callee": {"kind": "builtin", "name": "len"},
                    "args": [_name(seq, iterable["type"], span)],
                    "type": int_t,
                    "span": span,
                },
                span,
            ),
            _assign(idx, _const_int(0, span), span),
            *self._user_while(
                _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                [
                    _assign(
                        target,
                        {
                            "kind": "subscript",
                            "value": _name(seq, iterable["type"], span),
                            "index": _name(idx, int_t, span),
                            "type": elem,
                            "span": span,
                        },
                        span,
                    ),
                    *body,
                    *self._skip_on_break(
                        [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                        span,
                    ),
                ],
                span,
                init=False,
            ),
        ]

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

    def _synth_call(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        class_name = receiver["type"]["name"]
        if class_name == "Path" and node.func.attr == "read_text" and node.keywords:
            return self._path_read_text(node, span, receiver)
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
            self.fail(node, "PS2002", "**kwargs are not lowered")
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
            self.fail(node, "PS2002", "**kwargs are not lowered")
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            self.fail(node, "PS2002", "starred calls are not lowered")
        var_index = next((index for index, param in enumerate(params) if param.get("vararg")), None)
        if var_index is not None:
            return self._fill_varargs(node, params, var_index, what)
        if len(node.args) > len(params):
            self.fail(node, "PS1003", f"{what} expects {len(params)} argument(s)")
        slots: list[dict[str, Any] | None] = [None] * len(params)
        names = [param["name"] for param in params]
        for index, arg_node in enumerate(node.args):
            slots[index] = self._expr(arg_node, params[index]["type"])
        seen: set[str] = set()
        for kw in node.keywords:
            assert kw.arg is not None
            if kw.arg not in names:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
            index = names.index(kw.arg)
            if index < len(node.args) or kw.arg in seen:
                self.fail(kw, "PS2003", f"{kw.arg} given more than once")
            seen.add(kw.arg)
            slots[index] = self._expr(kw.value, params[index]["type"])
        args: list[dict[str, Any]] = []
        for index, param in enumerate(params):
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects {len(params)} argument(s)")
                got = param["default"]
            if not types_equal(got["type"], param["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        return args

    def _fill_varargs(
        self, node: ast.Call, params: list[dict[str, Any]], var_index: int, what: str
    ) -> list[dict[str, Any]]:
        fixed = params[:var_index]
        varparam = params[var_index]
        if any(kw.arg == varparam["name"] for kw in node.keywords):
            self.fail(node, "PS2002", "a starred parameter is not passed by keyword")
        positional = list(node.args[: len(fixed)])
        extra = list(node.args[len(fixed) :])
        slots: list[dict[str, Any] | None] = [None] * len(fixed)
        names = [param["name"] for param in fixed]
        for index, arg_node in enumerate(positional):
            slots[index] = self._expr(arg_node, fixed[index]["type"])
        seen: set[str] = set()
        for kw in node.keywords:
            assert kw.arg is not None
            if kw.arg not in names:
                self.fail(kw, "PS2003", f"unexpected keyword argument {kw.arg}")
            index = names.index(kw.arg)
            if index < len(positional) or kw.arg in seen:
                self.fail(kw, "PS2003", f"{kw.arg} given more than once")
            seen.add(kw.arg)
            slots[index] = self._expr(kw.value, fixed[index]["type"])
        args: list[dict[str, Any]] = []
        for index, param in enumerate(fixed):
            got = slots[index]
            if got is None:
                if "default" not in param:
                    self.fail(node, "PS1003", f"{what} expects {len(fixed)} argument(s)")
                got = param["default"]
            if not types_equal(got["type"], param["type"]):
                self.fail(node, "PS1003", f"argument {param['name']} of {what} has the wrong type")
            args.append(got)
        elem = varparam["type"]["elem"]
        elems = []
        for arg_node in extra:
            value = self._expr(arg_node, elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", f"argument {varparam['name']} of {what} has the wrong type")
            elems.append(value)
        span = span_of(node, self.filename)
        args.append({"kind": "list", "elems": elems, "type": varparam["type"], "span": span})
        return args

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

    def _frozenset_call(
        self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
    ) -> dict[str, Any]:
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
        kind = arg["type"]["kind"]
        if kind not in {"bool", "int", "float", "str", "bytes", "list", "dict"}:
            self.fail(node, "PS2002", f"bool() of {kind} is not lowered")
        return self._truthy(arg, span)

    def _list_call(
        self, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
    ) -> dict[str, Any]:
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "list expects 1 argument(s)")
        source = self._expr(node.args[0])
        if source["type"]["kind"] != "list":
            self.fail(node, "PS2002", "list() takes a list, a set, or *args")
        elem = source["type"]["elem"]
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

    def _set_method(self, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(node.func, ast.Attribute)
        if self._receiver_is_frozen(node, receiver):
            self.fail(node, "PS2002", "frozenset has no mutating methods")
        if node.func.attr != "add":
            self.fail(node, "PS2001", "set.add is the only lowered set method")
        self._reject_keywords(node)
        if len(node.args) != 1:
            self.fail(node, "PS2003", "set.add expects 1 argument(s)")
        if receiver["kind"] != "name":
            self.fail(node, "PS2002", "set.add needs a name")
        elem = receiver["type"]["elem"]
        value = self._expr(node.args[0], elem)
        if not types_equal(value["type"], elem):
            self.fail(node, "PS1003", "set.add takes an element of that set")
        self.prelude.extend(self._set_insert_stmts(receiver["id"], receiver["type"], value, span))
        return _const_none(span)

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

    def _truthy(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        if kind == "bool":
            return expr
        if kind == "int":
            return _compare("!=", expr, _const_int(0, span), span)
        if kind == "float":
            zero = {"kind": "const", "value": "0.0", "type": {"kind": "float"}, "span": span}
            return _compare("!=", expr, zero, span)
        if kind in {"str", "bytes", "list", "dict"}:
            length = {
                "kind": "call",
                "callee": {"kind": "builtin", "name": "len"},
                "args": [expr],
                "type": {"kind": "int"},
                "span": span,
            }
            return _compare("!=", length, _const_int(0, span), span)
        self.fail(ast.Pass(), "PS2004", f"and/or on {kind} is not lowered")
        raise AssertionError("unreachable")

    def _mixed_cond_kind(self, kind: str) -> bool:
        return kind == "bool" or kind in _TRUTHY

    def _bool_operand(self, node: ast.expr, as_cond: bool) -> dict[str, Any]:
        if as_cond and isinstance(node, ast.BoolOp):
            return self._boolop(node, span_of(node, self.filename), as_cond=True)
        return self._expr(node)

    def _boolop(self, node: ast.BoolOp, span: dict[str, Any], *, as_cond: bool = False) -> dict[str, Any]:
        """Same-type `and`/`or` still yields the operand.

        A mixed `and`/`or` in a condition is the truthiness of each side, joined
        by the existing bool `and`/`or`. The right side is evaluated only when
        the left does not decide it. Used as a value, the mixed form stays fenced.
        """
        op_and = isinstance(node.op, ast.And)
        first = self._bool_operand(node.values[0], as_cond)
        typ = first["type"]
        if typ["kind"] not in _TRUTHY | {"bool", "float"}:
            self.fail(node, "PS2004", f"and/or on {typ['kind']} is not lowered")
        temp = self._temp("boolop")
        self.locals[temp] = typ
        self.prelude.append(_assign(temp, first, span))
        for value_node in node.values[1:]:
            saved = self.prelude
            self.prelude = []
            right = self._bool_operand(value_node, as_cond)
            inner = self.prelude
            self.prelude = saved
            if not types_equal(right["type"], typ):
                left_ok = self._mixed_cond_kind(typ["kind"])
                right_ok = self._mixed_cond_kind(right["type"]["kind"])
                if not as_cond or not left_ok or not right_ok:
                    if not as_cond and left_ok and right_ok:
                        self.fail(node, "PS2004", "and/or across two types is not a value")
                    self.fail(node, "PS2004", "and/or across two types is not lowered")
                if typ["kind"] != "bool":
                    bool_temp = self._temp("cond")
                    self.locals[bool_temp] = {"kind": "bool"}
                    self.prelude.append(_assign(bool_temp, self._truthy(_name(temp, typ, span), span), span))
                    temp = bool_temp
                    typ = {"kind": "bool"}
                inner.append(_assign(temp, self._truthy(right, span), span))
            else:
                inner.append(_assign(temp, right, span))
            cond = self._truthy(_name(temp, typ, span), span)
            if not op_and:
                cond = {"kind": "unary", "op": "not", "operand": cond, "type": {"kind": "bool"}, "span": span}
            self.prelude.append({"kind": "if", "cond": cond, "then": inner, "else": [], "span": span})
        return _name(temp, typ, span)

    def _ifexp(self, node: ast.IfExp, span: dict[str, Any]) -> dict[str, Any]:
        """`a if cond else b` is the if statement BoolOp already uses.

        The condition is whatever `if` already accepts. Both operands lower to
        one type. Each operand's statements sit in its own branch, so the
        untaken call does not run. A nested conditional is the same desugar.
        """
        cond = self._bool_cond(node.test)
        saved = self.prelude
        self.prelude = []
        try:
            body = self._expr(node.body)
            body_pre = self.prelude
            self.prelude = []
            orelse = self._expr(node.orelse)
            orelse_pre = self.prelude
        finally:
            self.prelude = saved
        if not types_equal(body["type"], orelse["type"]):
            self.fail(node, "PS2004", "conditional expression across two types is not lowered")
        typ = body["type"]
        then_body = self._ifexp_branch(body_pre, body, span)
        else_body = self._ifexp_branch(orelse_pre, orelse, span)
        if typ["kind"] == "none":
            # None is not a register. The if still runs the taken operand.
            self.prelude.append({"kind": "if", "cond": cond, "then": then_body, "else": else_body, "span": span})
            return _const_none(span)
        temp = self._temp("ifexp")
        self.locals[temp] = typ
        then_body.append(_assign(temp, body, span))
        else_body.append(_assign(temp, orelse, span))
        self.prelude.append({"kind": "if", "cond": cond, "then": then_body, "else": else_body, "span": span})
        return _name(temp, typ, span)

    def _ifexp_branch(
        self, prelude: list[dict[str, Any]], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if value["type"]["kind"] != "none":
            return list(prelude)
        body = list(prelude)
        if value["kind"] != "const":
            body.append({"kind": "expr", "expr": value, "span": span})
        return body

    def _joined(self, node: ast.JoinedStr, span: dict[str, Any]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(self._const(value, span_of(value, self.filename)))
                continue
            if isinstance(value, ast.FormattedValue):
                if value.conversion != -1 or value.format_spec is not None:
                    self.fail(value, "PS2002", "f-string format specs are not lowered")
                parts.append(self._as_str(self._expr(value.value), value))
                continue
            self.fail(value, "PS2002", "f-strings are not lowered")
        if self.function is None:
            folded = _const_str_join(parts, span)
            if folded is not None:
                return folded
        return _concat_str(parts, span)

    def _as_str(self, expr: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        kind = expr["type"]["kind"]
        span = expr["span"]
        if kind == "str":
            return expr
        if kind in {"int", "float"}:
            return {
                "kind": "call",
                "callee": {"kind": "builtin", "name": "str"},
                "args": [expr],
                "type": {"kind": "str"},
                "span": span,
            }
        self.fail(node, "PS2002", f"formatting {_show(expr['type'])} is not lowered")
        raise AssertionError("unreachable")

    def _percent(self, node: ast.BinOp, span: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(node.left, ast.Constant) or not isinstance(node.left.value, str):
            self.fail(node, "PS2004", "a format string must be a literal")
        if any(ord(ch) > 255 for ch in node.left.value):
            self.fail(node, "PS2005", "string literals outside Latin-1 are not lowered")
        pieces = _percent_pieces(node.left.value)
        if pieces is None:
            self.fail(node, "PS2004", "% formatting is lowered for %s and %d")
        specs = [kind for kind, _text in pieces if kind != "lit"]
        if isinstance(node.right, ast.Tuple):
            values = list(node.right.elts)
        else:
            values = [node.right]
        if len(values) != len(specs):
            self.fail(node, "PS2003", "format string and values disagree")
        rendered: list[dict[str, Any]] = []
        index = 0
        for kind, text in pieces:
            if kind == "lit":
                if text:
                    rendered.append({"kind": "const", "value": text, "type": {"kind": "str"}, "span": span})
                continue
            expr = self._expr(values[index])
            index += 1
            if kind == "s":
                if expr["type"]["kind"] != "str":
                    self.fail(node, "PS1003", "%s takes a str")
                rendered.append(expr)
            else:
                if expr["type"]["kind"] != "int":
                    self.fail(node, "PS1003", "%d takes an int")
                rendered.append(self._as_str(expr, node))
        return _concat_str(rendered, span)

    def _slice(self, node: ast.Subscript, span: dict[str, Any]) -> dict[str, Any]:
        slc = node.slice
        assert isinstance(slc, ast.Slice)
        value = self._expr(node.value)
        kind = value["type"]["kind"]
        if kind == "list" and value["type"].get("set"):
            self.fail(node, "PS2002", "a set has no subscript")
        if kind not in {"str", "bytes", "list"}:
            self.fail(node, "PS2002", "slices are lowered for str, bytes, and list")
        start, has_start = self._slice_bound(slc.lower, span)
        stop, has_stop = self._slice_bound(slc.upper, span)
        step = self._slice_step(slc.step, span)
        start = self._pin_slice_int(start, span)
        stop = self._pin_slice_int(stop, span)
        step = self._pin_slice_int(step, span)
        symbol = "ps_seq_slice" if kind == "list" else "ps_str_slice"
        result = value["type"] if kind != "list" else value["type"]
        if kind == "str":
            result = {"kind": "str"}
        elif kind == "bytes":
            result = {"kind": "bytes"}
        return self._abi_call(
            "builtins",
            "slice",
            [value, start, stop, _const_int(has_start, span), _const_int(has_stop, span), step],
            result,
            symbol,
            span,
        )

    def _slice_bound(self, node: ast.expr | None, span: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if node is None:
            return _const_int(0, span), 0
        expr = self._expr(node, {"kind": "int"})
        if expr["type"]["kind"] != "int":
            self.fail(node, "PS1003", "a slice bound is int")
        return expr, 1

    def _slice_step(self, node: ast.expr | None, span: dict[str, Any]) -> dict[str, Any]:
        """A positive step is the existing slice. A negative step stays fenced.

        Step 0 is the runtime's ValueError. The step expression is one operand,
        so it runs once.
        """
        if node is None:
            return _const_int(1, span)
        expr = self._expr(node, {"kind": "int"})
        if expr["type"]["kind"] != "int":
            self.fail(node, "PS1003", "a slice step is int")
        static = _static_int(expr)
        if static is not None and static < 0:
            self.fail(node, "PS2002", "a negative slice step is not lowered")
        return expr

    def _pin_slice_int(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Store a slice operand once. A module call emits its arguments twice."""
        if expr["kind"] in {"const", "name"}:
            return expr
        temp = self._temp("sl")
        self.locals[temp] = {"kind": "int"}
        self.prelude.append(_assign(temp, expr, span))
        return _name(temp, {"kind": "int"}, span)

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

    def _loop_body(self, stmt: ast.For) -> list[dict[str, Any]]:
        if self.comp_elt is None or self.comp_dest is None:
            return [*self._continue_reset(span_of(stmt, self.filename)), *self._body(list(stmt.body))]
        filt_pre: list[dict[str, Any]] = []
        filt_cond: dict[str, Any] | None = None
        if self.comp_if is not None:
            saved = self.prelude
            self.prelude = []
            try:
                filt_cond = self._bool_cond(self.comp_if)
                filt_pre = self.prelude
            finally:
                self.prelude = saved
        saved = self.prelude
        self.prelude = []
        try:
            elt = self._expr(self.comp_elt)
            inner = self.prelude
        finally:
            self.prelude = saved
        elem = elt["type"]
        if elem["kind"] == "bool" or elem["kind"] not in {"int", "float", "str", "bytes", "list", "dict", "class"}:
            self.fail(self.comp_elt, "PS2002", f"list[{_show(elem)}] is not lowered")
        typ = {"kind": "list", "elem": elem}
        if self.comp_expect is not None and self.comp_expect["kind"] == "list":
            self._expect(self.comp_elt, typ, self.comp_expect, "list")
        if not self.comp_ready:
            self.locals[self.comp_dest] = typ
            empty = {"kind": "list", "elems": [], "type": typ, "span": span_of(self.comp_elt, self.filename)}
            self.comp_init = _assign(self.comp_dest, empty, empty["span"])
            self.comp_ready = True
        # The push is a module call, and those arguments are emitted twice.
        # Store the element first so a call in it runs once, and only in this branch.
        slot = self._temp("elt")
        self.locals[slot] = elem
        push = {
            "kind": "expr",
            "expr": {
                "kind": "call",
                "callee": {"kind": "module", "module": "builtins", "name": "list.append"},
                "args": [_name(self.comp_dest, typ, elt["span"]), _name(slot, elem, elt["span"])],
                "symbol": "ps_list_push",
                "type": {"kind": "none"},
                "span": elt["span"],
            },
            "span": elt["span"],
        }
        kept = [*inner, _assign(slot, elt, elt["span"]), push]
        if filt_cond is None:
            return kept
        return [
            *filt_pre,
            {"kind": "if", "cond": filt_cond, "then": kept, "else": [], "span": span_of(self.comp_if, self.filename)},
        ]

    def _with_as_file(self, stmt: ast.With, item: ast.withitem, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(item.optional_vars, ast.Name):
            self.fail(stmt, "PS2002", "with as_file needs a name")
        ctx = item.context_expr
        assert isinstance(ctx, ast.Call)
        if len(ctx.args) != 1 or ctx.keywords:
            self.fail(ctx, "PS2003", "as_file expects 1 argument(s)")
        opened = self._expr(ctx.args[0])
        if opened["type"]["kind"] != "class" or opened["type"].get("name") != "Path":
            self.fail(ctx, "PS1003", "as_file takes a resource path")
        target = item.optional_vars.id
        if target in self.bindings or target in self.constants:
            self.fail(item.optional_vars, "PS2002", f"{target} is already bound")
        self.locals[target] = opened["type"]
        return [_assign(target, opened, span), *self._body(list(stmt.body))]

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

    def _with_open(self, stmt: ast.With, item: ast.withitem, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(item.optional_vars, ast.Name):
            self.fail(stmt, "PS2002", "with open needs a name")
        assert isinstance(item.context_expr, ast.Call)
        opened = self._expr(item.context_expr)
        target = item.optional_vars.id
        if target in self.bindings or target in self.constants:
            self.fail(item.optional_vars, "PS2002", f"{target} is already bound")
        self.locals[target] = opened["type"]
        close = {
            "kind": "expr",
            "expr": self._abi_call(
                "io", "close", [_name(target, opened["type"], span)], {"kind": "none"}, "ps_io_close", span
            ),
            "span": span,
        }
        body = self._inject_close(self._body(list(stmt.body)), close)
        return [
            _assign(target, opened, span),
            {
                "kind": "try",
                "body": [*body, close],
                "handlers": [
                    {
                        "exc": ["Exception"],
                        "body": [
                            close,
                            {
                                "kind": "raise",
                                "exc": "Exception",
                                "message": None,
                                "reraise": True,
                                "span": span,
                            },
                        ],
                    }
                ],
                "span": span,
            },
        ]

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

    def _one_str_pattern(self, node: ast.expr, value: dict[str, Any]) -> bool:
        """True when this is re.compile of one str constant and no keywords.

        That is the call a function already lowers to a Pattern. The stored
        value is that call, copied at each use. Anything else is not this form.
        """
        if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 1:
            return False
        arg = node.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
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

    def _for_enumerate(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords:
            self.fail(call, "PS2003", "keyword arguments are not lowered")
        if len(call.args) not in {1, 2}:
            self.fail(call, "PS2003", "enumerate takes 1 or 2 arguments")
        source = self._expr(call.args[0])
        if source["type"]["kind"] != "list" or source["type"].get("set"):
            self.fail(call, "PS2002", "enumerate() takes a list")
        elem = source["type"]["elem"]
        if elem["kind"] not in _SCALAR | {"bool"}:
            self.fail(call, "PS2002", f"enumerate() of list[{_show(elem)}] is not lowered")
        start = _const_int(0, span)
        if len(call.args) == 2:
            start = self._expr(call.args[1], {"kind": "int"})
            if start["type"]["kind"] != "int":
                self.fail(call, "PS1003", "enumerate start is int")
        return self._for_pairs(stmt, span, [source], [elem], start, "enumerate")

    def _for_zip(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords or len(call.args) != 2:
            self.fail(call, "PS2003", "zip takes 2 lists")
        sources = [self._expr(arg) for arg in call.args]
        elems: list[dict[str, Any]] = []
        for source in sources:
            if source["type"]["kind"] != "list" or source["type"].get("set"):
                self.fail(call, "PS2002", "zip() takes lists")
            elem = source["type"]["elem"]
            if elem["kind"] not in _SCALAR | {"bool"}:
                self.fail(call, "PS2002", f"zip() of list[{_show(elem)}] is not lowered")
            elems.append(elem)
        return self._for_pairs(stmt, span, sources, elems, None, "zip")

    def _for_pairs(
        self,
        stmt: ast.For,
        span: dict[str, Any],
        sources: list[dict[str, Any]],
        elems: list[dict[str, Any]],
        enum_start: dict[str, Any] | None,
        what: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "for targets a single name")
        int_t = {"kind": "int"}
        fields = ([{"kind": "int"}, *elems]) if enum_start is not None else elems
        pair = self._tuple_type(fields, stmt.iter)
        target = stmt.target.id
        fresh = self._bind_loop_target(target, pair, stmt.target)
        seqs = [self._temp("seq") for _ in sources]
        idx = self._temp("idx")
        length = self._temp("len")
        enum_i = self._temp("enum")
        for seq, source in zip(seqs, sources, strict=True):
            self.locals[seq] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        if enum_start is not None:
            self.locals[enum_i] = int_t
        body = self._loop_body(stmt)
        values: list[dict[str, Any]] = []
        if enum_start is not None:
            values.append(_name(enum_i, int_t, span))
        for seq, source, elem in zip(seqs, sources, elems, strict=True):
            values.append(
                {
                    "kind": "subscript",
                    "value": _name(seq, source["type"], span),
                    "index": _name(idx, int_t, span),
                    "type": elem,
                    "span": span,
                }
            )
        tail = [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)]
        if enum_start is not None:
            tail.append(_assign(enum_i, _binary_int("+", _name(enum_i, int_t, span), _const_int(1, span), span), span))
        step = [*_tuple_assign(target, pair, values, span), *body, *self._skip_on_break(tail, span)]
        prefix: list[dict[str, Any]] = []
        if fresh:
            zeros = [_zero(field, span) for field in fields]
            prefix.extend(_tuple_assign(target, pair, zeros, span))
        for seq, source in zip(seqs, sources, strict=True):
            prefix.append(_assign(seq, source, span))
        prefix.append(_assign(idx, _const_int(0, span), span))
        if len(sources) == 1:
            prefix.append(_assign(length, _len_call(_name(seqs[0], sources[0]["type"], span), span), span))
        else:
            left_n = self._temp("n")
            right_n = self._temp("n")
            self.locals[left_n] = int_t
            self.locals[right_n] = int_t
            prefix.extend(
                [
                    _assign(left_n, _len_call(_name(seqs[0], sources[0]["type"], span), span), span),
                    _assign(right_n, _len_call(_name(seqs[1], sources[1]["type"], span), span), span),
                    _assign(length, _name(left_n, int_t, span), span),
                    {
                        "kind": "if",
                        "cond": _compare("<", _name(right_n, int_t, span), _name(left_n, int_t, span), span),
                        "then": [_assign(length, _name(right_n, int_t, span), span)],
                        "else": [],
                        "span": span,
                    },
                ]
            )
        if enum_start is not None:
            prefix.append(_assign(enum_i, enum_start, span))
        prefix.extend(
            self._user_while(
                _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                step,
                span,
            )
        )
        if what not in {"enumerate", "zip"}:
            self.fail(stmt, "PS2002", f"{what} is not lowered")
        return prefix

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

    def _inject_close(self, body: list[dict[str, Any]], close: dict[str, Any]) -> list[dict[str, Any]]:
        """Close after a return value is computed, so ``return handle.read()`` still sees an open file."""
        out: list[dict[str, Any]] = []
        for stmt in body:
            if stmt["kind"] == "return":
                value = stmt["value"]
                if value["kind"] == "const" and value["type"]["kind"] == "none":
                    out.append(close)
                    out.append(stmt)
                    continue
                temp = self._temp("withret")
                self.locals[temp] = value["type"]
                out.append(_assign(temp, value, value["span"]))
                out.append(close)
                out.append({**stmt, "value": _name(temp, value["type"], value["span"])})
                continue
            if stmt["kind"] == "if":
                out.append(
                    {
                        **stmt,
                        "then": self._inject_close(stmt["then"], close),
                        "else": self._inject_close(stmt["else"], close),
                    }
                )
                continue
            if stmt["kind"] == "while":
                out.append({**stmt, "body": self._inject_close(stmt["body"], close)})
                continue
            if stmt["kind"] == "try":
                out.append(
                    {
                        **stmt,
                        "body": self._inject_close(stmt["body"], close),
                        "handlers": [
                            {**handler, "body": self._inject_close(handler["body"], close)}
                            for handler in stmt["handlers"]
                        ],
                    }
                )
                continue
            out.append(stmt)
        return out


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


def _const_list_concat(
    left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any] | None:
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


def _name(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


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


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}


def _const_none(span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": None, "type": {"kind": "none"}, "span": span}


def _text_ir(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span}


def _len_call(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }


def _tuple_assign(
    dest: str, typ: dict[str, Any], elems: list[dict[str, Any]], span: dict[str, Any]
) -> list[dict[str, Any]]:
    made = {
        "kind": "call",
        "callee": {"kind": "class", "name": typ["name"]},
        "args": [],
        "type": typ,
        "span": span,
    }
    stmts = [_assign(dest, made, span)]
    for index, elem in enumerate(elems):
        stmts.append(
            {
                "kind": "setattr",
                "object": _name(dest, typ, span),
                "name": f"_{index}",
                "value": elem,
                "span": span,
            }
        )
    return stmts


def _is_call_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


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


def _zero(typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    if typ["kind"] == "int":
        return _const_int(0, span)
    if typ["kind"] == "bool":
        return _const_bool(False, span)
    if typ["kind"] == "float":
        return {"kind": "const", "value": "0.0", "type": typ, "span": span}
    return {"kind": "const", "value": "", "type": typ, "span": span}


def _binary_int(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "op": op, "left": left, "right": right, "type": {"kind": "int"}, "span": span}


def _compare(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": {"kind": "bool"}, "span": span}


def _show(typ: dict[str, Any]) -> str:
    if typ["kind"] == "list":
        head = "set" if typ.get("set") else "list"
        return f"{head}[{_show(typ['elem'])}]"
    if typ["kind"] == "dict":
        return f"dict[{_show(typ['key'])}, {_show(typ['value'])}]"
    if typ["kind"] == "class":
        return str(typ.get("label", typ["name"]))
    return str(typ["kind"])


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


def _dotted(node: ast.Attribute) -> str:
    parts: list[str] = [node.attr]
    cur: ast.AST = node.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    test = stmt.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_range_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range"


def _is_setitem(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Subscript)


def _handler_names(lowerer: Lowerer, node: ast.expr) -> list[str]:
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    found: list[str] = []
    for item in names:
        if not isinstance(item, ast.Name):
            lowerer.fail(item, "PS2002", "except names a lowered exception")
        resolved = lowerer.class_aliases.get(item.id, item.id)
        info = lowerer.classes.get(resolved)
        user_exception = info is not None and bool(info.get("exception"))
        if resolved not in EXCEPTIONS and item.id not in EXCEPTIONS and not user_exception:
            lowerer.fail(item, "PS2002", "except names a lowered exception")
        found.append(resolved if user_exception else item.id)
    return found


def _not_bool(name: str, span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "unary",
        "op": "not",
        "operand": _name(name, {"kind": "bool"}, span),
        "type": {"kind": "bool"},
        "span": span,
    }


def _stmt_jumps_this_loop(stmt: ast.stmt, jump: type[ast.stmt]) -> bool:
    if isinstance(stmt, jump):
        return True
    if isinstance(stmt, (ast.While, ast.For, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    nested: list[ast.stmt] = []
    if isinstance(stmt, ast.If):
        nested = [*stmt.body, *stmt.orelse]
    elif isinstance(stmt, ast.Try):
        nested = [*stmt.body, *stmt.orelse, *stmt.finalbody]
        for handler in stmt.handlers:
            nested.extend(handler.body)
    elif isinstance(stmt, ast.With):
        nested = list(stmt.body)
    elif isinstance(stmt, ast.Match):
        nested = [child for case in stmt.cases for child in case.body]
    return any(_stmt_jumps_this_loop(child, jump) for child in nested)


def _stmt_breaks_this_loop(stmt: ast.stmt) -> bool:
    return _stmt_jumps_this_loop(stmt, ast.Break)


def _stmt_continues_this_loop(stmt: ast.stmt) -> bool:
    return _stmt_jumps_this_loop(stmt, ast.Continue)


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


def _always_returns(body: list[dict[str, Any]]) -> bool:
    for stmt in body:
        if stmt["kind"] == "return" or stmt["kind"] == "raise":
            return True
        if stmt["kind"] == "if" and _always_returns(stmt["then"]) and _always_returns(stmt["else"]):
            return True
        if (
            stmt["kind"] == "try"
            and _always_returns(stmt["body"])
            and all(_always_returns(handler["body"]) for handler in stmt["handlers"])
        ):
            return True
    return False


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


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None


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
