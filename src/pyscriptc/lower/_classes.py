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
    _binary_int,
    _compare,
    _concat_str,
    _const_bool,
    _const_int,
    _const_str,
    _has_typevar,
    _mk_span,
    _mk_type,
    _name,
    _node_type,
    _node_type_kind,
    _plain_const_kind,
    _show,
    _sig_typevars,
)
from pyscriptc.lower._expr_compare import _assign_node, _compare_node, _if_node, _unary_node
from pyscriptc.lower._ndarray import _dtype_of as _nd_dtype_of
from pyscriptc.lower._protocols import _exit_returns_bool
from pyscriptc.lower._stmt import _always_returns, _is_docstring
from pyscriptc.reflection import annotation_note, metaclass_note
from pyscriptc.reprspec import type_spec
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.restlower import path_attr, rest_call
from pyscriptc.restlower._dataclasses import (
    container_factory,
    factory_ast,
    marker_type,
)
from pyscriptc.restlower._popen import _reaped_fill
from pyscriptc.restlower._uuid import uuid_hex_attr
from pyscriptc.sigrec import T, type_to_dict
from pyscriptc.stdmore import (
    ALWAYS_CLASSES,
    CLASS_FIELDS,
    DEFERRED_CLASSES,
    PTR_CLASSES,
    RESERVED_CODE_EXCEPTIONS,
    SYNTHETIC_EXCEPTION_PARENTS,
    SYNTHETIC_EXCEPTIONS,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


def _const_str_node(value: str, span: dict[str, Any]) -> dict[str, Any]:
    """The const str node one dict-IR const spells."""
    return node_to_dict(_ir.const_node(_ir.P_STR, value, _ir.t_leaf("str"), _mk_span(span)))


def _const_int_node(value: int, span: dict[str, Any]) -> dict[str, Any]:
    """The const int node one dict-IR const spells."""
    return node_to_dict(_ir.const_node(_ir.P_INT, str(value), _ir.t_leaf("int"), _mk_span(span)))


def _return_node(value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The return statement node."""
    return node_to_dict(_ir.return_stmt(dict_to_node(value), _mk_span(span)))


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


def _maybe_node_kind(node: dict[str, Any] | None) -> str | None:
    """The kind tag of an IR node record that may carry none."""
    if node is None or "kind" not in node:
        return None
    return _node_kind(node)


def _record_kind(record: dict[str, Any]) -> str:
    """The kind tag of a record the typed layer cannot carry.

    A class-base descriptor (`enum`, `exception`, `class`) is not an IR
    node and has no typed record, so its tag stays a plain dict read, in
    one place.
    """
    kind: str = record["kind"]
    return kind


#: The default markers the class body pass produces. A marker is not an
#: IR node: the construction site that omits the argument replays it, so
#: its kind tag stays a plain dict read (issue #444).
DEFAULT_MARKERS = frozenset({"tuple-default", "factory-default", "field-const-default"})


def _default_kind(default: dict[str, Any]) -> str:
    """The kind tag of a default value: an IR node or a default marker."""
    kind = default.get("kind")
    if kind in DEFAULT_MARKERS:
        return str(kind)
    return _node_kind(default)


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
    def _install_class_export(
        self,
        local: str,
        item: dict[str, Any],
        stmt: ast.AST,
        fences: list[Fence],
        module: str | None = None,
    ) -> None:
        name = str(item["name"])
        if name in self.classes and not self.classes[name].get("imported"):
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        info = copy.deepcopy(item["info"])
        info["imported"] = True
        # The export table carries the MRO list and the primary-base spine
        # (issue #732): a consumer that subclasses this class resolves an
        # inherited method's owner through them. The table stores the spine
        # as a list (the export rows are plain data), so it becomes a set
        # here, the shape the class records carry.
        if "chain" in info and isinstance(info["chain"], list):
            info["chain"] = set(info["chain"])
        self.classes[name] = info
        self.class_aliases[local] = name
        self.known_classes.add(local)
        self.known_classes.add(name)
        self.declared.add(local)
        self.declared.add(name)
        if module is not None:
            self._install_frame_exports(module, info, stmt, fences)

    def _install_frame_exports(self, module: str, info: dict[str, Any], stmt: ast.AST, fences: list[Fence]) -> None:
        """Install the frame classes a resumable method's outward type names.

        A generator method returns its frame class (`gen.C.m`), so a
        consumer that calls the method but never names the frame still has
        to type the loop local and dispatch `__iter__`/`__next__` on it. The
        frame class rides the export table next to the owner class (the
        defining module emits it), and the consumer installs it here, the
        way `_install_return_class` does for a generator function's frame
        (issue #441).
        """
        methods = info.get("methods")
        if not isinstance(methods, dict):
            return
        for _mname, sig in methods.items():
            if not isinstance(sig, (list, tuple)) or len(sig) != 2:
                continue
            ret = sig[1]
            if not isinstance(ret, dict) or ret.get("kind") != "class":
                continue
            frame = str(ret.get("name", ""))
            if not frame.startswith(("gen.", "cm.")) or frame in self.classes:
                continue
            item = self._export_lookup(module, frame)
            if item is None or item.get("kind") != "class" or item.get("island"):
                continue
            self._install_class_export(frame, item, stmt, fences, module)

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
            elif name in SYNTHETIC_EXCEPTION_PARENTS:
                # The same registration with an explicit parent: CPython's
                # subprocess.TimeoutExpired derives from SubprocessError (a
                # plain Exception), so `except TimeoutError` must not match
                # it (issue #1223).
                record["exception"] = True
                record["exc_code"] = self.exc_n
                self.exc_n += 1
                record["exc_parent"] = SYNTHETIC_EXCEPTION_PARENTS[name]
            elif name in RESERVED_CODE_EXCEPTIONS:
                # A reserved-code exception (queue.Empty): the code is fixed
                # at the top of the i32 space, so user codes never shift and
                # the runtime needs no registration.
                record["exception"] = True
                record["exc_code"] = RESERVED_CODE_EXCEPTIONS[name]
                record["exc_parent"] = 7
            self.classes[name] = record

    def _is_field_call(self, node: ast.expr) -> bool:
        """Whether the expression is the `dataclasses.field` call, spelled
        either `field(...)` or `dataclasses.field(...)`."""

        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name):
            binding = self.bindings.get(node.func.id)
            return binding is not None and binding.module == "dataclasses" and binding.attr == "field"
        if isinstance(node.func, ast.Attribute):
            resolved = self._resolve_module(node.func)
            return resolved is not None and resolved[1] == "dataclasses" and resolved[2] == "field"
        return False

    def _field_default(self, node: ast.Call, typ: dict[str, Any]) -> dict[str, Any]:
        """Lower one `field(default_factory=...)` into the factory marker.

        The call rides the rest handler (`rest_call`); the expectation is
        the annotation's field type, so a callable factory's return type
        is checked against the annotation here.
        """
        return rest_call(self, node, span_of(node, self.filename), "dataclasses", "field", typ)

    def _is_tuple_annotation(self, typ: dict[str, Any]) -> bool:
        """Whether the annotation names a fixed-shape tuple class."""
        return _maybe_type_kind(typ) == "class" and bool(self.classes.get(typ["name"], {}).get("tuple"))

    def _tuple_field_default(self, node: ast.expr, typ: dict[str, Any], stmt: ast.AnnAssign) -> dict[str, Any]:
        """Lower a fixed-shape tuple default into the tuple-default marker.

        The marker stores the constant elements, not a materialized
        record: the construction site that omits the argument replays the
        elements into a fresh record, the same rule the function default
        follows.
        """
        field_rows = self.classes[typ["name"]]["fields"]
        if not isinstance(node, ast.Tuple) or len(node.elts) != len(field_rows):
            self.fail(stmt, "PS2002", "a tuple default must match the annotation's shape")
        elems = []
        for elt, frow in zip(node.elts, field_rows, strict=True):
            ev = self._expr(elt, frow["type"])
            self._expect(elt, ev["type"], frow["type"], stmt.target.id)
            if _node_kind(ev) != "const":
                self.fail(elt, "PS2002", "a tuple default element must be a constant")
            elems.append(ev)
        return _tuple_default(elems, typ)

    def _field_const_default(self, marker: dict[str, Any], typ: dict[str, Any], stmt: ast.AnnAssign) -> dict[str, Any]:
        """Lower one `field(default=<constant>)` into a plain default.

        The marker holds the default's AST node (the rest handler cannot
        lower it: the class-body collect pass runs before the module's
        function signatures are known). The value lowers here, against the
        annotation, the same rule the bare `x: T = <constant>` spelling
        follows: a constant, an `Optional` that takes `None`, or a
        fixed-shape tuple whose elements are constants.
        """
        node = marker.get("default_ast")
        if node is None:
            # `field(init=False)` with no default (issue #452): CPython
            # leaves the field unset, so the read raises `AttributeError`.
            # The lowered struct always has the slot, so this shape has no
            # right answer and fences here.
            self.fail(stmt, "PS2002", "field(init=False) needs a default")
        assert isinstance(node, ast.expr)
        if self._is_tuple_annotation(typ):
            return self._tuple_field_default(node, typ, stmt)
        value = self._expr(node, None if typ.get("optional") else typ)
        if _node_kind(value) != "const":
            self.fail(node, "PS2002", "a field default must be a constant")
        return value

    def _check_factory_default(self, stmt: ast.AnnAssign, marker: dict[str, Any], typ: dict[str, Any]) -> None:
        """Type-check one factory default against the annotation.

        The container factories build the empty container of the field's
        type, so a container annotation is the only shape that lowers; a
        callable factory's result type is the annotation itself, which the
        rest handler already checked.
        """
        name = container_factory(marker)
        if name is None:
            return
        if name == "list" and _type_kind(typ) == "list" and not typ.get("set") and typ.get("elem") is not None:
            return
        if name == "set" and _type_kind(typ) == "list" and typ.get("set") and typ.get("elem") is not None:
            return
        if name == "dict" and _type_kind(typ) == "dict":
            return
        if _type_kind(typ) == "func":
            return
        self.fail(stmt, "PS1003", f"default_factory={name} needs a matching container annotation")

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
        # The enum-as-int mixin spelling (issue #1314): `class C(int, Enum)`
        # is CPython's documented IntEnum spelling, so the bare `int` base
        # erases here and the enum base drives the enum collection. The
        # erase happens only in that spelling: an `int` base on its own
        # keeps the `this class base is not lowered` fence.
        int_bases = {id(b) for b in node.bases if isinstance(b, ast.Name) and b.id == "int"}
        enum_mixin = False
        if int_bases:
            others = [b for b in node.bases if id(b) not in int_bases]
            enum_mixin = any(self._enum_base_name(other) is not None for other in others)
            if enum_mixin:
                node.bases = others
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
                self.fail_base(
                    node,
                    "PS2002",
                    "multiple inheritance needs lowered class bases",
                    base_node,
                )
            bases.append(resolved)
        base = bases[0] if bases else None
        # The int-mixin enum spelling (issue #1314): `class C(int, Enum)` is
        # CPython's IntEnum spelling, so the collected enum's base is the
        # integer one and the members answer as ints.
        if base is not None and _base_kind(base) == "enum" and enum_mixin:
            base = _enum_base("IntEnum")
        if deco == "dataclass" and base is not None:
            # The dataclass base (issue #452): CPython lets a dataclass
            # extend a dataclass, and the child's fields follow the base's.
            # Only a dataclass base lowers: a plain class base would mix
            # the two constructor shapes, and the base's own `__init__` is
            # not the dataclass one.
            base_info = self.classes.get(str(base["name"]))
            if base_info is None or base_info.get("generated_init") != "dataclass":
                self.fail(node, "PS2002", "a dataclass base is not lowered")
        if base is not None and _base_kind(base) == "enum":
            parent = str(base.get("parent", ""))
            if parent:
                self.enum_parent_of[node.name] = parent
                self.enum_base_members[node.name] = dict(self.classes.get(parent, {}).get("members", {}))
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
        # The annotated types of the class body's constant attributes
        # (`x: T = <const>`). The constant is a class attribute in
        # CPython, and the annotation still names the type of the value
        # an instance may carry: a written `__init__` that assigns
        # `self.x` proves an instance field of that type, and the
        # instance field shadows the class attribute (issue #444).
        const_types: dict[str, dict[str, Any]] = {}
        for ancestor in reversed(mro[1:]):
            ancestor_types = self.classes.get(ancestor, {}).get("const_types")
            if ancestor_types:
                const_types.update(ancestor_types)
        # Class-body assignments with a runtime value (`x = f()`, a
        # comprehension, a container of computed elements): the value lowers
        # after the signature pass into the class's initializer, and the
        # attribute reads through a runtime slot keyed `C.x`. The statements
        # are recorded here; `_lower_class_attrs` lowers them.
        runtime_attrs: list[tuple[str, ast.stmt]] = []
        # The cached properties of this class (issue #440): each one owns a
        # hidden value slot and a hidden computed flag in the class's struct
        # layout, so the value survives the first read per instance.
        cached_props: list[str] = []
        cached_names: set[str] = set()
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None and deco != "dataclass":
                        if self._is_field_call(stmt.value):
                            # `field(...)` spells a dataclass field (issues
                            # #1279, #444); a plain class body is not a
                            # dataclass, so the call keeps its fence.
                            self.fail(stmt.value, "PS2002", "dataclasses.field lowers on a @dataclass only")
                        const = self._module_fold(stmt.value)
                        if const is None or const.get("kind") not in {"const", "tuple-const"}:
                            typ = self._annotation(stmt.annotation)
                            if _type_kind(typ) not in {"str", "int", "list", "dict"}:
                                self.fail(stmt, "PS1204", "class fields are declared with an annotation and no value")
                            if stmt.target.id in seen or stmt.target.id in inherited_types:
                                self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                            if stmt.target.id in cached_names:
                                self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                            runtime_attrs.append((stmt.target.id, stmt))
                            seen.add(stmt.target.id)
                            continue
                        typ = self._annotation(stmt.annotation)
                        if const.get("kind") == "tuple-const":
                            # A fixed-shape tuple constant (issue #444): the
                            # class attribute holds the tuple, and the
                            # annotation must name the same shape.
                            if not self._is_tuple_annotation(typ):
                                # The annotation does not name a tuple: the
                                # value still has to match it (an `int`
                                # annotation over a tuple is the type
                                # mismatch the fence names).
                                self._expect(stmt, const["type"], typ, stmt.target.id)
                                self.fail(stmt, "PS1204", "class fields are declared with an annotation and no value")
                            if len(const["elems"]) != len(self.classes[typ["name"]]["fields"]):
                                self.fail(stmt, "PS2002", "a tuple default must match the annotation's shape")
                            for elem, frow in zip(const["elems"], self.classes[typ["name"]]["fields"], strict=True):
                                self._expect(stmt, elem["type"], frow["type"], stmt.target.id)
                        elif typ.get("optional"):
                            if _type_kind(const["type"]) != "none" and not types_equal(const["type"], typ["elem"]):
                                self.fail(stmt, "PS1003", f"{stmt.target.id} default is not {_show(typ)}")
                        else:
                            self._expect(stmt, const["type"], typ, stmt.target.id)
                        class_constants[stmt.target.id] = const
                        const_types[stmt.target.id] = typ
                        continue
                    if stmt.target.id in seen or stmt.target.id in inherited_types:
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                    if stmt.target.id in cached_names:
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                    typ = self._annotation(stmt.annotation)
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
                            if self._is_field_call(stmt.value):
                                # `field(default_factory=...)` (issue #1279): the
                                # per-instance default. The marker carries the
                                # factory, not a value, so the construction
                                # site replays it. `field(default=<constant>)`
                                # (issue #444) rides the same call with a
                                # marker that defers to this pass: the value
                                # lowers right here, against the annotation,
                                # the same rule the bare spelling follows.
                                default = self._field_default(stmt.value, typ)
                                # The `repr=`/`compare=`/`init=` flags (issue
                                # #452) ride the marker; each names whether the
                                # field joins the synthesized `__repr__` and
                                # `__eq__` or the constructor.
                                for flag_name in ("repr", "compare", "init"):
                                    if flag_name in default:
                                        field[flag_name] = bool(default[flag_name])
                                if _record_kind(default) == "field-const-default":
                                    default = self._field_const_default(default, typ, stmt)
                            elif self._is_tuple_annotation(typ):
                                default = self._tuple_field_default(stmt.value, typ, stmt)
                            else:
                                default = self._expr(stmt.value, None if typ.get("optional") else typ)
                        finally:
                            self.prelude = saved_prelude
                        if _record_kind(default) == "factory-default":
                            # The factory default: the field takes the
                            # factory's value per construction, so the
                            # annotation drives the factory's result type.
                            self._check_factory_default(stmt, default, typ)
                            field["factory"] = default
                        elif _default_kind(default) not in {"const", "tuple-default"}:
                            self.fail(stmt, "PS2002", "a dataclass default must be a constant")
                        elif typ.get("optional"):
                            if _type_kind(default["type"]) != "none" and not types_equal(default["type"], typ["elem"]):
                                self.fail(stmt, "PS1003", f"{stmt.target.id} default is not {_show(typ)}")
                            field["optdefault"] = True
                        elif _default_kind(default) != "tuple-default":
                            self._expect(stmt, default["type"], typ, stmt.target.id)
                        if _record_kind(default) != "factory-default":
                            field["default"] = default
                        seen_default = True
                    elif seen_default:
                        self.fail(stmt, "PS2002", "a non-default field follows a default")
                    elif any(("default" in base_field or "factory" in base_field) for base_field in inherited):
                        # The dataclass base (issue #452): CPython's rule
                        # spans base and child, so a required child field
                        # after a defaulted base field is the same
                        # TypeError CPython raises at class creation.
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
                    if kind == "cached_property":
                        cached_props.append(stmt.name)
                        # The name is the compiler's: a same-named field or
                        # class constant would shadow the slot protocol.
                        if stmt.name in seen or stmt.name in inherited_types:
                            self.fail(stmt, "PS2002", f"cached property {stmt.name} clashes with a field")
                        cached_names.add(stmt.name)
                    methods.append((stmt, kind))  # type: ignore[arg-type]
                    continue
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    if stmt.targets[0].id in cached_names:
                        # A cached property owns the name (issue #440): a
                        # class-body assignment of it has no slot protocol.
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.targets[0].id}")
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
        #
        # A cross-module base (issue #732) brings its inherited methods
        # through the export table, and the export records the definer, not
        # the class the consumer imported: `Mid`'s export says `get_x` is
        # owned by `Base`, two modules up. The ancestor's own MRO names that
        # definer, so the method is inherited with its real owner and the
        # emitted symbol stays `cls.{Base}.{method}`, the one the defining
        # module emits. Without the ancestor's MRO the method is dropped and
        # the call site has no body to call.
        for ancestor in mro[1:]:
            ancestor_info = self.classes.get(ancestor)
            if ancestor_info is None:
                continue
            ancestor_mro = ancestor_info.get("mro", [ancestor])
            for mname, sig in ancestor_info["methods"].items():
                owner = ancestor_info["method_owner"].get(mname)
                if mname in method_sigs:
                    continue
                if owner != ancestor and owner not in ancestor_mro:
                    continue
                if deco == "dataclass" and mname in {"__eq__", "__repr__"}:
                    # The dataclass base (issue #452): CPython regenerates
                    # the synthesized `__eq__` and `__repr__` on the child,
                    # over the whole field list, so the base's copies do not
                    # come through the MRO. A child that writes its own
                    # `__eq__`/`__repr__` keeps it (the MRO loop skips the
                    # names the child defines).
                    continue
                if sig[0] and sig[0][0]["name"] == "self":
                    sig = ([_self_param(node.name), *sig[0][1:]], sig[1])
                method_sigs[mname] = sig
                kinds[mname] = ancestor_info["method_kind"].get(mname, "method")
                owners[mname] = owner if owner is not None else ancestor
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
                        const_types,
                    )
                )
        # A method other than `__init__` may write a field the class body
        # declared as a constant attribute (`x: T = <const>`, issue
        # #444): the write creates the instance attribute, and the class
        # attribute keeps its own value. The field's type is the class
        # body's annotation, the same rule the `__init__` write follows.
        for method, _kind in methods:
            if method.name == "__init__":
                continue
            fields.extend(
                self._method_const_fields(
                    method,
                    {field["name"] for field in fields} | set(inherited_types),
                    const_types,
                )
            )
        # The cached properties' hidden struct slots (issue #440): the value
        # slot rides the getter's proven return type, the flag is the one
        # byte `ps_alloc`'s calloc zero-fills (false = never computed). The
        # slots join the class's struct layout after the declared fields, so
        # the declared layout is never reordered; `own_fields` stays the
        # declared set, so `__repr__`, the dataclass `__eq__` and the export
        # table never see them.
        hidden_fields: list[dict[str, Any]] = []
        # The declared fields of this class and of its dataclass bases:
        # the set the dataclass rules read (the constructor, the
        # `__repr__` and the `__eq__`). The hidden slots stay out of it.
        declared_fields: list[dict[str, Any]] = []
        for name in cached_props:
            sig = method_sigs.get(name)
            if sig is None:
                continue
            ret = sig[1]
            if not self._cached_slot_type(ret):
                self.fail(node, "PS2002", f"cached property {name} needs a storable return type")
            hidden_fields.append({"name": f"__cached_{name}", "type": ret})
            hidden_fields.append({"name": f"__cached_{name}_set", "type": _leaf_type("bool")})
        generated: str | None = None
        generated_eq = False
        init_target: str | None = None
        seed_init = (
            deco != "dataclass"
            and not wrote_init
            and "__init__" not in method_sigs
            and any(field["name"] in class_constants for field in fields)
        )
        if seed_init:
            # The class body spells a constant attribute and a method
            # writes it: the instance slot needs the constant before the
            # write, the CPython class-attribute fallback. The class has
            # no written `__init__`, so the seeding one is synthesized
            # (issue #444).
            method_sigs["__init__"] = ([_self_param(node.name)], _leaf_type("none"))
            kinds["__init__"] = "method"
            owners["__init__"] = node.name
            generated = "const-seed"
        if deco == "dataclass":
            if wrote_init:
                self.fail(node, "PS2002", "a dataclass does not take a written __init__")
            # The dataclass base (issue #452): the base's fields are the
            # constructor's leading parameters, so the child's own fields
            # follow them. CPython's rule is the whole field list in
            # order, so a required field after a defaulted one is the same
            # fence the base-less dataclass has.
            # The cached-property hidden slots (issue #440) are struct
            # slots, not dataclass fields: they ride the layout but not
            # the constructor, the `__repr__` or the `__eq__`, so the
            # dataclass rules below see only the declared set.
            all_fields = [*inherited, *fields]
            declared_fields = list(all_fields)
            params = [_self_param(node.name)]
            init_excluded: list[dict[str, Any]] = []
            for field in all_fields:
                if field.get("init") is False:
                    # `field(init=False)` (issue #452): the field is not a
                    # constructor parameter. CPython's generated `__init__`
                    # writes the field's default (or calls the factory) and
                    # takes no argument for it.
                    init_excluded.append(field)
                    continue
                param: dict[str, Any] = {"name": field["name"], "type": field["type"]}
                if "default" in field:
                    param["default"] = field["default"]
                    if field.get("optdefault"):
                        param["optdefault"] = True
                if "factory" in field:
                    # The per-instance default (issue #1279): the
                    # construction site that omits the argument calls the
                    # factory, so every instance gets its own value.
                    param["factory"] = field["factory"]
                params.append(param)
            method_sigs["__init__"] = (params, _leaf_type("none"))
            kinds["__init__"] = "method"
            owners["__init__"] = node.name
            generated = "dataclass"
            if (
                "__eq__" not in method_sigs
                and all_fields
                and all(
                    self._eq_comparable_field(field["type"], current=node.name)
                    for field in all_fields
                    if field.get("compare", True)
                )
            ):
                # The dataclass `__eq__` (issue #1279): CPython synthesizes
                # it, so the signature is registered here, in the collect
                # pass, and the compare sites find it before the body pass
                # lowers the method. A written `__eq__` wins. A field the
                # chain cannot spell keeps the identity fallback. A field
                # spelled `field(compare=False)` (issue #452) drops out of
                # the chain, so it needs no compare.
                eq_params = [
                    _self_param(node.name),
                    {"name": "other", "type": _class_type(node.name)},
                ]
                method_sigs["__eq__"] = (eq_params, _leaf_type("bool"))
                kinds["__eq__"] = "method"
                owners["__eq__"] = node.name
                generated_eq = True
        elif not wrote_init and "__init__" in method_sigs and not seed_init:
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
            "fields": [*inherited, *fields, *hidden_fields],
            "own_fields": fields,
            "declared_fields": declared_fields or fields,
            "constants": class_constants,
            "const_types": const_types,
            "methods": method_sigs,
            "method_kind": kinds,
            "method_owner": owners,
            "base": parent_name,
            "mro": mro,
            "chain": chain,
            "node": node,
            "class_id": self._class_ids.assign(node.name),
        }
        if cached_props:
            # The cached properties of this class (issue #440): the read
            # site calls the synthesized accessor, and the accessor owns
            # the one-shot compute.
            info["cached_props"] = cached_props
        if generated is not None:
            info["generated_init"] = generated
            info["init_target"] = init_target
        if generated_eq:
            info["generated_eq"] = True
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
        const_types: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Fields a written `__init__` proves by `self.<name>` targets.

        `self.x = <param>` takes the parameter's annotated type;
        `self.x: T = v` takes the annotation (the value's own check runs
        in the body pass). Names the class body or a base already
        declared are skipped: the declared type wins and a mismatch
        fences at the write.

        `self.x = <value>` (a literal, a call, any value that is not a
        parameter) takes the type of the class body's `x: T` declaration
        when the class body has one (issue #444): a plain class's
        `x: T = <constant>` is a class attribute in CPython, and the
        annotation still names the type the instance field carries. The
        write then shadows the class attribute, the same rule CPython's
        instance dictionary follows.
        """
        found: list[dict[str, Any]] = []
        seen = set(taken)
        const_types = const_types or {}
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
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute):
                target = stmt.targets[0]
                if isinstance(stmt.value, ast.Name):
                    typ = arg_types.get(stmt.value.id)
                else:
                    # A value that is not a parameter: the class body's
                    # annotation is the only type the field has.
                    typ = const_types.get(target.attr)
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

    def _method_const_fields(
        self,
        fn: ast.FunctionDef,
        taken: set[str],
        const_types: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fields a non-init method proves by `self.<constant name>` writes.

        Only the class body's constant attributes take part: `x: T =
        <const>` declares the type, and the method's `self.x = ...`
        creates the instance attribute over it (issue #444). A write to
        a name the class body never declared has no type and stays
        fenced at the write site, the rule every other undeclared
        attribute write follows.
        """
        found: list[dict[str, Any]] = []
        for stmt in ast.walk(fn):
            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Attribute)
                and isinstance(stmt.targets[0].value, ast.Name)
                and stmt.targets[0].value.id == "self"
                and stmt.targets[0].attr in const_types
                and stmt.targets[0].attr not in taken
            ):
                continue
            name = stmt.targets[0].attr
            taken.add(name)
            found.append({"name": name, "type": copy.deepcopy(const_types[name])})
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
            "__ne__": "bool",
            "__lt__": "bool",
            "__le__": "bool",
            "__gt__": "bool",
            "__ge__": "bool",
            "__contains__": "bool",
            "__bool__": "bool",
            "__hash__": "int",
            "__format__": "str",
        }.get(name)
        if want is not None and _type_kind(ret) != want:
            self.fail(node, "PS1003", f"{class_name}.{name} returns {_show(ret)}, not {want}")
        if name == "__exit__" and not _exit_returns_bool(ret):
            self.fail(node, "PS1003", f"{class_name}.__exit__ returns {_show(ret)}, not bool")
        emit = self._emit_owner(class_name, name)
        return _function_call(f"cls.{emit}.{name}", [receiver, *args], ret, span)

    def _definer(self, class_name: str, name: str) -> str | None:
        """The first mro class defining `name` as a plain method, or None.

        The `_emit_owner` rule: a dunder bound as an attribute, decorated,
        a `staticmethod` or a `classmethod` counts as undefined.
        """
        info = self.classes.get(class_name)
        if info is None:
            return None
        # A class record without an mro of its own (a generator frame
        # class, an exported record) still defines its own methods, so the
        # empty list falls back to the class itself.
        for ancestor in info.get("mro") or [class_name]:
            xinfo = self.classes.get(ancestor)
            if xinfo is None:
                continue
            if xinfo["methods"].get(name) is not None and xinfo.get("method_kind", {}).get(name, "method") == "method":
                return ancestor
        return None

    def _class_in_chain(self, a: str, b: str) -> bool:
        """True when `b` sits in `a`'s c3 mro (a and b share one chain)."""
        info = self.classes.get(a)
        if info is None:
            return False
        return b in info.get("mro", [a])

    def _class_accepts(self, definer: str, name: str, operand: str) -> bool:
        """True when the definer's dunder parameter accepts the operand class.

        The parameter annotation must be the operand class itself or an
        ancestor of it; the checker rejects the `other: object` spelling
        that would need narrowing, so no annotation means no match.
        """
        info = self.classes.get(definer)
        if info is None:
            return False
        sig = info["methods"].get(name)
        if sig is None:
            return False
        params, _ret = sig
        if len(params) < 2:
            return False
        param = params[1]["type"]
        if param.get("kind") != "class":
            return False
        pname = str(param.get("name"))
        return pname == operand or self._class_in_chain(operand, pname)

    def _compare_dispatch(
        self,
        node: ast.AST,
        op: str,
        left: dict[str, Any],
        right: dict[str, Any],
        span: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Protocol dispatch for the compare family on class operands.

        The fixed order of decision 2026-11-23: the subclass-priority
        reflected call, the left definer of the op's dunder, the right
        class's reflected dunder with swapped operands, else None (the
        caller applies the identity fallback or fences). `==` passes
        `__eq__`/`__eq__`, `!=` `__ne__`/`__ne__` (falling back to the
        `==` result's negation when no user `__ne__` resolves), `<` maps
        to `__lt__`/`__gt__` and so on.
        """
        table = {
            "==": ("__eq__", "__eq__"),
            "!=": ("__ne__", "__ne__"),
            "<": ("__lt__", "__gt__"),
            "<=": ("__le__", "__ge__"),
            ">": ("__gt__", "__lt__"),
            ">=": ("__ge__", "__le__"),
        }
        if op not in table:
            return None
        dunder, reflected = table[op]
        lname = str(left["type"].get("name"))
        rname = str(right["type"].get("name"))
        ldef = self._definer(lname, dunder)
        rdef = self._definer(rname, dunder)
        if op == "!=" and ldef is None and rdef is None:
            # object.__ne__ negates the == result; == goes through this
            # same order.
            eq = self._compare_dispatch(node, "==", left, right, span)
            if eq is None:
                return None
            return _unary_node("not", eq, {"kind": "bool"}, span)
        if lname != rname and self._class_in_chain(rname, lname):
            # rname subclasses lname: CPython's subclass priority tries the
            # reflected call first when the definers differ.
            if rdef is not None and rdef != ldef and self._class_accepts(rdef, dunder, lname):
                called = self._dunder_call(node, right, dunder, [left], span)
                if called is not None:
                    return called
        if ldef is not None and self._class_accepts(ldef, dunder, rname):
            called = self._dunder_call(node, left, dunder, [right], span)
            if called is not None:
                return called
        if op == "!=":
            if rdef is not None and self._class_accepts(rdef, dunder, lname):
                called = self._dunder_call(node, right, dunder, [left], span)
                if called is not None:
                    return called
            eq = self._compare_dispatch(node, "==", left, right, span)
            if eq is None:
                return None
            return _unary_node("not", eq, {"kind": "bool"}, span)
        if rdef is not None and self._class_accepts(rdef, reflected, lname):
            called = self._dunder_call(node, right, reflected, [left], span)
            if called is not None:
                return called
        return None

    def _pin_operand(self, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """Pin a lowered operand to a temp so the protocol call reads it
        once; both compare arms and the bool test see the same value."""
        temp = self._temp("proto")
        self.locals[temp] = expr["type"]
        self.prelude.append(_assign_node(temp, expr, span))
        return _name(temp, expr["type"], span)

    def _truthy_dispatch(self, node: ast.AST, expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
        """Truthiness of a class operand: the `__bool__` definer, else the
        `__len__` definer against zero, else None (the caller keeps the
        constant-True default the decision spells)."""
        if expr["type"].get("optional") or expr["type"].get("island"):
            return None
        name = str(expr["type"]["name"])
        if self._definer(name, "__bool__") is None and self._definer(name, "__len__") is None:
            return None
        ref = self._pin_operand(expr, span)
        if self._definer(name, "__bool__") is not None:
            called = self._dunder_call(node, ref, "__bool__", [], span)
            if called is not None:
                return called
        called = self._dunder_call(node, ref, "__len__", [], span)
        if called is not None:
            return _compare("!=", called, _const_int(0, span), span)
        return None

    def _hash_dispatch(self, node: ast.AST, arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
        """`hash(x)` on a class operand: the `__hash__` definer with -1
        mapped to -2, the unhashable fence for an eq-without-hash class,
        else None (the emit's identity hash answers)."""
        name = str(arg["type"]["name"])
        if self._definer(name, "__hash__") is None:
            eqdef = self._definer(name, "__eq__")
            if eqdef is not None:
                self.fail(node, "PS2004", f"hash({name}) is refused: the class defines __eq__ without __hash__")
            return None
        called = self._dunder_call(node, arg, "__hash__", [], span)
        if called is None:
            return None
        int_t: dict[str, Any] = {"kind": "int"}
        # CPython reserves -1 for its own error slot and maps the result to
        # -2. Our int is i64 (divergence 1), so CPython's wider fold-through
        # cannot occur and every other value passes through verbatim.
        pinned = self._pin_operand(called, span)
        neg_one = _compare("==", pinned, _const_int(-1, span), span)
        mapped = _binary_int("-", pinned, _const_int(1, span), span)
        temp = self._temp("hashmap")
        self.locals[temp] = int_t
        self.prelude.append(
            _if_node(neg_one, [_assign_node(temp, mapped, span)], [_assign_node(temp, pinned, span)], span)
        )
        return _name(temp, int_t, span)

    def _lower_factories(self, info: dict[str, Any]) -> None:
        """Lower the callable factories of one class into callable values.

        The class-body collect pass runs before the module's function
        signatures are known, so a factory that names a module function
        could not be lowered there. The class body pass runs after, so the
        callable value is built here and stored on the `__init__` parameter
        the construction site replays.
        """
        params = info.get("methods", {}).get("__init__")
        if params is None:
            return
        # The `__init=False` fields (issue #452) carry no parameter, so
        # their factory rides the field row instead of a parameter row.
        markers: list[dict[str, Any]] = []
        for param in params[0]:
            marker = param.get("factory")
            if marker is not None:
                markers.append(marker)
        for field in info.get("fields", []):
            marker = field.get("factory")
            if marker is not None:
                markers.append(marker)
        for marker in markers:
            ast_node = factory_ast(marker)
            if ast_node is None:
                continue
            field_type = marker_type(marker)
            # `default_factory=ClassName` (issue #452): a class is a
            # callable, so the factory replays the zero-argument
            # construction at the site that omits the field. The field's
            # annotation names the class, so no callable value is lowered.
            factory_node = factory_ast(marker)
            class_name = factory_node.id if isinstance(factory_node, ast.Name) else None
            if (
                class_name is not None
                and not field_type.get("optional")
                and _type_kind(field_type) == "class"
                and str(field_type.get("name")) == class_name
                and class_name in self.classes
            ):
                marker["class_ctor"] = class_name
                marker["factory_ast"] = None
                continue
            ret = field_type
            if ret.get("optional"):
                ret = ret["elem"]
            ftype = {"kind": "func", "params": [], "return": ret}
            saved_prelude = self.prelude
            self.prelude = []
            try:
                value = self._expr(ast_node, ftype)
            finally:
                self.prelude = saved_prelude
            if _node_type_kind(value) != "func":
                self.fail(ast_node, "PS2002", "default_factory takes a callable with no parameters")
            if _node_type(value)["params"]:
                self.fail(ast_node, "PS2003", "default_factory takes no arguments")
            marker["factory"] = value
            marker["factory_ast"] = None

    def _lower_class(self, node: ast.ClassDef, emit_name: str | None = None) -> dict[str, Any] | None:
        own = emit_name or node.name
        info = self.classes[own]
        if info.get("enum"):
            return self._lower_enum(node, own, info)
        self._lower_factories(info)
        own_names = {field["name"] for field in info.get("own_fields", info["fields"])}
        methods: list[dict[str, Any]] = []
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            self.method_nodes.setdefault((own, stmt.name), stmt)
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
        generated_repr = self._generated_repr(node, info)
        if generated_repr is not None:
            methods.append(generated_repr)
        generated_eq = self._generated_eq(node, info)
        if generated_eq is not None:
            methods.append(generated_eq)
        # The cached-property accessors (issue #440): one method per cached
        # property, after the getter itself, so the emitter's function list
        # carries both.
        for name in info.get("cached_props", []):
            accessor = self._cached_accessor(node, info, name)
            if accessor is not None:
                methods.append(accessor)
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

    def _lower_enum(self, node: ast.ClassDef, own: str, info: dict[str, Any]) -> dict[str, Any] | None:
        """The class record an enum's methods emit (issue #451).

        CPython attaches an enum's methods to the enum class, so every
        member answers them. The enum class has no instance layout (the
        member is the value itself), so the record carries only the
        methods: the emitter spells each one `cls.{Enum}.{method}`, the
        same shape a normal class method takes.
        """
        own_names: set[str] = set()
        methods: list[dict[str, Any]] = []
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if info["method_owner"].get(stmt.name) != own:
                continue
            emitted = self._emit_class_method(own, stmt, info, own_names)
            if emitted is not None:
                methods.append(emitted)
        if self.caught:
            return None
        if not methods:
            return None
        return {
            "name": own,
            "fields": [],
            "methods": methods,
            "span": span_of(node, self.filename),
        }

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
        is_enum = bool(info.get("enum"))
        if kind == "class":
            # The cls row rides the emitted signature; the body reads the id
            # through `self.cls_class`, not through a local (issue #447).
            params = [{"name": "cls", "type": _leaf_type("int")}, *params]
        elif is_enum:
            # An enum method's `self` is the member (issue #451), and the
            # lowered member is the value itself, so the row keeps the value
            # kind the signature pass gave it.
            pass
        elif params and params[0]["name"] == "self":
            params = [_self_param(class_name), *params[1:]]
        # A generator method's body lowers under the None-returning resume
        # signature; the outward frame type rides in the method signature.
        params, ret = self._coro_method_body_sig(class_name, stmt.name, params, ret)
        self.locals = {param["name"]: param["type"] for param in params}
        self.frozen_names = set()
        self.fn_body = list(stmt.body)
        if kind in {"method", "property"} and not is_enum:
            self.locals["self"] = _class_type(class_name)
        # A classmethod body lowers with the receiver class named: `cls`
        # reads the compile-time class id (issue #447, divergence 10).
        saved_cls_class = self.cls_class
        self.cls_class = class_name if kind == "class" else None
        # Function-local imports rebind module names: snapshot the
        # compile-time binding tables so they restore when the body ends.
        saved_bindings = dict(self.bindings)
        saved_aliases = dict(self.type_aliases)
        saved_declared = set(self.declared)
        saved_typevars = dict(self.typevars)
        saved_bounds = dict(self.typevar_bounds)
        try:
            body = self._body(list(stmt.body))
        finally:
            self.cls_class = saved_cls_class
            self.bindings = saved_bindings
            self.type_aliases = saved_aliases
            self.declared = saved_declared
            self.typevars = saved_typevars
            self.typevar_bounds = saved_bounds
        if self.caught:
            return None
        if stmt.name == "__init__":
            assigned = _assigned_fields(stmt.body)
            missing = sorted(own_names - assigned)
            if missing:
                self.fail(stmt, "PS1001", f"__init__ does not assign {', '.join(missing)}")
            body = self._seed_const_fields(class_name, info, body, span_of(stmt, self.filename))
        if _type_kind(ret) != "none" and not _always_returns(body):
            self.fail(stmt, "PS1004", f"{stmt.name} can fall off the end and return None")
        return self._method_ir(stmt.name, params, ret, body, span_of(stmt, self.filename))

    def _drain_frame_methods(self, lowered_classes: list[dict[str, Any]], fences: list[Fence]) -> None:
        """Lower every queued frame-specialized copy of a method.

        A generator frame passed into an iterator-shaped method parameter
        needs the method's own slot to carry the frame's layout, not the
        synthetic `iter_` cursor ({seq, idx}): the cursor read as a frame
        dereferences the frame's fields as a list cell and segfaults
        (issue #441). The copy is the same body with one parameter's type
        replaced by the frame type, so the copied body's `for`/`next()`
        dispatch on the frame's real `__iter__`/`__next__` methods.

        The copy is a method of the same class under its own name, so the
        original method keeps its own signature and the two coexist: a
        call site that passes a frame calls the copy, every other call
        site calls the original.
        """
        while self.frame_method_queue:
            class_name, owner, method, emit, stmt, slot, frame_t = self.frame_method_queue.pop(0)
            info = self.classes.get(class_name)
            if info is None:
                continue
            sig = info["methods"].get(method)
            if sig is None:
                continue
            params = [param for param in sig[0] if param["name"] != "self"]
            if slot >= len(params):
                fences.append(Fence("PS2002", "the frame parameter is gone", span_of(stmt, self.filename), method))
                continue
            self.function = f"{class_name}.{emit}"
            self.temp_n = 0
            self.prelude = []
            # boxed:begin
            self.island_modules = {}
            self.island_class_handles = {}
            # boxed:end
            self.caught = []
            self.fn_globals = self._globals_for(stmt.body)
            self.fn_nonlocals = set()
            self.captures = {}
            copy_params = [dict(param) for param in params]
            copy_params[slot]["type"] = frame_t
            copy_params = [_self_param(class_name), *copy_params]
            ret = sig[1]
            self.frame_sig = (copy_params, ret)
            self.locals = {param["name"]: param["type"] for param in copy_params}
            self.frozen_names = set()
            self.fn_body = list(stmt.body)
            saved_bindings = dict(self.bindings)
            saved_aliases = dict(self.type_aliases)
            saved_declared = set(self.declared)
            saved_typevars = dict(self.typevars)
            saved_bounds = dict(self.typevar_bounds)
            saved_cls_class = self.cls_class
            self.cls_class = None
            try:
                body = self._body(list(stmt.body))
            finally:
                self.cls_class = saved_cls_class
                self.bindings = saved_bindings
                self.type_aliases = saved_aliases
                self.declared = saved_declared
                self.typevars = saved_typevars
                self.typevar_bounds = saved_bounds
                self.frame_sig = None
            if self.caught:
                fences.extend(self.caught)
                continue
            record = self._method_ir(emit, copy_params, ret, body, span_of(stmt, self.filename))
            target = next((cls for cls in lowered_classes if cls["name"] == class_name), None)
            if target is None:
                fences.append(
                    Fence("PS2002", "the frame-specialized method has no class", span_of(stmt, self.filename), method)
                )
                continue
            target["methods"].append(record)

    def _seed_const_fields(
        self, class_name: str, info: dict[str, Any], body: list[dict[str, Any]], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Seed the instance fields whose class body spells a constant.

        `class C: x: int = 1` is a class attribute in CPython, and an
        instance read of `x` falls back to it until the instance writes
        its own. The lowered class is a struct, so the fallback is a
        store: the `__init__` prologue writes the constant into the field
        slot, and the body's `self.x = ...` overwrites it (issue #444).
        Only the fields the body proves (every own field must be
        assigned) take a seed, so a class attribute nothing writes keeps
        its compile-time read.
        """
        constants = info.get("constants") or {}
        self_type = _class_type(class_name)
        seeds: list[dict[str, Any]] = []
        for field in info.get("own_fields", []):
            const = constants.get(field["name"])
            if const is None or const.get("kind") != "const":
                continue
            if field["type"].get("optional") and (
                _type_kind(const["type"]) == "none" or types_equal(const["type"], field["type"]["elem"])
            ):
                # The class attribute holds the Optional's unwrapped value
                # (`None` or the element, CPython's class dictionary) and
                # the field slot holds the boxed cell: seed the cell with
                # the attribute's value (issue #444).
                seeds.append(
                    _setattr_node(
                        _name("self", self_type, span),
                        field["name"],
                        self._opt_box(None if _type_kind(const["type"]) == "none" else const, field["type"], span),
                        span,
                    )
                )
                continue
            if not types_equal(const["type"], field["type"]):
                # The class attribute and the instance field disagree on
                # the type (a re-annotation spells this in CPython, which
                # keeps the class attribute and gives the instance its
                # own). One struct slot cannot hold both, so the fence
                # names the field and the two types.
                self.fail(
                    info.get("node"),
                    "PS1003",
                    f"{class_name}.{field['name']} is a {_show(const['type'])} class attribute "
                    f"and a {_show(field['type'])} field",
                )
            seeds.append(_setattr_node(_name("self", self_type, span), field["name"], {**const, "span": span}, span))
        if self.prelude:
            # The prelude builds the temps the seeds read (the Optional
            # cell), so it comes first.
            seeds = [*self.prelude, *seeds]
            self.prelude = []
        if not seeds:
            return body
        return [*seeds, *body]

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

    def _cached_slot_type(self, typ: dict[str, Any]) -> bool:
        """True when a value of this type can live in a cached property's slot.

        The slot is a struct field, so it takes the field types the struct
        layout carries: `str`, `int`, `list`, `dict` and the pointer
        classes. A `float`, a `bool` and a tuple have no field form, so the
        cached property keeps its fence.
        """
        kind = _type_kind(typ)
        if kind in {"str", "int", "list", "dict"}:
            return True
        if kind == "class":
            return bool(typ.get("name") in PTR_CLASSES)
        return False

    def _cached_accessor(self, node: ast.ClassDef, info: dict[str, Any], name: str) -> dict[str, Any] | None:
        """The accessor method a cached property's read site calls.

        The accessor owns the whole protocol (issue #440): the first read
        calls the getter once, stores the value in the class's hidden slot
        and marks the slot computed; every later read answers the stored
        value. The getter itself stays an ordinary method (`cls.{C}.{name}`),
        so the accessor calls it the way a `@property` read calls its getter.
        """
        sig = info["methods"].get(name)
        if sig is None:
            return None
        ret = sig[1]
        span = span_of(node, self.filename)
        self_type = _class_type(node.name)
        self_n = _name("self", self_type, span)
        value_slot = f"__cached_{name}"
        flag_slot = f"__cached_{name}_set"
        flag = _attr_node(self_n, flag_slot, _leaf_type("bool"), span)
        value = _attr_node(self_n, value_slot, ret, span)
        owner = info["method_owner"].get(name, node.name)
        getter = _function_call(f"cls.{owner}.{name}", [self_n], ret, span)
        # The computed value rides a local: the store writes the slot and
        # the return answers the same value, the way CPython's descriptor
        # stores the getter's result and returns it.
        computed = f"__cached_{name}_value"
        params = [_self_param(node.name)]
        saved_locals = dict(self.locals)
        self.locals = {param["name"]: param["type"] for param in params}
        self.locals[computed] = ret
        try:
            body = [
                _if_node(
                    flag,
                    [_return_node(value, span)],
                    [
                        _assign_node(computed, getter, span),
                        _setattr_node(self_n, value_slot, _name(computed, ret, span), span),
                        _setattr_node(self_n, flag_slot, _const_bool(True, span), span),
                        _return_node(_name(computed, ret, span), span),
                    ],
                    span,
                ),
            ]
            return self._method_ir(f"__cached_{name}", params, ret, body, span)
        finally:
            self.locals = saved_locals

    def _dataclass_post_init(
        self, node: ast.ClassDef, info: dict[str, Any], span: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The `__post_init__` call the generated init ends with, or None.

        CPython's `@dataclass` synthesizes an `__init__` that calls
        `self.__post_init__()` when the class (or an ancestor) defines
        one (issue #452). The call is static: the definer's own body
        runs, so the hook reads and writes the instance fields the way
        CPython's does. A class that defines no `__post_init__` anywhere
        in its MRO gets no call.
        """
        owner = self._definer(node.name, "__post_init__")
        if owner is None:
            return None
        emit = self._emit_owner(node.name, "__post_init__")
        sig = self.classes[owner]["methods"]["__post_init__"]
        if sig[1] is not None and _type_kind(sig[1]) != "none":
            self.fail(node, "PS1003", "__post_init__ returns None")
        self_type = _class_type(node.name)
        return _expr_stmt_node(
            _function_call(f"cls.{emit}.__post_init__", [_name("self", self_type, span)], _leaf_type("none"), span),
            span,
        )

    def _initless_field_store(
        self, node: ast.ClassDef, field: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """The store a `field(init=False)` field gets in the generated init.

        CPython's `init=False` field with a default is not a constructor
        parameter, but the generated `__init__` still writes the default
        into the instance (issue #452). A factory field calls the factory
        there, so every instance gets its own value. A field with no
        default at all stays unset in CPython (the read raises
        `AttributeError`); the lowered struct always has the slot, so
        that shape fences instead of answering a wrong value.
        """
        self_type = _class_type(node.name)
        target = _name("self", self_type, span)
        if "factory" in field:
            marker = field["factory"]
            saved_prelude = self.prelude
            self.prelude = []
            try:
                value = self._factory_default(marker, field["type"], node)
            finally:
                inner = self.prelude
                self.prelude = saved_prelude
            return [*inner, _setattr_node(target, field["name"], value, span)]
        default = field.get("default")
        if default is None:
            self.fail(node, "PS2002", "field(init=False) needs a default")
        if field.get("optdefault"):
            value = self._opt_box(default, field["type"], span)
        else:
            value = default
        return [_setattr_node(target, field["name"], value, span)]

    def _generated_method(self, node: ast.ClassDef, info: dict[str, Any]) -> dict[str, Any] | None:
        mode = info.get("generated_init")
        if mode is None:
            return None
        params, ret = info["methods"]["__init__"]
        span = span_of(node, self.filename)
        self_type = _class_type(node.name)
        self.locals = {param["name"]: param["type"] for param in params}
        if mode == "dataclass":
            # The dataclass base (issue #452): the base's fields are the
            # instance's leading slots, so the generated init writes them
            # before the child's own, the order the layout declares.
            body: list[dict[str, Any]] = []
            param_names = {param["name"] for param in params}
            for field in info.get("declared_fields", info["own_fields"]):
                if field["name"] not in param_names:
                    # A `field(init=False)` field (issue #452): no
                    # parameter exists, so the init writes the default
                    # (the Optional cell for `None`) or calls the factory.
                    body.extend(self._initless_field_store(node, field, span))
                    continue
                body.append(
                    _setattr_node(
                        _name("self", self_type, span),
                        field["name"],
                        _name(field["name"], field["type"], span),
                        span,
                    )
                )
            post = self._dataclass_post_init(node, info, span)
            if post is not None:
                body.append(post)
        elif mode == "const-seed":
            # The seeding init: the class body's constant attributes fill
            # the instance slots before any method writes them (issue
            # #444).
            body = self._seed_const_fields(node.name, info, [], span)
        else:
            parent = info.get("init_target") or info.get("base")
            args = [_name(param["name"], param["type"], span) for param in params]
            body = [_expr_stmt_node(_function_call(f"cls.{parent}.__init__", args, _leaf_type("none"), span), span)]
        return self._method_ir("__init__", params, ret, body, span)

    def _generated_repr(self, node: ast.ClassDef, info: dict[str, Any]) -> dict[str, Any] | None:
        """The dataclass `__repr__`: `ClassName(field=..., ...)` as one str.

        CPython's `@dataclass` synthesizes `__repr__` from the field list,
        so `print(P(1))` on a dataclass prints `P(x=1)` (issue #1246). The
        generated body concatenates the class name with each field's
        `name=` and repr (`ps_str_format` for the scalars, the container
        repr rows for list/dict/set); a field whose repr is outside the
        printable set keeps the `print` fence instead of emitting a wrong
        string. A written `__repr__` or `__str__` wins, as in CPython.
        A field spelled `field(repr=False)` (issue #452) drops out of the
        body, the way CPython's `repr` flag drops it.
        """
        mode = info.get("generated_init")
        if mode != "dataclass" or "__repr__" in info["methods"] or "__str__" in info["methods"]:
            return None
        fields = [field for field in info.get("declared_fields", info["own_fields"]) if field.get("repr", True)]
        if any(not self._reprable_field(field["type"]) for field in fields):
            return None
        span = span_of(node, self.filename)
        self_type = _class_type(node.name)
        parts: list[dict[str, Any]] = [_const_str_node(f"{node.name}(", span)]
        for index, field in enumerate(fields):
            if index > 0:
                parts.append(_const_str_node(", ", span))
            parts.append(_const_str_node(f"{field['name']}=", span))
            read = _attr_node(_name("self", self_type, span), field["name"], field["type"], span)
            rendered = self._field_repr(read, field["type"], node, span)
            if rendered is None:
                return None
            parts.append(rendered)
        parts.append(_const_str_node(")", span))
        body = [_return_node(_concat_str(parts, span), span)]
        params = [_self_param(node.name)]
        self.locals = {param["name"]: param["type"] for param in params}
        info["methods"]["__repr__"] = (params, _leaf_type("str"))
        info["method_kind"]["__repr__"] = "method"
        info["method_owner"]["__repr__"] = node.name
        return self._method_ir("__repr__", params, _leaf_type("str"), body, span)

    def _generated_eq(self, node: ast.ClassDef, info: dict[str, Any]) -> dict[str, Any] | None:
        """The dataclass `__eq__`: fieldwise equality, identity if equal.

        CPython's `@dataclass` synthesizes `__eq__` as `other is self or
        self._fields == other._fields` (issue #1279), so `C(1) == C(1)` is
        True where the identity fallback answered False. The generated body
        is the identity compare plus one short-circuiting chain over the
        field pairs; each field compare recurses through `_compare_values`,
        so a container or nested-dataclass field compares by value. A
        written `__eq__` wins, as in CPython; `eq=False` on the decorator
        is a different decorator spelling and never reaches this class
        lowering. A field spelled `field(compare=False)` (issue #452)
        drops out of the chain, the way CPython's `compare` flag drops it.
        """
        if not info.get("generated_eq"):
            return None
        fields = [field for field in info.get("declared_fields", info["own_fields"]) if field.get("compare", True)]
        if any(not self._eq_comparable_field(field["type"], current=node.name) for field in fields):
            return None
        span = span_of(node, self.filename)
        self_type = _class_type(node.name)
        params = info["methods"]["__eq__"][0]
        self.locals = {param["name"]: param["type"] for param in params}
        self_n = _name("self", self_type, span)
        other_n = _name("other", self_type, span)
        bool_t = _leaf_type("bool")
        res = self._temp("eq")
        self.locals[res] = bool_t
        res_n = _name(res, bool_t, span)
        field_stmts: list[dict[str, Any]] = []
        for field in fields:
            f_self = _attr_node(self_n, field["name"], field["type"], span)
            f_other = _attr_node(other_n, field["name"], field["type"], span)
            saved = self.prelude
            self.prelude = []
            try:
                pair = self._compare_values(node, "==", f_self, f_other, span)
            finally:
                inner = self.prelude
                self.prelude = saved
            cond = self._truthy(res_n, span)
            field_stmts.append(_if_node(cond, [*inner, _assign_node(res, pair, span)], [], span))
        body = [
            # `other is self` answers True without reading a field; the
            # chain below runs only for two distinct instances.
            _assign_node(res, _const_bool(True, span), span),
            _if_node(_compare_node("is not", self_n, other_n, span), field_stmts, [], span),
            _return_node(res_n, span),
        ]
        return self._method_ir("__eq__", params, bool_t, body, span)

    def _eq_comparable_field(
        self, typ: dict[str, Any], seen: frozenset[str] | None = None, current: str | None = None
    ) -> bool:
        """Whether a field type has an `==` this lowering can spell.

        The scalars, the containers, and a nested dataclass (whose own
        generated `__eq__` the body calls) are comparable; anything else
        keeps the identity fallback. `current` names the class whose
        `__eq__` is being decided, so a self-referencing field (a tree of
        nodes) stays comparable: the body calls the class's own `__eq__`
        and the recursion ends on the data, not on the types.
        """
        seen = seen or frozenset()
        kind = _type_kind(typ)
        if kind in {"int", "float", "str", "bool", "bytes", "none"}:
            return True
        if kind == "list":
            elem = typ.get("elem")
            return elem is not None and self._eq_comparable_field(elem, seen, current)
        if kind == "dict":
            key = typ.get("key")
            value = typ.get("value")
            return (
                key is not None
                and value is not None
                and self._eq_comparable_field(key, seen, current)
                and self._eq_comparable_field(value, seen, current)
            )
        if kind == "class":
            class_name = str(typ["name"])
            if class_name == current:
                return True
            class_info = self.classes.get(class_name)
            if class_info is None:
                # A class the collect pass has not reached yet: it is a
                # forward reference, and its own pass decides whether it
                # gets an `__eq__`.
                return False
            if class_info.get("generated_eq"):
                return True
            if class_info.get("generated_init") == "dataclass":
                return True
            if class_info.get("methods", {}).get("__eq__") is not None:
                return class_info.get("method_kind", {}).get("__eq__") == "method"
            return False
        return False

    def _reprable_field(self, typ: dict[str, Any], seen: frozenset[str] | None = None) -> bool:
        """Whether a field type has a repr this lowering can spell.

        The scalars, the containers whose element type is a scalar, and a
        nested dataclass (whose own `__repr__` the generated body calls)
        are printable; anything else keeps the `print` fence. `seen` holds
        the classes on the way in, so a cyclic class graph stops instead
        of recursing forever.
        """
        seen = seen or frozenset()
        kind = _type_kind(typ)
        if kind in {"int", "float", "str", "bool", "bytes", "none"}:
            return True
        if kind == "list":
            elem = typ.get("elem")
            return elem is not None and self._reprable_field(elem, seen)
        if kind == "dict":
            return type_spec(typ, self.classes) is not None
        if kind == "class":
            return self._nested_repr_callable(typ["name"], seen)
        return False

    def _nested_repr_callable(self, class_name: str, seen: frozenset[str] = frozenset()) -> bool:
        """Whether a nested class field's own `__repr__` the generated body
        can call: a dataclass with a printable field set, or a class that
        wrote its own `__repr__`."""
        info = self.classes.get(class_name)
        if info is None:
            return False
        if info.get("methods", {}).get("__repr__") is not None:
            return info.get("method_kind", {}).get("__repr__") == "method"
        if info.get("generated_init") != "dataclass" or class_name in seen:
            return False
        nested = frozenset({*seen, class_name})
        return all(self._reprable_field(field["type"], nested) for field in info.get("own_fields", []))

    def _field_repr(
        self, read: dict[str, Any], typ: dict[str, Any], node: ast.AST, span: dict[str, Any]
    ) -> dict[str, Any] | None:
        """One field's repr text: the scalars ride `%r`, containers ride the
        `ps_seq_repr`/`ps_map_repr` rows, a nested dataclass rides its own
        `__repr__`, None is the literal `None`."""
        kind = _type_kind(typ)
        if kind == "none":
            return _const_str_node("None", span)
        if kind in {"int", "float", "str", "bool", "bytes"}:
            fmt = _const_str_node("%r", span)
            expr = _call_node(
                {"kind": "module", "module": "builtins", "name": "format"},
                [fmt, read],
                _leaf_type("str"),
                span,
            )
            expr["symbol"] = "ps_str_format"
            expr["format_style"] = "percent"
            return expr
        if kind == "list":
            elem = typ.get("elem")
            spec = type_spec(elem, self.classes) if elem is not None else None
            if spec is None:
                return None
            is_set = _const_int_node(1 if typ.get("set") else 0, span)
            expr = _call_node(
                {"kind": "module", "module": "builtins", "name": "seq_repr"},
                [read, _const_str_node(spec, span), is_set],
                _leaf_type("str"),
                span,
            )
            expr["symbol"] = "ps_seq_repr"
            return expr
        if kind == "dict":
            spec = type_spec(typ, self.classes)
            if spec is None:
                return None
            expr = _call_node(
                {"kind": "module", "module": "builtins", "name": "map_repr"},
                [read, _const_str_node(spec, span)],
                _leaf_type("str"),
                span,
            )
            expr["symbol"] = "ps_map_repr"
            return expr
        if kind == "class":
            if not self._nested_repr_callable(typ["name"]):
                return None
            return _function_call(f"cls.{typ['name']}.__repr__", [read], _leaf_type("str"), span)
        return None

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
                    bound = self._typevar_bound(tv, node)
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
        # The bound resolves lazily (issue #412): a `bound=P` spelling may
        # name a class the module declares below the TypeVar, so holding the
        # annotation node and lowering it on first read lets the class
        # collection that runs before the signature pass fill the table.
        bound_node: ast.expr | None = None
        for kw in call.keywords:
            if kw.arg == "bound":
                bound_node = kw.value
            elif kw.arg in {"covariant", "contravariant", "infer_variance"}:
                continue
            else:
                self.fail(kw.value, "PS2003", f"TypeVar keyword {kw.arg} is not lowered")
        self.typevars[target] = None
        self.typevar_bounds[target] = bound_node
        self.declared.add(target)

    def _typevar_bound(self, tv: str, node: ast.AST) -> dict[str, Any] | None:
        """The bound type of `tv`, resolved once per module pass.

        The first read lowers the stored annotation node (a class named
        after the TypeVar resolves then); the resolved type is cached on
        `typevars[tv]`, the shape every reader already consults."""
        bound = self.typevars.get(tv)
        if bound is not None:
            return bound
        bound_node = self.typevar_bounds.get(tv)
        if bound_node is None:
            return None
        typ = self._annotation(bound_node)
        bound = typ if _type_kind(typ) != "typevar" else None
        self.typevars[tv] = bound
        return bound

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
            bound = self._typevar_bound(tv, node)
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
        inst["declared_fields"] = [
            {**field, "type": self._subst(field["type"], mapping, node)}
            for field in inst.get("declared_fields", inst["own_fields"])
        ]
        for field in inst["fields"] + inst["own_fields"] + inst["declared_fields"]:
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

    def _ndarray_attr(self, obj: dict[str, Any], name: str, span: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        """The ndarray attribute surface (issue #496): the metadata reads
        call the runtime, and any other name fences by name."""
        if name == "ndim":
            return self._abi_call("pyscriptc_ndarray", "ndim", [obj], {"kind": "int"}, "ps_nd_ndim", span)
        if name == "size":
            return self._abi_call("pyscriptc_ndarray", "size", [obj], {"kind": "int"}, "ps_nd_size", span)
        if name == "nbytes":
            return self._abi_call("pyscriptc_ndarray", "nbytes", [obj], {"kind": "int"}, "ps_nd_nbytes", span)
        if name == "dtype":
            # The dtype is static: it rides on the array's IR type, so the
            # attribute is the name, not a runtime read. numpy spells the
            # dtype as a type object, but the subset gives the name.
            return _const_str(_nd_dtype_of(obj["type"]), span)
        if name == "shape":
            return self._abi_call(
                "pyscriptc_ndarray", "shape", [obj], {"kind": "list", "elem": {"kind": "int"}}, "ps_nd_shape", span
            )
        self.fail(
            node,
            "PS2001",
            f"pyscriptc_ndarray.{name} is not in the lowered subset of pyscriptc_ndarray",
        )
        raise AssertionError("unreachable")

    def _http_handler_attr(
        self, node: ast.AST, obj: dict[str, Any], name: str, span: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The handler instance's request-backed attribute, or None.

        `self.path` reads the request's target, `self.headers` the parsed
        header block, `self.rfile` the request's body reader and
        `self.wfile` the response's body writer (issue #1270, slice 2).
        Each one is a runtime call over the `_request` or `_response`
        record the accept loop parked on the instance, so a read lowers
        here and never lands in the field table.

        The handler's `self` is an ordinary class instance, so a plain
        name never reaches this: only the four request-backed attributes
        route through.
        """
        from pyscriptc.restlower._httpserver import handler_attr

        if not self.is_http_handler_instance(obj["type"].get("name")):
            return None
        return handler_attr(self, node, span, obj)

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
            bound = obj["type"].get("name")
            if bound:
                # `except C as e` over a user exception class (issue #448):
                # the runtime record carries the class instance as its
                # payload, so the custom fields read through it. The read is
                # a normal attribute read on the payload pointer: the emitter
                # knows the class layout statically, so the field keeps its
                # own type. Methods and the builtin attributes stay fenced:
                # the payload is the record the raise site built, nothing
                # else.
                target = self.class_aliases.get(bound, bound)
                info = self.classes.get(target)
                if info is not None:
                    for field in info["fields"]:
                        if field["name"] == name:
                            return _attr_node(obj, name, field["type"], span)
                    # A class-body constant the class declares (`tag: str =
                    # "sub"`): CPython's instance read falls back to the
                    # class attribute (issue #444's rule), and the payload
                    # is that class's instance, so the constant reads
                    # through it the same way an ordinary instance read
                    # does.
                    const = self._class_const(target, name)
                    if const is not None:
                        return {**const, "span": span}
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
            # A manifest may declare read-only getters for a handle
            # (issue #1343): the read is one thunk call over the pointer.
            attr = self._ffi_handle_attr(obj, class_name, name, span, node)
            if attr is not None:
                return attr
            self.fail(node, "PS4015", f"{class_name} is an opaque ffi handle and has no attributes")
        if class_name == "ndarray" and not obj["type"].get("handle"):
            # The ndarray attribute surface (issue #496): the metadata reads
            # call the runtime; any other name fences.
            return self._ndarray_attr(obj, name, span, node)
        if class_name == "Semaphore" and name == "_value":
            return self._abi_call("threading", "Semaphore._value", [obj], _leaf_type("int"), "ps_sem_value", span)
        if class_name == "AsyncSemaphore" and name == "_value":
            # The asyncio semaphore's counter, the same shape the threading
            # one has: an opaque pointer with a read-only count.
            return self._abi_call("asyncio", "Semaphore._value", [obj], _leaf_type("int"), "ps_asem_value", span)
        if class_name == "AsyncProcess" and name == "returncode":
            # The async subprocess's exit code (issue #1224): the runtime
            # owns the child, so the read is a call into the record. The
            # code is -1 while the child still runs, where CPython's
            # returncode is None.
            return self._abi_call("asyncio", "Process.returncode", [obj], _leaf_type("int"), "ps_aproc_code_of", span)
        if class_name in {"Popen", "PopenText"} and name in {"pid", "returncode", "stdout", "stderr", "stdin"}:
            # The sync child handle's surface (issue #1345): the pid is a
            # read into the record, the return code is the tagged
            # `int | None` (None is the child that still runs, CPython's
            # exact shape), and the three stream names hand back the pipe
            # handle over the child (the same handle every spelling
            # reads, the buffers live on the child). The stream handles
            # are never None here, where CPython answers None for an
            # unpiped stream.
            if name == "pid":
                return self._abi_call("subprocess", "Popen.pid", [obj], _leaf_type("int"), "ps_popen_pid", span)
            if name == "returncode":
                return _reaped_fill(self, obj, span, node)
            stream = {"stdout": 0, "stderr": 1, "stdin": 2}[name]
            return self._abi_call(
                "subprocess",
                f"Popen.{name}",
                [obj, _const_int_node(stream, span)],
                _class_type("PopenPipeText" if class_name == "PopenText" else "PopenPipe"),
                "ps_popen_pipe",
                span,
            )
        if class_name == "Thread" and name == "name":
            return self._abi_call("threading", "Thread.name", [obj], _leaf_type("str"), "ps_thread_name2", span)
        if class_name == "Thread" and name == "daemon":
            return self._abi_call("threading", "Thread.daemon", [obj], _leaf_type("bool"), "ps_thread_daemon", span)
        info = self.classes.get(class_name)
        if info is None:
            self.fail(node, "PS2001", f"{class_name} has no fields")
        # The http.server handler's request-backed attributes (issue #1270,
        # slice 2): `self.path`, `self.headers`, `self.rfile` and
        # `self.wfile` read the request and response records the accept
        # loop parked on the instance, so they are runtime calls and not
        # plain field reads. The hook runs before the field table: the
        # base class declares the four names so the checker sees them, but
        # their values live in the runtime records, not in the instance.
        handler_attr = self._http_handler_attr(node, obj, name, span)
        if handler_attr is not None:
            return handler_attr
        # The http.server server's bound-address attributes (issue #1270,
        # slice 2): `server.server_port` and `server.server_address` are
        # runtime reads over the server record, not instance fields.
        if class_name == "HTTPServer":
            from pyscriptc.restlower._httpserver import server_attr

            server_hook = server_attr(self, node, span, obj)
            if server_hook is not None:
                return server_hook
        # The asyncio server's bound socket (issue #1281, slice 2):
        # `server.sockets` is a runtime read over the server record, not an
        # instance field.
        if class_name == "Server":
            from pyscriptc.restlower._asyncio_server import server_attr as asyncio_server_attr

            asyncio_hook = asyncio_server_attr(self, node, span, obj)
            if asyncio_hook is not None:
                return asyncio_hook
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
            if const.get("kind") == "tuple-const":
                return self._materialize_tuple(const["elems"], span)
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
        if info.get("method_kind", {}).get(name) == "cached_property":
            # The cached property's read (issue #440): the accessor method
            # owns the first-read compute and every later read answers the
            # instance slot, the way CPython's descriptor does.
            owner = self._emit_owner(class_name, name)
            sig = info["methods"][name]
            return _function_call(f"cls.{owner}.__cached_{name}", [obj], sig[1], span)
        hook = self._getattr_hook(class_name)
        if hook is not None:
            return self._getattr_hook_call(hook, obj, name, span, node, expect)
        attr = path_attr(name) if class_name == "Path" else None
        if attr is not None:
            typ, symbol = attr
            return self._abi_call("pathlib", name, [obj], typ, symbol, span)
        if synth_method(class_name, name) is not None:
            self.fail(node, "PS2002", f"{class_name}.{name} is a function and is not a value")
        # A classmethod body's `cls.<method>` reads the bound method off the
        # class object (issue #447). The static answer is the closure over
        # the emitted method record; a classmethod's record already takes
        # the class id, an instance method's takes the implicit self.
        sig = info["methods"].get(name)
        if sig is not None and info.get("method_kind", {}).get(name) in {"class", "static", "method"}:
            from pyscriptc.lower._expr import _closure_node

            owner = info.get("method_owner", {}).get(name, class_name)
            kind = info["method_kind"].get(name)
            params = [param["type"] for param in sig[0][1:]] if kind == "class" else [param["type"] for param in sig[0]]
            return _closure_node(f"cls.{owner}.{name}", {"kind": "func", "params": params, "return": sig[1]}, span)
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
