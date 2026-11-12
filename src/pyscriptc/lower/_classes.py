"""Class lowering and typing-annotation machinery.

`ClassesMixin` collects `class` statements (dataclasses, enums, synthetic
tuple/deque/OrderedDict shapes), lowers method signatures, and resolves
typing annotations. Moved verbatim from `lower.py` via `lower/__init__.py`;
no signature or behavior change.
"""

from __future__ import annotations

import ast
import copy
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc import coro
from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.errors import span_of
from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._annotations import (
    AnnotationMixin,
    _type_slug,
)
from pyscriptc.lower._class_attrs import ClassAttrsMixin
from pyscriptc.lower._classes_more import ClassesMoreMixin
from pyscriptc.lower._core import (
    Fence,
    _assign,
    _has_typevar,
    _mk_span,
    _mk_type,
    _name,
    _plain_const_kind,
    _show,
    _sig_typevars,
)
from pyscriptc.lower._stmt import _always_returns, _is_docstring
from pyscriptc.reflection import annotation_note, metaclass_note
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.restlower import path_attr
from pyscriptc.restlower._uuid import uuid_hex_attr
from pyscriptc.sigrec import T, type_to_dict
from pyscriptc.stdmore import (
    ALWAYS_CLASSES,
    CLASS_FIELDS,
    DEFERRED_CLASSES,
    RESERVED_CODE_EXCEPTIONS,
    SYNTHETIC_EXCEPTIONS,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


def _const_str_node(value: str, span: dict[str, Any]) -> dict[str, Any]:
    """The const str node one dict-IR const spells."""
    return node_to_dict(_ir.const_node(_ir.P_STR, value, _ir.t_leaf("str"), _mk_span(span)))


def _call_node(
    callee: dict[str, Any],
    args: list[dict[str, Any]],
    typ: dict[str, Any],
    span: dict[str, Any],
) -> dict[str, Any]:
    """The call node one dict-IR call spells."""
    return node_to_dict(
        _ir.call(
            dict_to_node(callee),
            [dict_to_node(arg) for arg in args],
            _mk_type(typ),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _class_call(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The call to a class constructor."""
    return _call_node({"kind": "class", "name": name}, args, typ, span)


def _function_call(fname: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The call to a lowered function."""
    return _call_node({"kind": "function", "name": fname}, args, typ, span)


def _attr_node(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The attribute read node."""
    return node_to_dict(_ir.attr(name, _mk_type(typ), dict_to_node(value), _mk_span(span)))


def _setattr_node(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The attribute write node."""
    return node_to_dict(_ir.setattr_stmt(dict_to_node(obj), name, dict_to_node(value), _mk_span(span)))


def _binary_node(
    op: str, left: dict[str, Any], right: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The binary operation node."""
    return node_to_dict(_ir.binary(op, dict_to_node(left), dict_to_node(right), _mk_type(typ), _mk_span(span)))


def _expr_stmt_node(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The expression statement node."""
    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _type_rt(typ: dict[str, Any]) -> dict[str, Any]:
    """The exact type dict one dict-IR type spells, through the T record."""
    return node_to_dict(dict_to_type(typ))


def _leaf_type(kind: str) -> dict[str, Any]:
    """The leaf type record one dict-IR leaf type spells."""
    return _type_rt(_ir.t_leaf(kind))


def _list_type(elem: dict[str, Any]) -> dict[str, Any]:
    """The list type record one dict-IR list type spells."""
    return _type_rt(_ir.t_list(dict_to_type(elem)))


def _class_type(name: str) -> dict[str, Any]:
    """The class type record one dict-IR class type spells."""
    return _type_rt(_ir.t_class(name))


def _param_row(name: str, typ: dict[str, Any]) -> dict[str, Any]:
    """The `{name, type}` parameter row one lowered parameter spells."""
    row: dict[str, Any] = {}
    row["name"] = name
    row["type"] = _type_rt(typ)
    return row


def _self_param(class_name: str) -> dict[str, Any]:
    """The `self` parameter row typed at its class."""
    return _param_row("self", _class_type(class_name))


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record, read off the typed `T` record."""
    return _mk_type(typ).kind


def _maybe_type_kind(typ: dict[str, Any]) -> str | None:
    """The kind tag of a type record that may carry none."""
    if "kind" not in typ:
        return None
    return _type_kind(typ)


def _node_kind(node: dict[str, Any]) -> str:
    """The kind tag of an IR node record, read off the typed node record."""
    return dict_to_node(node).kind


def _record_kind(record: dict[str, Any]) -> str:
    """The kind tag of a record the typed layer cannot carry.

    A class-base descriptor (`enum`, `exception`, `class`) is not an IR
    node and has no typed record, so its tag stays a plain dict read, in
    one place.
    """
    kind: str = record["kind"]
    return kind


def _base_kind(base: dict[str, Any]) -> str:
    """The kind tag of a class-base descriptor."""
    return _record_kind(base)


_BASE_ENUM = "enum"
_BASE_EXCEPTION = "exception"


def _enum_base(name: str) -> dict[str, str]:
    """The enum base descriptor one `_resolve_base` spells."""
    return {"kind": _BASE_ENUM, "name": name}


def _exception_base(name: str) -> dict[str, str]:
    """The exception base descriptor one `_resolve_base` spells."""
    return {"kind": _BASE_EXCEPTION, "name": name}


def _tuple_default(elems: list[dict[str, Any]], typ: dict[str, Any]) -> dict[str, Any]:
    """The tuple-default marker record a fixed-shape default spells.

    The marker is not an IR node: the call site replays the stored constant
    elements, so the record stays a plain dict.
    """
    row: dict[str, Any] = {}
    row["kind"] = "tuple-default"
    row["elems"] = elems
    row["type"] = typ
    return row


def _bare_leaf(kind: str) -> dict[str, Any]:
    """The bare leaf type record an isinstance target spells.

    The `bare` bit has no typed `T` slot, so the record stays a plain dict.
    """
    row: dict[str, Any] = {}
    row["kind"] = kind
    row["bare"] = True
    return row


def _field_rows(fields: list[tuple[str, T]]) -> list[dict[str, Any]]:
    """The `{name, type}` dict rows of `(name, T)` records, the
    record-to-dict boundary of `CLASS_FIELDS` (the typing-hygiene slice).
    """

    return [{"name": name, "type": type_to_dict(typ)} for name, typ in fields]


# Spellings of the bottom type: a function annotated with one never returns a
# value to its caller (issue #645). Accepted bare or as `typing.` /
# `typing_extensions.` attributes.
# Tier-2 erasure (the tiered-lowering decision; see
# `.agents/docs/design/checker-bridge.md`):
# static-typing constructs that vanish before emission. `abc` is an erased
# module, so these names arrive through `type_aliases` or an erased
# `_resolve_module` triple.
_ERASED_BASES = frozenset({"ABC", "ABCMeta", "Protocol"})
_ERASED_DECORATORS = frozenset({"abstractmethod", "overload"})
# The dynamic attribute protocol is refused by design (issue #432, the
# PS42xx refusal family). A hook that intercepts attribute access or writes
# has nothing to run in: attribute access resolves statically to declared
# fields, so the hook would be silently dropped (`__del__`) or silently
# bypassed (`__setattr__` wrote straight to the field). The refusal names
# the hook instead of letting the divergence through.
#
# `@property` getters are not in this table: they lower, and the getter is
# called at the read site.
#
# D2 (issue #434): `__getattr__` is out of this table for one shape only, a
# plain `def __getattr__(self, name: str) -> T` method: unknown-name READ
# sites then lower as a direct hook call with the name as a compile-time
# constant (`_getattr_hook_call`). Every other hook here still fences at its
# definition, and a hook whose signature fences never becomes dispatchable
# (the refusal stays a refusal).
_REFUSED_HOOKS: dict[str, str] = {
    "__setattr__": "PS4201",
    "__getattribute__": "PS4201",
    "__get__": "PS4201",
    "__set__": "PS4201",
    "__delattr__": "PS4202",
    "__del__": "PS4203",
}


class ClassesMixin(ClassAttrsMixin, ClassesMoreMixin, AnnotationMixin):
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

    def _install_synthetics(self, class_nodes: list[ast.ClassDef], fences: list[Fence]) -> None:
        self.synthetics.update(ALWAYS_CLASSES)
        specs: dict[str, list[dict[str, Any]]] = {}
        if "Pattern" in self.synthetics:
            specs["Pattern"] = [
                {"name": "pattern", "type": _leaf_type("str")},
                {"name": "flags", "type": _leaf_type("int")},
            ]
        if "Match" in self.synthetics:
            # %cls.Match layout mirrors the runtime PsMatch: the subject
            # text plus a flat i64 span list, -1 for groups that did not take part.
            specs["Match"] = [
                {"name": "_text", "type": _leaf_type("str")},
                {"name": "_spans", "type": _list_type(_leaf_type("int"))},
            ]
        if "SplitResult" in self.synthetics:
            specs["SplitResult"] = [
                {"name": name, "type": _leaf_type("str")} for name in ("scheme", "netloc", "path", "query", "fragment")
            ]
        if "HTTPError" in self.synthetics:
            specs["HTTPError"] = _field_rows(CLASS_FIELDS["HTTPError"])
        if "URLError" in self.synthetics:
            specs["URLError"] = _field_rows(CLASS_FIELDS["URLError"])
        for name in sorted(self.synthetics):
            if name in CLASS_FIELDS:
                specs[name] = _field_rows(CLASS_FIELDS[name])
        user_names = {node.name for node in class_nodes}
        for name, fields in specs.items():
            existing = self.classes.get(name)
            if existing is not None and existing.get("synthetic"):
                # The argparse handlers can declare their classes while the
                # entry is split, before this pass runs; the same class twice
                # is a re-install, not a clash.
                continue
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
            record = {
                "fields": fields,
                "own_fields": list(fields),
                "methods": {},
                "method_kind": {},
                "method_owner": {},
                "synthetic": True,
                "deferred": name in DEFERRED_CLASSES,
                "base": None,
            }
            if name in SYNTHETIC_EXCEPTIONS:
                # The runtime registers the class so `except HTTPError`
                # matches through the OSError chain like CPython's.
                record["exception"] = True
                record["exc_code"] = self.exc_n
                self.exc_n += 1
                record["exc_parent"] = SYNTHETIC_EXCEPTIONS[name]
            elif name in RESERVED_CODE_EXCEPTIONS:
                # A reserved-code exception (queue.Empty): the code is fixed
                # at the top of the i32 space, so user codes never shift and
                # the runtime needs no registration.
                record["exception"] = True
                record["exc_code"] = RESERVED_CODE_EXCEPTIONS[name]
                record["exc_parent"] = 7
            self.classes[name] = record

    def _collect_class(self, node: ast.ClassDef) -> None:
        keywords = [kw for kw in node.keywords if not self._is_abcmeta_keyword(kw)]
        if keywords:
            if any(kw.arg == "metaclass" for kw in keywords):
                self.fail(node, "PS1203", metaclass_note())
            self.fail(node, "PS1203", "class keywords are not lowered")
        if node.name in self.bindings or node.name in self.sigs or node.name in self.declared:
            self.fail(node, "PS2002", f"{node.name} is already bound")
        deco = self._class_decorator(node)
        # class C(object) and class C(object,) are the same class as class C.
        # object.__init__ is not emitted. `ABC`/`ABCMeta`/`Protocol` bases
        # erase the same way: the class lowers as a plain class.
        bases: list[dict[str, str]] = []
        type_params: list[str] = []
        protocol = False
        for base_node in node.bases:
            if self._is_builtin_object(base_node):
                continue
            generic_params = self._generic_base(base_node)
            if generic_params is not None:
                # `class C(Generic[T])` erases: the parameter list records
                # nothing at runtime; the declared TypeVars parameterize
                # the class's annotations for call-site instantiation.
                type_params = generic_params
                continue
            erased = self._erased_base_name(base_node)
            if erased is not None:
                protocol = protocol or erased == "Protocol"
                continue
            resolved = self._resolve_base(base_node)
            if _base_kind(resolved) != "class" and len(node.bases) > 1:
                self.fail(node, "PS2002", "multiple inheritance needs lowered class bases")
            bases.append(resolved)
        base = bases[0] if bases else None
        if deco == "dataclass" and base is not None:
            self.fail(node, "PS2002", "a dataclass has no base")
        if base is not None and _base_kind(base) == "enum":
            self._collect_enum(node, str(base["name"]))
            return
        self.declared.add(node.name)
        parent_name = base["name"] if base is not None else None
        parent = self.classes.get(parent_name) if parent_name and base and _base_kind(base) == "class" else None
        inherited = list(parent["fields"]) if parent is not None else []
        inherited_types = {field["name"]: field["type"] for field in inherited}
        # Extra bases contribute the fields the earlier bases do not already
        # name: the primary base's layout stays a literal prefix, which is what
        # shared parent methods GEP against.
        for extra in bases[1:]:
            for field in self.classes[extra["name"]]["fields"]:
                prior = inherited_types.get(field["name"])
                if prior is not None:
                    if not types_equal(prior, field["type"]):
                        self.fail(node, "PS2002", f"base field {field['name']} has conflicting types")
                    continue
                inherited.append(field)
                inherited_types[field["name"]] = field["type"]
        mro, chain = self._mro_chain(node, bases)
        fields: list[dict[str, Any]] = []
        methods: list[tuple[ast.FunctionDef, str]] = []
        seen: set[str] = set()
        seen_default = False
        # Class-level constants: `name = <fold>` and `name: T = <fold>` keep
        # a compile-time value readable as `C.name` and `obj.name`, never an
        # instance field. Ancestor constants merge in MRO order so the
        # nearest definer wins, the same rule instance attribute lookup
        # follows.
        class_constants: dict[str, dict[str, Any]] = {}
        for ancestor in reversed(mro[1:]):
            ancestor_constants = self.classes.get(ancestor, {}).get("constants")
            if ancestor_constants:
                class_constants.update(ancestor_constants)
        # Class-body assignments with a runtime value (`x = f()`, a
        # comprehension, a container of computed elements): the value lowers
        # after the signature pass into the class's initializer, and the
        # attribute reads through a runtime slot keyed `C.x`. The statements
        # are recorded here; `_lower_class_attrs` lowers them.
        runtime_attrs: list[tuple[str, ast.stmt]] = []
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None and deco != "dataclass":
                        const = self._module_fold(stmt.value)
                        if const is None or const.get("kind") != "const":
                            typ = self._annotation(stmt.annotation)
                            if _type_kind(typ) not in {"str", "int", "list", "dict"}:
                                self.fail(stmt, "PS1204", "class fields are declared with an annotation and no value")
                            if stmt.target.id in seen or stmt.target.id in inherited_types:
                                self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                            runtime_attrs.append((stmt.target.id, stmt))
                            seen.add(stmt.target.id)
                            continue
                        typ = self._annotation(stmt.annotation)
                        if typ.get("optional"):
                            if _type_kind(const["type"]) != "none" and not types_equal(const["type"], typ["elem"]):
                                self.fail(stmt, "PS1003", f"{stmt.target.id} default is not {_show(typ)}")
                        else:
                            self._expect(stmt, const["type"], typ, stmt.target.id)
                        class_constants[stmt.target.id] = const
                        continue
                    if stmt.target.id in seen or stmt.target.id in inherited_types:
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                    typ = self._annotation(stmt.annotation)
                    if _type_kind(typ) == "bool":
                        self.fail(stmt, "PS2002", "bool fields are not lowered")
                    if _type_kind(typ) == "func":
                        self.fail(stmt, "PS2002", "callable fields are not lowered")
                    field: dict[str, Any] = {"name": stmt.target.id, "type": typ}
                    if stmt.value is not None:
                        saved_prelude = self.prelude
                        self.prelude = []
                        try:
                            # An Optional field takes `None` or a constant of its
                            # element type, boxed per construction like a def default.
                            # A fixed-shape tuple default stores its constant
                            # elements, not a materialized record: the generated
                            # init replays them per omitted argument.
                            if _maybe_type_kind(typ) == "class" and self.classes.get(typ["name"], {}).get("tuple"):
                                field_rows = self.classes[typ["name"]]["fields"]
                                if not isinstance(stmt.value, ast.Tuple) or len(stmt.value.elts) != len(field_rows):
                                    self.fail(stmt, "PS2002", "a tuple default must match the annotation's shape")
                                elems = []
                                for elt, frow in zip(stmt.value.elts, field_rows, strict=True):
                                    ev = self._expr(elt, frow["type"])
                                    self._expect(elt, ev["type"], frow["type"], stmt.target.id)
                                    if _node_kind(ev) != "const":
                                        self.fail(elt, "PS2002", "a tuple default element must be a constant")
                                    elems.append(ev)
                                default = _tuple_default(elems, typ)
                            else:
                                default = self._expr(stmt.value, None if typ.get("optional") else typ)
                        finally:
                            self.prelude = saved_prelude
                        if _node_kind(default) not in {"const", "tuple-default"}:
                            self.fail(stmt, "PS2002", "a dataclass default must be a constant")
                        if typ.get("optional"):
                            if _type_kind(default["type"]) != "none" and not types_equal(default["type"], typ["elem"]):
                                self.fail(stmt, "PS1003", f"{stmt.target.id} default is not {_show(typ)}")
                            field["optdefault"] = True
                        else:
                            self._expect(stmt, default["type"], typ, stmt.target.id)
                        field["default"] = default
                        seen_default = True
                    elif seen_default:
                        self.fail(stmt, "PS2002", "a non-default field follows a default")
                    seen.add(stmt.target.id)
                    fields.append(field)
                    continue
                if isinstance(stmt, ast.FunctionDef) or (isinstance(stmt, ast.AsyncFunctionDef) and coro.enabled()):
                    decos = {self._erased_deco_name(d) for d in stmt.decorator_list}
                    decos.discard(None)
                    if "overload" in decos:
                        # typing.overload declares the shape; only the final
                        # undecorated implementation lowers.
                        continue
                    if (protocol or "abstractmethod" in decos) and self._declaration_only(stmt):
                        # `def f(self) -> T: ...` on a Protocol or an
                        # abstractmethod declares the method; there is no
                        # body to emit and no runtime check to honor.
                        continue
                    code = _REFUSED_HOOKS.get(stmt.name)
                    if code is not None:
                        self.fail(stmt, code, f"the {stmt.name} hook is refused by design")
                    kind = self._method_decorator(stmt)  # type: ignore[arg-type]
                    if stmt.name == "__init__" and kind != "method":
                        self.fail(stmt, "PS1201", "decorators are not lowered")
                    methods.append((stmt, kind))  # type: ignore[arg-type]
                    continue
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    const = self._module_fold(stmt.value)
                    if const is not None and const.get("kind") == "const":
                        class_constants[stmt.targets[0].id] = const
                        continue
                    if stmt.targets[0].id not in seen and stmt.targets[0].id not in inherited_types:
                        # A runtime class attribute: the value lowers after the
                        # signature pass, and reads go through the slot. The
                        # type is proven when the value lowers.
                        runtime_attrs.append((stmt.targets[0].id, stmt))
                        seen.add(stmt.targets[0].id)
                        continue
                self.fail(stmt, "PS1204", f"{type(stmt).__name__} in a class body is not lowered")
            except Fence as exc:
                self.caught.append(exc)
        method_sigs: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        kinds: dict[str, str] = {}
        owners: dict[str, str] = {}
        # MRO order, first definer wins: a method resolves to the earliest
        # ancestor that defined it, which is the C3 answer for diamonds too.
        for ancestor in mro[1:]:
            ancestor_info = self.classes.get(ancestor)
            if ancestor_info is None:
                continue
            for mname, sig in ancestor_info["methods"].items():
                if ancestor_info["method_owner"].get(mname) != ancestor or mname in method_sigs:
                    continue
                if sig[0] and sig[0][0]["name"] == "self":
                    sig = ([_self_param(node.name), *sig[0][1:]], sig[1])
                method_sigs[mname] = sig
                kinds[mname] = ancestor_info["method_kind"].get(mname, "method")
                owners[mname] = ancestor
        wrote_init = False
        # A field fence leaves the field set unknown; a method signature fence
        # only drops that one method. Count each kind so the catalog pass can
        # tell them apart.
        body_fences = len(self.caught)
        sig_fences = 0
        for method, kind in methods:
            try:
                method_sigs[method.name] = self._coro_method_signature(method, node.name) or self._signature(
                    method, node.name, kind
                )
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
        if wrote_init and deco != "dataclass":
            # A written `__init__` is the field declaration the corpus
            # spells: `self.x = <param>` takes the parameter's annotated
            # type, `self.x: T = v` takes the annotation. Fields the class
            # body already named keep their declared type.
            init_fn = next((method for method, _kind in methods if method.name == "__init__"), None)
            if init_fn is not None:
                fields.extend(
                    self._init_fields(
                        init_fn,
                        method_sigs.get("__init__"),
                        {field["name"] for field in fields} | set(inherited_types),
                    )
                )
        generated: str | None = None
        init_target: str | None = None
        if deco == "dataclass":
            if wrote_init:
                self.fail(node, "PS2002", "a dataclass does not take a written __init__")
            params = [_self_param(node.name)]
            for field in fields:
                param: dict[str, Any] = {"name": field["name"], "type": field["type"]}
                if "default" in field:
                    param["default"] = field["default"]
                    if field.get("optdefault"):
                        param["optdefault"] = True
                params.append(param)
            method_sigs["__init__"] = (params, _leaf_type("none"))
            kinds["__init__"] = "method"
            owners["__init__"] = node.name
            generated = "dataclass"
        elif not wrote_init and "__init__" in method_sigs:
            if fields:
                self.fail(node, "PS1001", f"{node.name} needs __init__")
            init_owner = owners["__init__"]
            if init_owner in chain:
                # The primary chain shares the emitted init: cls.{C}.__init__
                # is a trampoline to the first class in the MRO that emits it.
                init_target = init_owner
                for name in mro[1:]:
                    xinfo = self.classes.get(name)
                    if xinfo is None:
                        continue
                    if "__init__" in xinfo["methods"] and (
                        name == init_owner or init_owner not in xinfo.get("chain", {name})
                    ):
                        init_target = name
                        break
                method_sigs["__init__"] = (
                    [_self_param(node.name), *method_sigs["__init__"][0][1:]],
                    _leaf_type("none"),
                )
                kinds["__init__"] = "method"
                owners["__init__"] = node.name
                generated = "forward"
            # Otherwise the secondary base's init is monomorphized at emit
            # time: the definer's FunctionDef is re-lowered as cls.{C}.__init__
            # with self typed C, since C's layout is not a prefix extension of
            # the secondary base's.
        elif parent is not None and fields and not wrote_init:
            self.fail(node, "PS1001", f"{node.name} needs __init__")
        if deco == "dataclass":
            # A field default is also the class attribute CPython leaves
            # behind, so `C.x` reads it like any other class constant.
            for field in fields:
                default = field.get("default")
                if default is not None and default.get("kind") == "const":
                    class_constants[field["name"]] = default
        exception = bool(base and (_base_kind(base) == "exception" or (parent and parent.get("exception"))))
        info: dict[str, Any] = {
            "fields": [*inherited, *fields],
            "own_fields": fields,
            "constants": class_constants,
            "methods": method_sigs,
            "method_kind": kinds,
            "method_owner": owners,
            "base": parent_name,
            "mro": mro,
            "chain": chain,
            "node": node,
        }
        if generated is not None:
            info["generated_init"] = generated
            info["init_target"] = init_target
        if exception:
            info["exception"] = True
            info["exc_code"] = self.exc_n
            self.exc_n += 1
            # The parent code lets the runtime walk the same parent chain the
            # builtins use, so a user class matches `except Exception` and
            # `except <its builtin base>` without a precomputed code list.
            info["exc_parent"] = self._exception_parent_code(base, parent_name)
        if type_params:
            info["typevars"] = type_params
        if protocol:
            info["protocol"] = True
        if runtime_attrs:
            # The class-body assignments whose value the compiler computes.
            # They lower after the signature pass (the value may call a
            # module function), into the class's initializer.
            info["runtime_attrs"] = runtime_attrs
        self.classes[node.name] = info

    def _init_fields(
        self,
        fn: ast.FunctionDef,
        sig: tuple[list[dict[str, Any]], dict[str, Any]] | None,
        taken: set[str],
    ) -> list[dict[str, Any]]:
        """Fields a written `__init__` proves by `self.<name>` targets.

        `self.x = <param>` takes the parameter's annotated type;
        `self.x: T = v` takes the annotation (the value's own check runs
        in the body pass). Names the class body or a base already
        declared are skipped: the declared type wins and a mismatch
        fences at the write.
        """
        found: list[dict[str, Any]] = []
        seen = set(taken)
        if sig is None:
            return found
        params = sig[0]
        arg_types: dict[str, dict[str, Any]] = {}
        plain = list(fn.args.posonlyargs) + list(fn.args.args)
        for index, arg in enumerate(plain):
            if index < len(params):
                arg_types[arg.arg] = params[index]["type"]
        for stmt in ast.walk(fn):
            target: ast.expr | None = None
            typ: dict[str, Any] | None = None
            if isinstance(stmt, ast.AnnAssign) and stmt.value is not None and isinstance(stmt.target, ast.Attribute):
                target = stmt.target
                try:
                    typ = self._annotation(stmt.annotation)
                except Fence:
                    continue
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Attribute)
                and isinstance(stmt.value, ast.Name)
            ):
                target = stmt.targets[0]
                typ = arg_types.get(stmt.value.id)
                if typ is None:
                    continue
            else:
                continue
            if not isinstance(target, ast.Attribute):
                continue
            if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                continue
            name = target.attr
            if name in seen:
                continue
            seen.add(name)
            found.append({"name": name, "type": copy.deepcopy(typ)})
        return found

    def _mro_chain(self, node: ast.ClassDef, bases: list[dict[str, str]]) -> tuple[list[str], set[str]]:
        """C3 linearization of the bases, plus the primary-base spine.

        `mro` is method-resolution order: the first class in it that defined a
        method owns it. `chain` is the base[0] spine — a class on it sees its
        ancestors' fields as a literal layout prefix, so their methods are
        shared; anything off it is monomorphized per class at emit time.
        """
        base_names = [base["name"] for base in bases]
        seqs = [list(self.classes.get(name, {}).get("mro", [name])) for name in base_names]
        seqs.append(list(base_names))
        mro = [node.name]
        while True:
            head = next((seq[0] for seq in seqs if seq and all(seq[0] not in other[1:] for other in seqs)), None)
            if head is None:
                if any(seqs):
                    self.fail(node, "PS2002", "an inconsistent method resolution order")
                break
            mro.append(head)
            for seq in seqs:
                if seq and seq[0] == head:
                    seq.pop(0)
        chain = {node.name}
        if base_names:
            chain |= self.classes.get(base_names[0], {}).get("chain", {base_names[0]})
        return mro, chain

    def _emit_owner(self, class_name: str, method: str) -> str:
        """The class that emitted `cls.{?}.{method}` for a `class_name` receiver.

        Walks the MRO: the owner itself emits the method, and every class
        whose primary chain does not contain the owner emits its own
        monomorphized copy. The first MRO class qualifying wins.
        """
        info = self.classes[class_name]
        owner = info.get("method_owner", {}).get(method, class_name)
        for name in info.get("mro", [class_name]):
            xinfo = self.classes.get(name)
            if xinfo is None or method not in xinfo["methods"]:
                continue
            if name == owner or owner not in xinfo.get("chain", {name}):
                return name
        return owner

    def _dunder_call(
        self,
        node: ast.AST,
        receiver: dict[str, Any],
        name: str,
        args: list[dict[str, Any]],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`receiver.__x__(*args)` as a direct class-method call, or None.

        None means the class never defined the dunder as a plain method; the
        caller keeps its existing fence. Signature checks stay here so every
        dispatch site reports the same arity/type failures.
        """
        info = self.classes.get(receiver["type"]["name"])
        if info is None or info["methods"].get(name) is None:
            return None
        if info.get("method_kind", {}).get(name, "method") != "method":
            return None
        class_name = receiver["type"]["name"]
        params, ret = info["methods"][name]
        if len(params) - 1 != len(args):
            self.fail(node, "PS2003", f"{class_name}.{name} takes {len(params) - 1} argument(s)")
        for arg, param in zip(args, params[1:], strict=True):
            self._expect(node, arg["type"], param["type"], f"{class_name}.{name} argument")
        want = {
            "__str__": "str",
            "__repr__": "str",
            "__len__": "int",
            "__eq__": "bool",
            "__exit__": "bool",
        }.get(name)
        if want is not None and _type_kind(ret) != want:
            self.fail(node, "PS1003", f"{class_name}.{name} returns {_show(ret)}, not {want}")
        emit = self._emit_owner(class_name, name)
        return _function_call(f"cls.{emit}.{name}", [receiver, *args], ret, span)

    def _lower_class(self, node: ast.ClassDef, emit_name: str | None = None) -> dict[str, Any] | None:
        own = emit_name or node.name
        info = self.classes[own]
        if info.get("enum"):
            return None
        own_names = {field["name"] for field in info.get("own_fields", info["fields"])}
        methods: list[dict[str, Any]] = []
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if info["method_owner"].get(stmt.name) != own:
                continue
            emitted = self._emit_class_method(own, stmt, info, own_names)
            if emitted is not None:
                methods.append(emitted)
        for mname in info["methods"]:
            # Monomorphize inherited instance methods whose owner sits off the
            # primary chain: the owner's fields are not a layout prefix of this
            # class, so its body is re-lowered with self typed as this class.
            owner = info["method_owner"].get(mname, own)
            if owner == own or owner in info.get("chain", {own}):
                continue
            if info["method_kind"].get(mname, "method") not in {"method", "property"}:
                continue
            ostmt = next(
                (
                    stmt
                    for stmt in self.classes[owner].get("node", node).body
                    if isinstance(stmt, ast.FunctionDef) and stmt.name == mname
                ),
                None,
            )
            if ostmt is None:
                continue
            emitted = self._emit_class_method(own, ostmt, info, own_names)
            if emitted is not None:
                methods.append(emitted)
        if self.caught:
            return None
        generated = self._generated_method(node, info)
        if generated is not None:
            methods.append(generated)
        record = {
            "name": own,
            "fields": info["fields"],
            "methods": methods,
            "span": span_of(node, self.filename),
        }
        if info.get("exception"):
            # The runtime registers the class so `except` matches it through
            # the same parent chain the builtins use.
            record["exception"] = True
            record["exc_code"] = info["exc_code"]
            record["exc_parent"] = info.get("exc_parent", EXCEPTIONS["Exception"])
        return record

    def _emit_class_method(
        self, class_name: str, stmt: ast.FunctionDef, info: dict[str, Any], own_names: set[str]
    ) -> dict[str, Any] | None:
        """Lower one method body for `cls.{class_name}.{stmt.name}`.

        The definer's FunctionDef is reused verbatim; `self` and `super()`
        resolve against `class_name`'s layout while `method_owner` still names
        the defining class (CPython's __class__ cell for super()).
        """
        self.function = f"{class_name}.{stmt.name}"
        self.temp_n = 0
        self.prelude = []
        # boxed:begin
        self.island_modules = {}
        self.island_class_handles = {}
        # boxed:end
        self.fn_globals = self._globals_for(stmt.body)
        params, ret = info["methods"][stmt.name]
        kind = info["method_kind"].get(stmt.name, "method")
        if params and params[0]["name"] == "self":
            params = [_self_param(class_name), *params[1:]]
        # A generator method's body lowers under the None-returning resume
        # signature; the outward frame type rides in the method signature.
        params, ret = self._coro_method_body_sig(class_name, stmt.name, params, ret)
        self.locals = {param["name"]: param["type"] for param in params}
        self.frozen_names = set()
        self.fn_body = list(stmt.body)
        if kind in {"method", "property"}:
            self.locals["self"] = _class_type(class_name)
        # Function-local imports rebind module names: snapshot the
        # compile-time binding tables so they restore when the body ends.
        saved_bindings = dict(self.bindings)
        saved_aliases = dict(self.type_aliases)
        saved_declared = set(self.declared)
        saved_typevars = dict(self.typevars)
        try:
            body = self._body(list(stmt.body))
        finally:
            self.bindings = saved_bindings
            self.type_aliases = saved_aliases
            self.declared = saved_declared
            self.typevars = saved_typevars
        if self.caught:
            return None
        if stmt.name == "__init__":
            assigned = _assigned_fields(stmt.body)
            missing = sorted(own_names - assigned)
            if missing:
                self.fail(stmt, "PS1001", f"__init__ does not assign {', '.join(missing)}")
        if _type_kind(ret) != "none" and not _always_returns(body):
            self.fail(stmt, "PS1004", f"{stmt.name} can fall off the end and return None")
        return self._method_ir(stmt.name, params, ret, body, span_of(stmt, self.filename))

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
        self_type = _class_type(node.name)
        if mode == "dataclass":
            body = [
                _setattr_node(
                    _name("self", self_type, span), field["name"], _name(field["name"], field["type"], span), span
                )
                for field in info["own_fields"]
            ]
        else:
            parent = info.get("init_target") or info["base"]
            args = [_name(param["name"], param["type"], span) for param in params]
            body = [_expr_stmt_node(_function_call(f"cls.{parent}.__init__", args, _leaf_type("none"), span), span)]
        self.locals = {param["name"]: param["type"] for param in params}
        return self._method_ir("__init__", params, ret, body, span)

    def _param_default(self, arg: ast.arg, default_node: ast.expr, param: dict[str, Any]) -> None:
        if param["type"].get("optional"):
            value = self._expr(default_node)
            if _node_kind(value) != "const":
                self.fail(default_node, "PS2002", "a default must be a constant")
            if _type_kind(value["type"]) != "none" and not types_equal(value["type"], param["type"]["elem"]):
                self.fail(default_node, "PS1003", f"{arg.arg} is {_show(value['type'])}, not {_show(param['type'])}")
            param["default"] = value
            param["optdefault"] = True
        elif _maybe_type_kind(param["type"]) == "class" and self.classes.get(param["type"]["name"], {}).get("tuple"):
            # A fixed-shape tuple default: the elements must each be
            # constants, and the shape must match the annotation. The
            # elements are stored, not a materialized record: every call
            # site that omits the argument materializes its own copy
            # (CPython shares one tuple; ours replays — value-identical
            # for the immutable shapes the subset allows).
            fields = self.classes[param["type"]["name"]]["fields"]
            if not isinstance(default_node, ast.Tuple) or len(default_node.elts) != len(fields):
                self.fail(default_node, "PS2002", "a tuple default must match the annotation's shape")
            elems = []
            for elt, field in zip(default_node.elts, fields, strict=True):
                value = self._expr(elt, field["type"])
                self._expect(elt, value["type"], field["type"], arg.arg)
                if _node_kind(value) != "const":
                    self.fail(elt, "PS2002", "a tuple default element must be a constant")
                elems.append(value)
            param["default"] = _tuple_default(elems, param["type"])
        else:
            value = self._expr(default_node, param["type"])
            self._expect(default_node, value["type"], param["type"], arg.arg)
            if _node_kind(value) != "const":
                self.fail(default_node, "PS2002", "a default must be a constant")
            param["default"] = value

    def _construct(self, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
        info = self.classes[name]
        if info.get("namedtuple"):
            return self._construct_namedtuple(node, span, name, info)
        init = info["methods"].get("__init__")
        if info.get("typevars"):
            return self._construct_generic(node, span, name, init)
        if init is None:
            if node.args or node.keywords:
                self.fail(node, "PS2003", f"{name} takes no arguments")
            return _class_call(name, [], _class_type(name), span)
        params = [param for param in init[0] if param["name"] != "self"]
        args = self._fill_args(node, params, name)
        return _class_call(name, args, _class_type(name), span)

    def _construct_generic(
        self, node: ast.Call, span: dict[str, Any], name: str, init: tuple[Any, Any] | None
    ) -> dict[str, Any]:
        """`Box(3)` on a `class Box(Generic[T])`: unify the arguments with the
        __init__ parameters to bind its TypeVars, then emit the call against
        the specialized `Box_int`-style class record."""
        if init is None:
            self.fail(node, "PS1003", f"cannot infer type arguments for {name}")
        params = [param for param in init[0] if param["name"] != "self"]
        mapping: dict[str, dict[str, Any]] = {}
        args = self._fill_generic_args(node, params, mapping, name)
        tvars = self.classes[name].get("typevars") or []
        for param in params:
            for tv in _sig_typevars([param["type"]]):
                if tv not in mapping:
                    bound = self.typevars.get(tv)
                    if bound is None:
                        self.fail(node, "PS1003", f"cannot infer {tv} for {name}")
                    mapping[tv] = bound
        missing = [tv for tv in tvars if tv not in mapping]
        if missing:
            self.fail(node, "PS1003", f"cannot infer {missing[0]} for {name}")
        inst = self._instantiate_class(name, [mapping[tv] for tv in tvars], node)
        for arg, param in zip(args, params, strict=True):
            want = self._subst(param["type"], mapping, node)
            if not _has_typevar(want):
                self._expect(node, arg["type"], want, name)
        return _class_call(inst, args, _class_type(inst), span)

    def _namedtuple_call(self, node: ast.expr) -> ast.Call | None:
        """The `collections.namedtuple(...)` call, spelled either binding or `collections.namedtuple`."""
        if not isinstance(node, ast.Call):
            return None
        if isinstance(node.func, ast.Name):
            binding = self.bindings.get(node.func.id)
            if binding is not None and binding.module == "collections" and binding.attr == "namedtuple":
                return node
            return None
        if isinstance(node.func, ast.Attribute):
            resolved = self._resolve_module(node.func)
            if resolved is not None and resolved[1] == "collections" and resolved[2] == "namedtuple":
                return node
        return None

    def _namedtuple_decl(self, target: str, call: ast.Call, stmt: ast.stmt) -> None:
        """`P = namedtuple('P', 'x y')` declares the synthetic tuple class `P`.

        Subset: the typename is a str constant, the field names come from a
        str constant split on commas and whitespace or a list/tuple of str
        constants, and `fields=` may name the second argument. `rename`,
        `defaults`, and `module` stay fenced. Field types are fixed by the
        first `P(...)` construction.
        """
        fields_node = call.args[1] if len(call.args) == 2 else None
        for kw in call.keywords:
            if kw.arg == "fields" and fields_node is None:
                fields_node = kw.value
            else:
                self.fail(kw.value, "PS2003", f"namedtuple keyword {kw.arg} is not lowered")
        if len(call.args) not in {1, 2} or fields_node is None:
            self.fail(call, "PS2003", "namedtuple expects a typename and field names")
        type_node = call.args[0]
        if _plain_const_kind(type_node) != "str" or not type_node.value.isidentifier():
            self.fail(call, "PS2003", "namedtuple needs a str typename that is an identifier")
        names = _nt_fields(fields_node)
        if names is None:
            self.fail(call, "PS2003", "namedtuple field names are str constants")
        if len(set(names)) != len(names):
            self.fail(call, "PS2003", "namedtuple field names repeat")
        for field in names:
            if not field.isidentifier() or field.startswith("_"):
                self.fail(call, "PS2003", f"namedtuple field {field} is not an allowed name")
        if target in self.declared or target in self.bindings or target in self.locals:
            self.fail(stmt, "PS2002", f"{target} is already bound")
        typename = type_node.value
        existing = self.classes.get(typename)
        if existing is not None:
            if not existing.get("namedtuple") or existing["nt_fields"] != names:
                self.fail(stmt, "PS2002", f"{typename} is already bound")
        elif typename in self.declared:
            self.fail(stmt, "PS2002", f"{typename} is already bound")
        else:
            self.classes[typename] = {
                "fields": [],
                "methods": {},
                "synthetic": True,
                "tuple": True,
                "namedtuple": True,
                "nt_fields": names,
            }
            self.declared.add(typename)
        self.class_aliases[target] = typename
        self.declared.add(target)

    def _construct_namedtuple(
        self, node: ast.Call, span: dict[str, Any], name: str, info: dict[str, Any]
    ) -> dict[str, Any]:
        """`P(1, 2)` or `P(x=1, y=2)`: materialize with per-field setattrs.

        Positional arguments fill fields in order; keywords must name fields
        not already filled. The first construction fixes the field types;
        later ones check against them, like a class without an `__init__`.
        """
        fields = info["nt_fields"]
        node.args = self._expand_starred(node)
        if len(node.args) > len(fields):
            self.fail(node, "PS2003", f"{name}() expects {len(fields)} argument(s)")
        values: dict[str, dict[str, Any]] = {}
        for field, arg in zip(fields, node.args, strict=False):
            values[field] = self._expr(arg)
        for kw in node.keywords:
            if kw.arg is None or kw.arg not in fields:
                self.fail(node, "PS2003", f"{name}() keyword arguments name its fields")
            if kw.arg in values:
                self.fail(node, "PS2003", f"{name}() field {kw.arg} is given twice")
            values[kw.arg] = self._expr(kw.value)
        missing = [field for field in fields if field not in values]
        if missing:
            self.fail(node, "PS2003", f"{name}() is missing field {missing[0]}")
        args = [values[field] for field in fields]
        if not info["fields"]:
            built: list[dict[str, Any]] = []
            for field, arg in zip(fields, args, strict=True):
                if _type_kind(arg["type"]) in {"func", "none"}:
                    self.fail(node, "PS2002", f"{name}() field {field} is {_show(arg['type'])}")
                built.append({"name": field, "type": arg["type"]})
            info["fields"].extend(built)
        else:
            if len(info["fields"]) != len(args):
                self.fail(node, "PS2003", f"{name}() expects {len(info['fields'])} argument(s)")
            for field, arg in zip(info["fields"], args, strict=True):
                self._expect(node, arg["type"], field["type"], f"{name}.{field['name']}")
        typ = _class_type(name)
        dest = self._temp("nt").replace(".", "_")
        self.locals[dest] = typ
        made = _class_call(name, [], typ, span)
        self.prelude.append(_assign(dest, made, span))
        for field, arg in zip(fields, args, strict=True):
            self.prelude.append(_setattr_node(_name(dest, typ, span), field, arg, span))
        return _name(dest, typ, span)

    def _typevar_call(self, node: ast.expr) -> ast.Call | None:
        """The `TypeVar('T', ...)` call, spelled through a `from typing import
        TypeVar` local or an erased `typing.TypeVar` attribute."""
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        if isinstance(func, ast.Name):
            if self.type_aliases.get(func.id) == "TypeVar":
                return node
            return None
        if isinstance(func, ast.Attribute):
            resolved = self._resolve_module(func)
            if resolved is not None and resolved[3] == "erased" and resolved[2] == "TypeVar":
                return node
        return None

    def _declare_typevar(self, target: str, call: ast.Call, stmt: ast.AST) -> None:
        """`T = TypeVar('T')` registers `T` as a TypeVar and lowers as a no-op.

        `bound=` records a bound type its annotations erase to directly;
        variance flags are ignored; an unbound TypeVar annotates as a
        `{"kind": "typevar"}` placeholder that only call-site
        monomorphization resolves."""
        if target in self.bindings or target in self.declared or target in self.constants or target in self.typevars:
            self.fail(stmt, "PS2002", f"{target} is already bound")
        if not call.args or _plain_const_kind(call.args[0]) != "str":
            self.fail(call, "PS2003", "TypeVar needs a str name")
        bound: dict[str, Any] | None = None
        for kw in call.keywords:
            if kw.arg == "bound":
                typ = self._annotation(kw.value)
                bound = typ if _type_kind(typ) != "typevar" else None
            elif kw.arg in {"covariant", "contravariant", "infer_variance"}:
                continue
            else:
                self.fail(kw.value, "PS2003", f"TypeVar keyword {kw.arg} is not lowered")
        self.typevars[target] = bound
        self.declared.add(target)

    def _is_generic_name(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return self.type_aliases.get(node.id) == "Generic"
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            return resolved is not None and resolved[3] == "erased" and resolved[2] == "Generic"
        return False

    def _generic_base(self, node: ast.expr) -> list[str] | None:
        """A `Generic[T, ...]` (or bare `Generic`) base: the declared TypeVar
        names it parameterizes over, or None when this is not a Generic base."""
        if isinstance(node, ast.Subscript):
            if not self._is_generic_name(node.value):
                return None
            elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return [elt.id for elt in elts if isinstance(elt, ast.Name) and elt.id in self.typevars]
        if self._is_generic_name(node):
            return []
        return None

    def _instantiate_class(self, base: str, arg_types: list[dict[str, Any]], node: ast.AST) -> str:
        """Register (or reuse) `base` specialized at `arg_types` and return the
        emitted class name; the specialized record is queued to lower."""
        info = self.classes[base]
        tvars = info.get("typevars") or []
        if len(arg_types) != len(tvars):
            self.fail(node, "PS1003", f"{base} takes {len(tvars)} type argument(s)")
        mapping: dict[str, dict[str, Any]] = {}
        for tv, typ in zip(tvars, arg_types, strict=True):
            if _type_kind(typ) == "none":
                self.fail(node, "PS1003", f"cannot instantiate {base} with none")
            bound = self.typevars.get(tv)
            if bound is not None and not types_equal(typ, bound):
                self.fail(node, "PS1003", f"{_show(typ)} is outside the bound {_show(bound)} of {tv}")
            mapping[tv] = typ
        key = (base, tuple(type_key(typ) for typ in arg_types))
        name = self.class_insts.get(key)
        if name is not None:
            return name
        name = f"{base}_{'_'.join(_type_slug(typ) for typ in arg_types)}"
        if name in self.classes or name in self.declared:
            self.fail(node, "PS2002", f"{name} is already bound")
        inst = copy.deepcopy(info)
        inst["fields"] = [{**field, "type": self._subst(field["type"], mapping, node)} for field in inst["fields"]]
        inst["own_fields"] = [
            {**field, "type": self._subst(field["type"], mapping, node)} for field in inst.get("own_fields", [])
        ]
        for field in inst["fields"] + inst["own_fields"]:
            default = field.get("default")
            if default is not None:
                default["type"] = self._subst(default["type"], mapping, node)
        inst["methods"] = {
            mname: (
                [{**param, "type": self._subst(param["type"], mapping, node)} for param in params],
                self._subst(ret, mapping, node),
            )
            for mname, (params, ret) in inst["methods"].items()
        }
        inst["method_owner"] = {
            mname: (name if owner == base else owner) for mname, owner in inst["method_owner"].items()
        }
        inst.pop("typevars", None)
        inst["instance_of"] = base
        inst["mro"] = [name, base]
        inst["chain"] = {name}
        self.classes[name] = inst
        self.declared.add(name)
        self.known_classes.add(name)
        self.class_insts[key] = name
        base_node = info.get("node")
        if base_node is not None:
            self.class_queue.append((base_node, name, mapping))
        return name

    def _class_binary(
        self, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any]:
        op = type(node.op)
        left_name = left["type"].get("name")
        right_name = right["type"].get("name")
        if left["type"].get("handle") or right["type"].get("handle"):
            # An opaque handle is a bare pointer at the ABI: arithmetic on it
            # would be pointer arithmetic on a foreign object (issue #224).
            self.fail(
                node,
                "PS4016",
                f"{op.__name__} on the ffi handle {left_name if left['type'].get('handle') else right_name} "
                "is not lowered; a handle is an opaque token",
            )
        if op in {ast.Add, ast.Sub}:
            sign = "+" if op is ast.Add else "-"
            if left_name in {"Date", "DateTime"} and right_name == "TimeDelta":
                return _binary_node(sign, left, right, left["type"], span)
            if left_name == "TimeDelta" and right_name == "TimeDelta":
                return _binary_node(sign, left, right, left["type"], span)
            if op is ast.Sub and left_name == right_name and left_name in {"Date", "DateTime"}:
                return _binary_node("-", left, right, _class_type("TimeDelta"), span)
        if op is ast.Div:
            # `p / x` is pathlib's __truediv__/__rtruediv__: joinpath each way.
            if left_name == "Path" and _type_kind(right["type"]) == "str":
                return self._abi_call(
                    "pathlib", "Path.__truediv__", [left, right], left["type"], "ps_path_joinpath", span
                )
            if left_name == "Path" and right_name == "Path":
                return self._abi_call(
                    "pathlib", "Path.__truediv__", [left, right], left["type"], "ps_path_joinpath_p", span
                )
            if _type_kind(left["type"]) == "str" and right_name == "Path":
                return self._abi_call(
                    "pathlib", "Path.__rtruediv__", [left, right], right["type"], "ps_path_rjoin", span
                )
        self.fail(node, "PS2004", f"{op.__name__} on {left_name} and {right_name} is not lowered")
        raise AssertionError("unreachable")

    def _lookup_attr(
        self,
        obj: dict[str, Any],
        name: str,
        span: dict[str, Any],
        node: ast.AST,
        expect: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if _type_kind(obj["type"]) == "exc":
            if name == "args":
                return self._abi_call("builtins", "exc.args", [obj], _list_type(_leaf_type("str")), "ps_exc_args", span)
            self.fail(node, "PS2001", f"exception has no attribute {name}")
        if obj.get("symbol") == "ps_exc_class" and obj.get("attrtype") == "type":
            # type(e): the carrier holds the runtime class name. `.__name__`
            # is that name; any other attribute is not in the subset.
            if name == "__name__":
                return obj
            self.fail(node, "PS2001", f"type(e) has no attribute {name}")
        if _type_kind(obj["type"]) != "class":
            # A lowered uuid call keeps the `.hex` attribute CPython's UUID
            # holds: the canonical text without the dashes.
            hexed = uuid_hex_attr(self, obj, name, span)
            if hexed is not None:
                return hexed
            note = annotation_note(name)
            if note is not None:
                self.fail(node, "PS2001", note)
            self.fail(node, "PS2001", f"attribute access is not lowered ({name})")
        class_name = obj["type"]["name"]
        # boxed:begin
        if obj["type"].get("island"):
            return self._island_getattr(obj, name, span, node, expect)
        # boxed:end
        if obj["type"].get("handle"):
            # An opaque handle has no fields: it is a bare pointer at the
            # ABI, and reading a field would be a wild access (issue #224).
            self.fail(node, "PS4015", f"{class_name} is an opaque ffi handle and has no attributes")
        if class_name == "Semaphore" and name == "_value":
            return self._abi_call("threading", "Semaphore._value", [obj], _leaf_type("int"), "ps_sem_value", span)
        if class_name == "AsyncSemaphore" and name == "_value":
            # The asyncio semaphore's counter, the same shape the threading
            # one has: an opaque pointer with a read-only count.
            return self._abi_call("asyncio", "Semaphore._value", [obj], _leaf_type("int"), "ps_asem_value", span)
        if class_name == "Thread" and name == "name":
            return self._abi_call("threading", "Thread.name", [obj], _leaf_type("str"), "ps_thread_name2", span)
        if class_name == "Thread" and name == "daemon":
            return self._abi_call("threading", "Thread.daemon", [obj], _leaf_type("bool"), "ps_thread_daemon", span)
        info = self.classes.get(class_name)
        if info is None:
            self.fail(node, "PS2001", f"{class_name} has no fields")
        for field in info["fields"]:
            if field["name"] == name:
                return _attr_node(obj, name, field["type"], span)
        if obj["type"].get("optional") and _maybe_type_kind(obj["type"].get("elem", {})) == "class":
            # `x.f` on a `T | None` cell reads through `_val` once the tag
            # is proven set; an unset tag raises AttributeError.
            return self._lookup_attr(self._opt_payload(obj, name, span), name, span, node, expect)
        # A class-level constant reads through the instance when no instance
        # field shadows it.
        const = self._class_const(class_name, name)
        if const is not None:
            return {**const, "span": span}
        # A runtime class attribute reads through the instance too: the value
        # lives in the class's slot, not in the instance's field table.
        runtime = self._class_runtime_attr(class_name, name)
        if runtime is not None:
            owner, attr_type = runtime
            return self._class_attr_read(owner, name, attr_type, span)
        if info.get("method_kind", {}).get(name) == "property":
            sig = info["methods"][name]
            owner = self._emit_owner(class_name, name)
            return _function_call(f"cls.{owner}.{name}", [obj], sig[1], span)
        hook = self._getattr_hook(class_name)
        if hook is not None:
            return self._getattr_hook_call(hook, obj, name, span, node, expect)
        attr = path_attr(name) if class_name == "Path" else None
        if attr is not None:
            typ, symbol = attr
            return self._abi_call("pathlib", name, [obj], typ, symbol, span)
        if synth_method(class_name, name) is not None:
            self.fail(node, "PS2002", f"{class_name}.{name} is a function and is not a value")
        note = annotation_note(name)
        if note is not None:
            self.fail(node, "PS2001", note)
        self.fail(node, "PS2001", f"{class_name}.{name} is not a field")
        raise AssertionError("unreachable")

    def _class_const(self, class_name: str, attr: str) -> dict[str, Any] | None:
        """The class-level constant `attr` of `class_name`, or None.

        A method defined on the class itself wins over a same-named
        constant, matching the class dictionary's own-name precedence;
        ancestors' methods do not shadow it.
        """
        info = self.classes.get(class_name)
        if info is None:
            return None
        const = (info.get("constants") or {}).get(attr)
        if const is None or info.get("method_owner", {}).get(attr) == class_name:
            return None
        return const

    def _getattr_hook(self, class_name: str) -> tuple[str, dict[str, Any]] | None:
        """The first `__getattr__` definer in the MRO, as (owner, signature).

        None means no lowered class in the MRO defines the hook, or the
        definition is not the one supported shape: a plain instance method
        taking exactly `self` and one `str`. A `@property`, `@staticmethod` or
        `@classmethod` spelling, and a generator (`def ... -> Generator`) body,
        stay refused: the signature never becomes dispatchable, so the read
        site keeps its ordinary fence and D1's refusal is not quietly lifted
        for shapes this lowering cannot call (issue #434). A definition whose
        signature fenced never lands in `method_owner` either, so a partial
        refusal still refuses.
        """
        info = self.classes.get(class_name)
        if info is None:
            return None
        for ancestor in info.get("mro", [class_name]):
            ancestor_info = self.classes.get(ancestor)
            if ancestor_info is None:
                continue
            if ancestor_info.get("method_owner", {}).get("__getattr__") != ancestor:
                continue
            if ancestor_info.get("method_kind", {}).get("__getattr__", "method") != "method":
                return None
            params, _ = ancestor_info["methods"]["__getattr__"]
            if len(params) != 2 or _maybe_type_kind(params[1].get("type", {})) != "str":
                return None
            return ancestor, ancestor_info["methods"]["__getattr__"]
        return None

    def _getattr_hook_call(
        self,
        hook: tuple[str, Any],
        obj: dict[str, Any],
        name: str,
        span: dict[str, Any],
        node: ast.AST,
        expect: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """An unknown-name READ on a class with `__getattr__`: a direct call.

        The name rides as a compile-time string constant, so the hook is a
        plain two-argument call and the class layout never grows a name cell.
        The hook's declared return type must cover the read site: a hook that
        claims `int` cannot feed a `str` read, and that is a named fence, not
        a silent wrong-type load (issue #434).
        """
        owner, (params, ret) = hook
        if ret.get("optional"):
            self.fail(
                node,
                "PS4204",
                f"{owner}.__getattr__ returns {_show(ret)}; the return type must cover the read site exactly",
            )
            raise AssertionError("unreachable")
        if _type_kind(ret) in {"none"}:
            self.fail(
                node,
                "PS4204",
                f"{owner}.__getattr__ returns {_show(ret)}; a read needs a value type",
            )
            raise AssertionError("unreachable")
        if expect is not None and not types_equal(ret, expect):
            self.fail(
                node,
                "PS4204",
                f"{owner}.__getattr__ returns {_show(ret)}, the read site expects {_show(expect)}",
            )
            raise AssertionError("unreachable")
        return _function_call(
            f"cls.{owner}.__getattr__",
            [obj, _const_str_node(name, span)],
            copy.deepcopy(ret),
            span,
        )

    def _has_attr(self, obj: dict[str, Any], name: str) -> bool:
        if _type_kind(obj["type"]) != "class":
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


def _nt_fields(node: ast.expr) -> list[str] | None:
    """namedtuple field names: a str constant split on commas/whitespace or a list of them."""
    if _plain_const_kind(node) == "str":
        names = node.value.replace(",", " ").split()
        return names if names else None
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        names = [elt.value for elt in node.elts if _plain_const_kind(elt) == "str"]
        return names if len(names) == len(node.elts) else None
    return None
