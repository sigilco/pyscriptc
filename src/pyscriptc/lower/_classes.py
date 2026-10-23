"""Class lowering and typing-annotation machinery.

`ClassesMixin` collects `class` statements (dataclasses, enums, synthetic
tuple/deque/OrderedDict shapes), lowers method signatures, and resolves
typing annotations. Moved verbatim from `lower.py` via `lower/__init__.py`;
no signature or behavior change.
"""

from __future__ import annotations

import ast
import copy
from typing import Any, NoReturn

from pyscriptc import coro
from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS
from pyscriptc.coro import enabled
from pyscriptc.errors import span_of
from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._core import (
    _DICT_FORMS,
    _LIST_FORMS,
    _SET_FORMS,
    Fence,
    _assign,
    _has_typevar,
    _name,
    _plain_const_kind,
    _show,
    _sig_typevars,
    _subst_type,
)
from pyscriptc.lower._stmt import _always_returns, _is_docstring
from pyscriptc.restlower import alias_name, path_attr
from pyscriptc.restlower import method_spec as synth_method
from pyscriptc.stdmore import (
    ALWAYS_CLASSES,
    CLASS_FIELDS,
    DEFERRED_CLASSES,
    RESERVED_CODE_EXCEPTIONS,
    SYNTHETIC_EXCEPTIONS,
)

_UNWRAP_FORMS = frozenset({"Final", "ClassVar", "Annotated", "Required", "NotRequired"})
_DYNAMIC_ANNOTATIONS = frozenset({"object", "Any", "Protocol", "TypeVar", "Generic", "Callable", "ABC"})
# Tier-2 erasure (`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`):
# static-typing constructs that vanish before emission. `abc` is an erased
# module, so these names arrive through `type_aliases` or an erased
# `_resolve_module` triple.
_ERASED_BASES = frozenset({"ABC", "ABCMeta", "Protocol"})
_ERASED_DECORATORS = frozenset({"abstractmethod", "overload"})
_IO_HANDLES = {"BinaryIO": "BytesIO", "TextIO": "TextIO"}


class ClassesMixin:
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
                {"name": "pattern", "type": {"kind": "str"}},
                {"name": "flags", "type": {"kind": "int"}},
            ]
        if "Match" in self.synthetics:
            # %cls.Match layout mirrors the runtime PsMatch: the subject
            # text plus a flat i64 span list, -1 for groups that did not take part.
            specs["Match"] = [
                {"name": "_text", "type": {"kind": "str"}},
                {"name": "_spans", "type": {"kind": "list", "elem": {"kind": "int"}}},
            ]
        if "SplitResult" in self.synthetics:
            specs["SplitResult"] = [
                {"name": name, "type": {"kind": "str"}} for name in ("scheme", "netloc", "path", "query", "fragment")
            ]
        if "HTTPError" in self.synthetics:
            specs["HTTPError"] = CLASS_FIELDS["HTTPError"]
        if "URLError" in self.synthetics:
            specs["URLError"] = CLASS_FIELDS["URLError"]
        for name in self.synthetics:
            if name in CLASS_FIELDS:
                specs[name] = CLASS_FIELDS[name]
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
            if resolved["kind"] != "class" and len(node.bases) > 1:
                self.fail(node, "PS2002", "multiple inheritance needs lowered class bases")
            bases.append(resolved)
        base = bases[0] if bases else None
        if deco == "dataclass" and base is not None:
            self.fail(node, "PS2002", "a dataclass has no base")
        if base is not None and base["kind"] == "enum":
            self._collect_enum(node, str(base["name"]))
            return
        self.declared.add(node.name)
        parent_name = base["name"] if base is not None else None
        parent = self.classes.get(parent_name) if parent_name and base and base["kind"] == "class" else None
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
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None and deco != "dataclass":
                        self.fail(stmt, "PS1204", "class fields are declared with an annotation and no value")
                    if stmt.target.id in seen or stmt.target.id in inherited_types:
                        self.fail(stmt, "PS2002", f"duplicate field {stmt.target.id}")
                    typ = self._annotation(stmt.annotation)
                    if typ["kind"] == "bool":
                        self.fail(stmt, "PS2002", "bool fields are not lowered")
                    if typ["kind"] == "func":
                        self.fail(stmt, "PS2002", "callable fields are not lowered")
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
                    kind = self._method_decorator(stmt)  # type: ignore[arg-type]
                    if stmt.name == "__init__" and kind != "method":
                        self.fail(stmt, "PS1201", "decorators are not lowered")
                    methods.append((stmt, kind))  # type: ignore[arg-type]
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
                    sig = ([{"name": "self", "type": {"kind": "class", "name": node.name}}, *sig[0][1:]], sig[1])
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
        generated: str | None = None
        init_target: str | None = None
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
                    [{"name": "self", "type": {"kind": "class", "name": node.name}}, *method_sigs["__init__"][0][1:]],
                    {"kind": "none"},
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
        exception = bool(base and (base["kind"] == "exception" or (parent and parent.get("exception"))))
        info: dict[str, Any] = {
            "fields": [*inherited, *fields],
            "own_fields": fields,
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
        self.classes[node.name] = info

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
        if want is not None and ret["kind"] != want:
            self.fail(node, "PS1003", f"{class_name}.{name} returns {_show(ret)}, not {want}")
        emit = self._emit_owner(class_name, name)
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{emit}.{name}"},
            "args": [receiver, *args],
            "type": ret,
            "span": span,
        }

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
        # Erased typing decorators (`@abstractmethod`) drop out first, so
        # `@property @abstractmethod` still resolves to a property.
        decos = [d for d in fn.decorator_list if self._erased_deco_name(d) is None]
        if not decos:
            return "method"
        if len(decos) != 1:
            self.fail(fn, "PS1201", "decorators are not lowered")
        dec = decos[0]
        target = dec
        if isinstance(dec, ast.Call):
            if dec.args or dec.keywords:
                self.fail(fn, "PS1201", "decorators are not lowered")
            target = dec.func
        if isinstance(target, ast.Name) and target.id in {"property", "staticmethod", "classmethod"}:
            return {"property": "property", "staticmethod": "static", "classmethod": "class"}[target.id]
        if self._is_contextmanager_deco(fn) and enabled():
            # `@contextmanager`: the coro pass owns it; the method still
            # lowers as a plain method (the body is the resume body).
            return "method"
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

    def _erased_base_name(self, node: ast.expr) -> str | None:
        """`ABC`/`ABCMeta`/`Protocol` base through an erased import, or None."""
        member: str | None = None
        if isinstance(node, ast.Name):
            member = self.type_aliases.get(node.id)
        elif isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[3] == "erased":
                member = resolved[2]
        return member if member in _ERASED_BASES else None

    def _is_abcmeta_keyword(self, kw: ast.keyword) -> bool:
        """`metaclass=ABCMeta` — erased like the `ABC` base it is sugar for."""
        if kw.arg != "metaclass":
            return False
        value = kw.value
        if isinstance(value, ast.Name):
            return self.type_aliases.get(value.id) == "ABCMeta"
        if isinstance(value, ast.Attribute):
            resolved = self._resolve_module(value)
            return resolved is not None and resolved[3] == "erased" and resolved[2] == "ABCMeta"
        return False

    def _erased_deco_name(self, dec: ast.expr) -> str | None:
        """`abstractmethod`/`overload` through an erased import, or None."""
        member: str | None = None
        if isinstance(dec, ast.Name):
            member = self.type_aliases.get(dec.id)
        elif isinstance(dec, ast.Attribute):
            resolved = self._resolve_module(dec)
            if resolved is not None and resolved[3] == "erased":
                member = resolved[2]
        return member if member in _ERASED_DECORATORS else None

    def _declaration_only(self, fn: ast.FunctionDef) -> bool:
        """The body is only a docstring, `pass`, or `...` — a declaration, not an implementation."""
        for stmt in fn.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
                continue
            return False
        return True

    def _accepts_protocol(self, want: dict[str, Any], got: dict[str, Any]) -> bool:
        """A Protocol-annotated slot accepts any subclass of the erased class:
        the marker carries no fields, so every record below it already
        satisfies its layout. A `gen.` frame class is accepted where an
        `Iterator[T]`-shaped annotation (the synthetic `iter_` cursor) is
        expected: the frame's `__next__` return type must equal the
        annotation's element, and the caller narrows the slot to the frame."""
        if want["kind"] != "class" or got["kind"] != "class":
            return False
        want_name = want.get("name")
        got_name = got.get("name")
        if not isinstance(want_name, str) or not isinstance(got_name, str):
            return False
        if self.classes.get(want_name, {}).get("iterator") and want.get("elem") is not None:
            if not got_name.startswith("gen."):
                return False
            frame = self.classes.get(got_name)
            if frame is None or frame.get("coro") != "generator":
                return False
            next_ret = frame["methods"].get("__next__", (None, None))[1]
            return next_ret is not None and types_equal(next_ret, want["elem"])
        if not self.classes.get(want_name, {}).get("protocol"):
            return False
        return self._is_subclass_name(got_name, want_name)

    def _narrows_to_generator(self, want: dict[str, Any], got: dict[str, Any]) -> bool:
        """True when an iterator-shaped annotation holds a generator frame.

        The synthetic `iter_` cursor is a materialized list with an index;
        a generator frame is a lazy record with `__iter__`/`__next__`. When
        the value is a frame, the slot keeps the frame type so `next()`,
        `send()` and `for` dispatch on the real shape.
        """
        if want.get("kind") != "class" or got.get("kind") != "class":
            return False
        want_name = want.get("name")
        got_name = got.get("name")
        if not isinstance(want_name, str) or not isinstance(got_name, str):
            return False
        if not self.classes.get(want_name, {}).get("iterator"):
            return False
        frame = self.classes.get(got_name)
        if frame is None or frame.get("coro") != "generator":
            return False
        next_ret = frame["methods"].get("__next__", (None, None))[1]
        return next_ret is not None and types_equal(next_ret, want.get("elem"))

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
        next_auto = 1
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            try:
                target, value = self._enum_assign(stmt)
                auto = self._enum_auto(value)
                if auto is None:
                    number = _const_int_index(value)
                    if number is None:
                        self.fail(stmt, "PS2002", "an enum member is an integer constant")
                else:
                    if not auto:
                        self.fail(stmt, "PS2003", "enum.auto takes no arguments")
                    number = next_auto
                next_auto = number + 1
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

    def _enum_auto(self, value: ast.expr) -> bool | None:
        """True when the value is a bare `auto()` call with no arguments.

        False means an auto() call with arguments; None means not auto().
        Both the `from enum import auto` binding and the `enum.auto` dotted
        spelling resolve onto the enum module table.
        """
        if not isinstance(value, ast.Call):
            return None
        func = value.func
        if isinstance(func, ast.Name):
            binding = self.bindings.get(func.id)
            if binding is None or binding.module != "enum" or binding.attr != "auto":
                return None
        elif isinstance(func, ast.Attribute):
            resolved = self._resolve_module(func)
            if resolved is None or resolved[1] != "enum" or resolved[2] != "auto":
                return None
        else:
            return None
        return not value.args and not value.keywords

    def _enum_assign(self, stmt: ast.stmt) -> tuple[str, ast.expr]:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            return stmt.targets[0].id, stmt.value
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            return stmt.target.id, stmt.value
        self.fail(stmt, "PS2002", "an enum body is integer constants")
        raise AssertionError("unreachable")

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
        self.fn_globals = self._globals_for(stmt.body)
        params, ret = info["methods"][stmt.name]
        kind = info["method_kind"].get(stmt.name, "method")
        if params and params[0]["name"] == "self":
            params = [{"name": "self", "type": {"kind": "class", "name": class_name}}, *params[1:]]
        # A generator method's body lowers under the None-returning resume
        # signature; the outward frame type rides in the method signature.
        params, ret = self._coro_method_body_sig(class_name, stmt.name, params, ret)
        self.locals = {param["name"]: param["type"] for param in params}
        self.frozen_names = set()
        self.fn_body = list(stmt.body)
        if kind in {"method", "property"}:
            self.locals["self"] = {"kind": "class", "name": class_name}
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
        if ret["kind"] != "none" and not _always_returns(body):
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
            parent = info.get("init_target") or info["base"]
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
        # `**kwargs` capture stays lowered (packed into dict[str, T] by
        # `_fill_varargs`); `/`-positional and `*`-keyword parameters normalize
        # into the named-parameter machinery instead: positional-only
        # parameters refuse keywords, keyword-only ones must be passed by
        # name. See `_fill_args`/`_fill_varargs` for the binding rules.
        pos_marks = [True] * len(fn.args.posonlyargs) + [False] * len(fn.args.args)
        args = list(fn.args.posonlyargs) + list(fn.args.args)
        params: list[dict[str, Any]] = []
        if kind == "property":
            if fn.args.vararg is not None or fn.args.kwarg is not None or len(args) != 1 or args[0].arg != "self":
                self.fail(fn, "PS2002", "a property's only parameter is self")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "self is not annotated")
            if class_name is None:
                self.fail(fn, "PS1201", "decorators are not lowered")
            params.append({"name": "self", "type": {"kind": "class", "name": class_name}})
            args = []
            pos_marks = []
        elif kind == "class":
            if not args or args[0].arg != "cls":
                self.fail(fn, "PS2002", "a classmethod's first parameter must be cls")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "cls is not annotated")
            # cls is not a value. Class.method(...) does not pass the class object.
            args = args[1:]
            pos_marks = pos_marks[1:]
        elif kind == "static":
            pass
        elif class_name is not None:
            if not args or args[0].arg != "self":
                self.fail(fn, "PS2002", "a method's first parameter must be self")
            if args[0].annotation is not None:
                self.fail(args[0], "PS2002", "self is not annotated")
            params.append({"name": "self", "type": {"kind": "class", "name": class_name}})
            args = args[1:]
            pos_marks = pos_marks[1:]
        defaults = list(fn.args.defaults)
        missing = len(args) - len(defaults)
        for index, arg in enumerate(args):
            param = _param(self, arg, fn)
            if pos_marks[index]:
                param["posonly"] = True
            if index >= missing:
                self._param_default(arg, defaults[index - missing], param)
            params.append(param)
        if fn.args.vararg is not None:
            vararg = fn.args.vararg
            if vararg.annotation is None:
                self.fail(vararg, "PS1001", f"parameter {vararg.arg} needs an annotation")
            elem = self._annotation(vararg.annotation)
            if elem["kind"] == "bool":
                self.fail(vararg, "PS2002", "list[bool] is not lowered")
            params.append({"name": vararg.arg, "type": {"kind": "list", "elem": elem}, "vararg": True})
        for karg, kdef in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
            param = _param(self, karg, fn)
            param["kwonly"] = True
            if kdef is not None:
                self._param_default(karg, kdef, param)
            params.append(param)
        if fn.args.kwarg is not None:
            kwarg = fn.args.kwarg
            if kwarg.annotation is None:
                self.fail(fn, "PS1202", f"{fn.name} uses a parameter form that is not lowered")
            value = self._annotation(kwarg.annotation)
            if value["kind"] == "bool":
                self.fail(kwarg, "PS2002", "dict values of bool are not lowered")
            params.append(
                {
                    "name": kwarg.arg,
                    "type": {"kind": "dict", "key": {"kind": "str"}, "value": value},
                    "kwarg": True,
                }
            )
        if fn.returns is None:
            # Checker bridge: when every return statement's inferred type
            # agrees and the subset can express it, the bridge supplies the
            # missing annotation. Otherwise the fence stands (PS1001,
            # return-inference family).
            bridged = self._bridge_return_type(fn)
            if bridged is None:
                self.fail(fn, "PS1001", f"{fn.name} needs a return annotation")
            ret = bridged
        else:
            ret = self._annotation(fn.returns)
        # A callable return is heap-allocated (`ps_alloc` pair + env), so it
        # escapes the frame safely.
        if class_name is not None and fn.name == "__init__" and ret["kind"] != "none":
            self.fail(fn, "PS1003", "__init__ returns None")
        return params, ret

    def _param_default(self, arg: ast.arg, default_node: ast.expr, param: dict[str, Any]) -> None:
        if param["type"].get("optional"):
            value = self._expr(default_node)
            if value["kind"] != "const":
                self.fail(default_node, "PS2002", "a default must be a constant")
            if value["type"]["kind"] != "none" and not types_equal(value["type"], param["type"]["elem"]):
                self.fail(default_node, "PS1003", f"{arg.arg} is {_show(value['type'])}, not {_show(param['type'])}")
            param["default"] = value
            param["optdefault"] = True
        else:
            value = self._expr(default_node, param["type"])
            self._expect(default_node, value["type"], param["type"], arg.arg)
            if value["kind"] != "const":
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
            want = _subst_type(param["type"], mapping)
            if not _has_typevar(want):
                self._expect(node, arg["type"], want, name)
        return {
            "kind": "call",
            "callee": {"kind": "class", "name": inst},
            "args": args,
            "type": {"kind": "class", "name": inst},
            "span": span,
        }

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
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            self.fail(node, "PS2002", "starred calls are not lowered")
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
            for field, arg in zip(fields, args, strict=True):
                if arg["type"]["kind"] in {"func", "none"}:
                    self.fail(node, "PS2002", f"{name}() field {field} is {_show(arg['type'])}")
                info["fields"].append({"name": field, "type": arg["type"]})
        else:
            for field, arg in zip(info["fields"], args, strict=True):
                self._expect(node, arg["type"], field["type"], f"{name}.{field['name']}")
        typ = {"kind": "class", "name": name}
        dest = self._temp("nt").replace(".", "_")
        self.locals[dest] = typ
        made = {"kind": "call", "callee": {"kind": "class", "name": name}, "args": [], "type": typ, "span": span}
        self.prelude.append(_assign(dest, made, span))
        for field, arg in zip(fields, args, strict=True):
            self.prelude.append(
                {"kind": "setattr", "object": _name(dest, typ, span), "name": field, "value": arg, "span": span}
            )
        return _name(dest, typ, span)

    def _reject_banned_type(self, node: ast.AST, typ: dict[str, Any]) -> None:
        if typ["kind"] == "list" and typ["elem"]["kind"] == "bool":
            self.fail(node, "PS2002", "list[bool] is not lowered")
        if typ["kind"] == "dict":
            if typ["key"]["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "dict keys are int or str")
            if typ["value"]["kind"] == "bool":
                self.fail(node, "PS2002", "dict values of bool are not lowered")

    def _annotation(self, node: ast.AST) -> dict[str, Any]:
        if isinstance(node, ast.Name) and node.id in self.tv_subst:
            return self.tv_subst[node.id]
        if isinstance(node, ast.Name) and node.id in self.typevars:
            bound = self.typevars[node.id]
            if bound is not None:
                return bound
            return {"kind": "typevar", "name": node.id}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            opt = self._optional_pipe(node)
            if opt is not None:
                return opt
            self.fail(node, "PS1101", "union types are not in the IR")
        formed = self._typing_annotation(node)
        if formed is not None:
            return formed
        if isinstance(node, ast.Name) and node.id in {"int", "float", "bool", "str", "bytes"}:
            return {"kind": node.id}
        if isinstance(node, ast.Constant) and node.value is None:
            return {"kind": "none"}
        if isinstance(node, ast.Name) and (
            node.id in EXCEPTIONS or node.id == "BaseException" or self.classes.get(node.id, {}).get("exception")
        ):
            return {"kind": "exc"}
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
        inst = self._generic_alias_annotation(node)
        if inst is not None:
            return inst
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
                bound = typ if typ["kind"] != "typevar" else None
            elif kw.arg in {"covariant", "contravariant"}:
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

    def _generic_alias_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        """`Box[int]` on a user `class Box(Generic[T])`: instantiate the class
        at the argument types; the annotation resolves to `Box_int`."""
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
            return None
        info = self.classes.get(node.value.id)
        if info is None or not info.get("typevars"):
            return None
        elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        args = [self._annotation(elt) for elt in elts]
        name = self._instantiate_class(node.value.id, args, node)
        return {"kind": "class", "name": name}

    def _instantiate_class(self, base: str, arg_types: list[dict[str, Any]], node: ast.AST) -> str:
        """Register (or reuse) `base` specialized at `arg_types` and return the
        emitted class name; the specialized record is queued to lower."""
        info = self.classes[base]
        tvars = info.get("typevars") or []
        if len(arg_types) != len(tvars):
            self.fail(node, "PS1003", f"{base} takes {len(tvars)} type argument(s)")
        mapping: dict[str, dict[str, Any]] = {}
        for tv, typ in zip(tvars, arg_types, strict=True):
            if typ["kind"] == "none":
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
        inst["fields"] = [{**field, "type": _subst_type(field["type"], mapping)} for field in inst["fields"]]
        inst["own_fields"] = [
            {**field, "type": _subst_type(field["type"], mapping)} for field in inst.get("own_fields", [])
        ]
        for field in inst["fields"] + inst["own_fields"]:
            default = field.get("default")
            if default is not None:
                default["type"] = _subst_type(default["type"], mapping)
        inst["methods"] = {
            mname: (
                [{**param, "type": _subst_type(param["type"], mapping)} for param in params],
                _subst_type(ret, mapping),
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
        if op is ast.Div:
            # `p / x` is pathlib's __truediv__/__rtruediv__: joinpath each way.
            if left_name == "Path" and right["type"]["kind"] == "str":
                return self._abi_call(
                    "pathlib", "Path.__truediv__", [left, right], left["type"], "ps_path_joinpath", span
                )
            if left_name == "Path" and right_name == "Path":
                return self._abi_call(
                    "pathlib", "Path.__truediv__", [left, right], left["type"], "ps_path_joinpath_p", span
                )
            if left["type"]["kind"] == "str" and right_name == "Path":
                return self._abi_call(
                    "pathlib", "Path.__rtruediv__", [left, right], right["type"], "ps_path_rjoin", span
                )
        self.fail(node, "PS2004", f"{op.__name__} on {left_name} and {right_name} is not lowered")
        raise AssertionError("unreachable")

    def _lookup_attr(self, obj: dict[str, Any], name: str, span: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        if obj["type"]["kind"] == "exc":
            if name == "args":
                return self._abi_call(
                    "builtins", "exc.args", [obj], {"kind": "list", "elem": {"kind": "str"}}, "ps_exc_args", span
                )
            self.fail(node, "PS2001", f"exception has no attribute {name}")
        if obj.get("symbol") == "ps_exc_class" and obj.get("attrtype") == "type":
            # type(e): the carrier holds the runtime class name. `.__name__`
            # is that name; any other attribute is not in the subset.
            if name == "__name__":
                return obj
            self.fail(node, "PS2001", f"type(e) has no attribute {name}")
        if obj["type"]["kind"] != "class":
            self.fail(node, "PS2001", f"attribute access is not lowered ({name})")
        class_name = obj["type"]["name"]
        if class_name == "Semaphore" and name == "_value":
            return self._abi_call("threading", "Semaphore._value", [obj], {"kind": "int"}, "ps_sem_value", span)
        if class_name == "Thread" and name == "name":
            return self._abi_call("threading", "Thread.name", [obj], {"kind": "str"}, "ps_thread_name2", span)
        if class_name == "Thread" and name == "daemon":
            return self._abi_call("threading", "Thread.daemon", [obj], {"kind": "bool"}, "ps_thread_daemon", span)
        info = self.classes.get(class_name)
        if info is None:
            self.fail(node, "PS2001", f"{class_name} has no fields")
        for field in info["fields"]:
            if field["name"] == name:
                return {"kind": "attr", "value": obj, "name": name, "type": field["type"], "span": span}
        if info.get("method_kind", {}).get(name) == "property":
            sig = info["methods"][name]
            owner = self._emit_owner(class_name, name)
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
            name = self.class_aliases.get(node.id, node.id)
            if self.classes.get(name, {}).get("protocol"):
                self.fail(node, "PS2001", "isinstance on a Protocol is not lowered")
            return {"kind": "class", "name": name}
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
        info = self.classes.get(got)
        if info is None:
            return False
        mro = info.get("mro")
        if mro is not None:
            return want in mro
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

    def _exception_parent_code(self, base: dict[str, str] | None, parent_name: str | None) -> int:
        """The code a user exception class registers as its parent.

        A builtin base contributes its table code. A user base contributes
        that class's own code; the runtime resolves the chain from there.
        """

        if base is not None and base.get("kind") == "exception":
            return EXCEPTIONS[base["name"]]
        if parent_name is not None:
            info = self.classes.get(parent_name)
            code = info.get("exc_code") if info is not None else None
            if isinstance(code, int):
                return code
        return EXCEPTIONS["Exception"]

    def _exception_code_of(self, name: str) -> int | None:
        """The code of one exception class, without its subclasses."""

        if name in EXCEPTIONS:
            return EXCEPTIONS[name]
        if name in RESERVED_EXCEPTIONS:
            return RESERVED_EXCEPTIONS[name]
        info = self.classes.get(name)
        code = info.get("exc_code") if info is not None else None
        return code if isinstance(code, int) else None

    def _exception_codes(self, name: str) -> list[int]:
        found: list[int] = []
        if name in EXCEPTIONS:
            found.append(EXCEPTIONS[name])
        elif name in RESERVED_EXCEPTIONS:
            found.append(RESERVED_EXCEPTIONS[name])
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
        if alias == "Counter":
            # `counter` marks the dict so a `c[k]` read yields 0 on a miss.
            typ = {"kind": "dict", "key": self._annotation(node.slice), "value": {"kind": "int"}, "counter": True}
            self._reject_banned_type(node, typ)
            return typ
        if alias == "defaultdict":
            # `defaultdict` marks the dict so a `d[k]` miss inserts a fresh
            # default (the zero of the value type) before the read.
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "defaultdict annotations are defaultdict[key, value]")
            typ = {
                "kind": "dict",
                "key": self._annotation(node.slice.elts[0]),
                "value": self._annotation(node.slice.elts[1]),
                "defaultdict": True,
            }
            self._reject_banned_type(node, typ)
            return typ
        if alias == "Queue":
            # The queue record holds element-sized slots, so the annotation
            # carries the element type the way a deque annotation does.
            elem = self._annotation(node.slice)
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            return {"kind": "class", "name": "Queue", "elem": elem}
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
            if binding.module == "collections" and binding.attr == "Counter":
                return "Counter"
            if binding.module == "collections" and binding.attr == "defaultdict":
                return "defaultdict"
            if binding.module == "queue" and binding.attr in {"Queue", "LifoQueue", "SimpleQueue"}:
                return "Queue"
            return alias_name(binding.module, binding.attr)
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is None or resolved[3] != "lowered":
                return None
            if resolved[1] == "collections" and resolved[2] == "deque":
                return "Deque"
            if resolved[1] == "collections" and resolved[2] == "OrderedDict":
                return "OrderedDict"
            if resolved[1] == "collections" and resolved[2] == "Counter":
                return "Counter"
            if resolved[1] == "collections" and resolved[2] == "defaultdict":
                return "defaultdict"
            if resolved[1] == "queue" and resolved[2] in {"Queue", "LifoQueue", "SimpleQueue"}:
                return "Queue"
        return None

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
        if alias == "Callable":
            if (
                not isinstance(node.slice, ast.Tuple)
                or len(node.slice.elts) != 2
                or not isinstance(node.slice.elts[0], ast.List)
            ):
                self.fail(node, "PS1001", "Callable annotations are Callable[[args], ret]")
            params = [self._annotation(arg) for arg in node.slice.elts[0].elts]
            ret = self._annotation(node.slice.elts[1])
            return {"kind": "func", "params": params, "return": ret}
        if alias == "Tuple":
            return self._tuple_annotation(node)
        if alias == "Iterable" or alias == "Sequence":
            typ = {"kind": "list", "elem": self._annotation(node.slice)}
            self._reject_banned_type(node, typ)
            return typ
        if alias == "Iterator":
            return self._iter_type(self._annotation(node.slice), node)
        if alias == "Generator":
            return self._generator_annotation(node)
        if alias in _UNWRAP_FORMS:
            inner = node.slice
            if alias == "Annotated":
                if isinstance(inner, ast.Tuple) and inner.elts:
                    inner = inner.elts[0]
                else:
                    self.fail(node, "PS1001", "Annotated needs a type")
            return self._annotation(inner)
        if alias == "Optional":
            return self._opt_type(self._annotation(node.slice), node)
        if alias == "Union":
            parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if len(parts) == 2:
                for i, part in enumerate(parts):
                    if isinstance(part, ast.Constant) and part.value is None:
                        return self._opt_type(self._annotation(parts[1 - i]), node)
            if any(isinstance(part, ast.Constant) and part.value is None for part in parts):
                self.fail(node, "PS1102", "an optional of a multi-arm union is not in the IR")
            self.fail(node, "PS1101", "union types are not in the IR")
        self.fail(node, "PS1103", f"{alias} is not a value type in the IR")

    # Spellings of `collections.abc` / `typing` iterator forms that appear in
    # annotations even when the import sat under `if TYPE_CHECKING:` and was
    # never bound at runtime. A user class of the same name still wins.
    _IMPLICIT_ITER_FORMS = frozenset({"Iterator", "Iterable", "Generator", "Sequence"})

    def _typing_alias(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.type_aliases:
                return self.type_aliases[node.id]
            if node.id in self._IMPLICIT_ITER_FORMS and node.id not in self.classes and node.id not in self.declared:
                return node.id
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[3] == "erased":
                return resolved[2]
        return None

    def _synthetic_classes(self, seen: set[str], deferred_only: bool = False) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for name, info in self.classes.items():
            if name in seen or not info.get("synthetic"):
                continue
            # A deferred class (the argparse Namespace) grows fields while
            # the statements lower, so only the closing pass emits it.
            if deferred_only != bool(info.get("deferred")):
                continue
            record = {
                "name": name,
                "fields": info["fields"],
                "methods": [],
                "span": {"file": self.filename, "line": 1, "col": 1, "endLine": 1, "endCol": 1},
            }
            if info.get("exception"):
                record["exception"] = True
                record["exc_code"] = info["exc_code"]
                record["exc_parent"] = info.get("exc_parent", EXCEPTIONS["Exception"])
            found.append(record)
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
        if any(field["kind"] == "func" for field in fields):
            self.fail(node, "PS2002", "callable members are not lowered")
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

    def _generator_annotation(self, node: ast.Subscript) -> dict[str, Any]:
        """`Generator[T, S, R]` as a value annotation is the iterator cursor
        over T: the send and return types stay with the generator's own
        signature, and a local of this type holding a generator frame keeps
        the frame type (`_narrows_to_generator`)."""
        if not isinstance(node.slice, ast.Tuple) or not 1 <= len(node.slice.elts) <= 3:
            self.fail(node, "PS1001", "Generator is Generator[T] or Generator[T, S, R]")
        return self._iter_type(self._annotation(node.slice.elts[0]), node)

    def _iter_type(self, elem: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        """`Iterator[T]` / the `iter()` value: a synthetic record
        {seq: list[T], idx: int} whose cursor `next()`/`for` advance. The
        sequence materializes eagerly (the R24 model), so the record is a
        cursor into a list, not a lazy pull."""
        self._reject_banned_type(node, {"kind": "list", "elem": elem})
        name = f"iter_{_type_slug(elem)}"
        existing = self.classes.get(name)
        if existing is None:
            if name in self.declared:
                self.fail(node, "PS2002", f"{name} clashes with an iterator type")
            self.classes[name] = {
                "fields": [
                    {"name": "seq", "type": {"kind": "list", "elem": elem}},
                    {"name": "idx", "type": {"kind": "int"}},
                ],
                "methods": {},
                "synthetic": True,
                "iterator": True,
            }
            self.declared.add(name)
        elif not existing.get("iterator"):
            self.fail(node, "PS2002", f"{name} clashes with an iterator type")
        return {"kind": "class", "name": name, "elem": elem}

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

    def _optional_pipe(self, node: ast.BinOp) -> dict[str, Any] | None:
        arms = []
        pending = [node]
        while pending:
            cur = pending.pop()
            if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.BitOr):
                pending.append(cur.right)
                pending.append(cur.left)
            else:
                arms.append(cur)
        if not any(isinstance(arm, ast.Constant) and arm.value is None for arm in arms):
            return None
        rest = [arm for arm in arms if not (isinstance(arm, ast.Constant) and arm.value is None)]
        distinct = {ast.dump(arm) for arm in rest}
        if len(distinct) > 1:
            self.fail(node, "PS1102", "an optional of a multi-arm union is not in the IR")
        if not rest:
            return None
        return self._opt_type(self._annotation(rest[0]), node)

    def _opt_type(self, elem: dict[str, Any], node: ast.AST) -> dict[str, Any]:
        if elem["kind"] == "none":
            return {"kind": "none"}
        if elem.get("optional"):
            return elem
        name = "opt_" + _type_slug(elem)
        existing = self.classes.get(name)
        if existing is None:
            if name in self.declared:
                self.fail(node, "PS2002", f"{name} clashes with an optional type")
            self.classes[name] = {
                "fields": [
                    {"name": "_tag", "type": {"kind": "bool"}},
                    {"name": "_val", "type": elem},
                ],
                "methods": {},
                "synthetic": True,
                "optional": True,
            }
            self.declared.add(name)
        elif not existing.get("optional"):
            self.fail(node, "PS2002", f"{name} clashes with an optional type")
        return {"kind": "class", "name": name, "label": _show(elem) + " | None", "optional": True, "elem": elem}


def _param(lowerer: Any, arg: ast.arg, fn: ast.FunctionDef | None = None) -> dict[str, Any]:
    if arg.annotation is None:
        # Checker bridge: the checker inferred the parameter's type (from the
        # default, the callers, or the body). Without a bridged answer the
        # fence stands (PS1001, param-inference family).
        if fn is not None:
            inferred = lowerer._bridge_param_type(fn, arg)
            if inferred is not None:
                return {"name": arg.arg, "type": inferred}
        lowerer.fail(arg, "PS1001", f"parameter {arg.arg} needs an annotation")
    return {"name": arg.arg, "type": lowerer._annotation(arg.annotation)}


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


def _type_slug(typ: dict[str, Any]) -> str:
    raw = type_key(typ)
    return "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
