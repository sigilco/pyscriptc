"""Class decorators, enums, isinstance and exception codes, split verbatim
from `lower._classes`.

`ClassesMoreMixin` holds the class-decorator and enum family
(`_class_decorator`, `_is_dataclass_deco`, `_method_decorator`, the erased
base/decorator probes, `_resolve_base`, `_collect_enum`, `_enum_auto` and
`_enum_assign`) plus the isinstance and exception-code family
(`_isinstance_type`, `_isinstance_holds`, `_is_subclass_name`,
`_exception_parent_code`, `_exception_code_of`, `_exception_codes`).
`ClassesMixin` composes it, the way `ClassesMixin` composes
`AnnotationMixin`, so every method stays reachable as `self._collect_enum(...)`
and friends. The module-level helpers the moved code needs (`_class_type`,
`_leaf_type`, `_enum_base`, `_exception_base`, `_bare_leaf`, `_type_kind`,
`_maybe_type_kind`) moved with the family so nothing here back-imports
`lower._classes`. Every body below is the code that lived in
`lower._classes`, unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS
from pyscriptc.coro import enabled
from pyscriptc.ir import types_equal
from pyscriptc.lower._annotations import _const_int_index
from pyscriptc.lower._core import Fence, _any_accepts, _mk_type
from pyscriptc.lower._stmt import _is_docstring
from pyscriptc.restlower import alias_name
from pyscriptc.tirserde import dict_to_type, node_to_dict


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


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record, read off the typed `T` record."""
    return _mk_type(typ).kind


def _maybe_type_kind(typ: dict[str, Any]) -> str | None:
    """The kind tag of a type record that may carry none."""
    if "kind" not in typ:
        return None
    return _type_kind(typ)


_BASE_ENUM = "enum"
_BASE_EXCEPTION = "exception"


def _enum_base(name: str) -> dict[str, str]:
    """The enum base descriptor one `_resolve_base` spells."""
    return {"kind": _BASE_ENUM, "name": name}


def _exception_base(name: str) -> dict[str, str]:
    """The exception base descriptor one `_resolve_base` spells."""
    return {"kind": _BASE_EXCEPTION, "name": name}


def _bare_leaf(kind: str) -> dict[str, Any]:
    """The bare leaf type record an isinstance target spells.

    The `bare` bit has no typed `T` slot, so the record stays a plain dict.
    """
    return {"kind": kind, "bare": True}


_ERASED_BASES = frozenset({"ABC", "ABCMeta", "Protocol"})
_ERASED_DECORATORS = frozenset({"abstractmethod", "overload"})


# The stdlib decorators that synthesize class behavior: they never take the
# erase, they keep the fence (native) or the island route (fallback tier).
_CLASS_SYNTHESIS_DECORATORS = {
    ("dataclasses", "dataclass"),
    ("functools", "total_ordering"),
}


_DECO_ERASED_MESSAGE = (
    "the class decorator is erased: the class lowers undecorated and the "
    "wrap never runs, where CPython applies the decorator to the class "
    "object (docs/reference/divergences.md #53, PS3099)"
)

_METHOD_DECO_ERASED_MESSAGE = (
    "the method decorator is erased: the method lowers undecorated and the "
    "wrap never runs, where CPython replaces the attribute with whatever the "
    "decorator returns (docs/reference/divergences.md #54, PS3099)"
)

_MODULE_DECO_ERASED_MESSAGE = (
    "the function decorator is erased: the function lowers undecorated and the "
    "wrap never runs, where CPython replaces the name with whatever the "
    "decorator returns (docs/reference/divergences.md #54, PS3099)"
)

# The stdlib decorators that synthesize function behavior at runtime: they
# never take the erase, they keep the fence (native) or the island route
# (fallback tier). `lru_cache` memoizes (a second call with a warm cache
# skips the body, and attribute reads see a wrapper object, not a function);
# `wraps` rebinds `__wrapped__` and `__dict__` metadata on the wrapper;
# `contextmanager` turns a generator into a context manager (the `with`
# block calls `__enter__`/`__exit__` on a manager object, not the
# generator).
_METHOD_SYNTHESIS_DECORATORS = {
    ("functools", "lru_cache"),
    ("functools", "cache"),
    ("functools", "wraps"),
    ("functools", "singledispatchmethod"),
    ("contextlib", "contextmanager"),
}


class ClassesMoreMixin:
    def _class_decorator(self, node: ast.ClassDef) -> str | None:
        """The class's decorator kind: `"dataclass"` or None.

        `@dataclass` (and only it) changes the class lowering. A user
        decorator that cannot lower as a callable value (`deco(d)` would
        lower, CPython runs it and drops the result) or any other spelling
        desugars away before the def lowers: the class keeps its fields,
        methods and constructor and the wrap is skipped (issue #732). The
        divergence is the documented PS3099 seam (issue #745).
        """
        if not node.decorator_list:
            return None
        if len(node.decorator_list) == 1:
            if self._is_dataclass_deco(node.decorator_list[0]):
                return "dataclass"
            # A stdlib decorator that synthesizes class behavior at runtime
            # (`dataclasses.dataclass(order=...)`, `functools.total_ordering`)
            # is not a user wrap: erasing it silently loses the ordering
            # methods CPython attaches, so it keeps the #628 island route
            # under the fallback tier and the PS1201 fence native, never
            # the erase.
            if self._is_class_synthesis_deco(node.decorator_list[0]):
                self.fail(node, "PS1201", "decorators are not lowered")
        # The erase is advisory: the class lowers undecorated, so the wrap
        # never runs (divergence 53, issue #732). The note rides the
        # module's `_notes` and prints on stderr; the compile itself exits
        # 0 and the binary runs.
        node.decorator_list = []
        note = f"note: PS3099 ({node.name}): {_DECO_ERASED_MESSAGE}"
        if note not in self.module_notes:
            self.module_notes.append(note)
        return None

    def _is_method_synthesis_deco(self, dec: ast.expr) -> bool:
        """A stdlib decorator whose call changes the function's call semantics.

        `functools.lru_cache` (and `cache`) memoizes, `wraps` attaches
        metadata to a wrapper, `singledispatchmethod` dispatches on the first
        argument's type; erasing any of them silently loses that behavior,
        so they never take the erase (they keep the PS1201 fence native and
        the island route under the fallback tier).
        """
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            if binding is not None:
                return (binding.module, binding.attr) in _METHOD_SYNTHESIS_DECORATORS
            # A `from functools import lru_cache` spelling: the member is
            # outside the lowered subset, so the import fenced and no
            # binding exists. The import table still names the member.
            imported = self.stdlib_imports.get(target.id)
            return imported is not None and imported in _METHOD_SYNTHESIS_DECORATORS
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            if resolved is None:
                return False
            return (resolved[1], resolved[2]) in _METHOD_SYNTHESIS_DECORATORS
        return False

    def _has_synthesis_deco(self, fn: ast.FunctionDef) -> bool:
        """True when the definition carries a behavior-synthesizing decorator.

        A stack that holds one never takes the advisory erase: the def
        lowers undecorated, so the synthesis (`functools.lru_cache`'s
        memoization, `wraps`' metadata, `singledispatchmethod`'s dispatch)
        would be lost with it. Such a definition keeps its fence.
        """
        return any(self._is_method_synthesis_deco(dec) for dec in fn.decorator_list)

    def _erase_module_decorators(self, fn: ast.FunctionDef) -> None:
        """Drop module-function decorators outside the closed set that do not
        synthesize behavior, with the advisory PS3099 note (divergence 54,
        issue #732).

        A stack that carries a behavior-synthesizing decorator never takes
        the erase: the whole stack lowers undecorated, so the synthesis
        would be lost with it. The caller checks for that first and keeps
        the fence; this helper only ever drops the members it may drop.
        """
        for dec in fn.decorator_list:
            if self._is_method_synthesis_deco(dec):
                continue
            note = f"note: PS3099 ({fn.name}): {_MODULE_DECO_ERASED_MESSAGE}"
            if note not in self.module_notes:
                self.module_notes.append(note)
        fn.decorator_list = []

    def _erase_method_decorators(self, fn: ast.FunctionDef) -> None:
        """Drop the decorators that are not in the closed set and are not
        behavior-synthesizing, with the advisory PS3099 note (divergence 54,
        issue #732). The method lowers as the plain kind the remaining
        decorator selects.

        A stack that carries a behavior-synthesizing decorator never takes
        the erase: the method would lower undecorated and the synthesis
        would be lost with it, so the caller keeps the fence instead.
        """
        for dec in fn.decorator_list:
            if self._is_method_synthesis_deco(dec):
                continue
            note = f"note: PS3099 ({fn.name}): {_METHOD_DECO_ERASED_MESSAGE}"
            if note not in self.module_notes:
                self.module_notes.append(note)
        fn.decorator_list = []

    def _is_dataclass_deco(self, dec: ast.expr) -> bool:
        target = dec
        if isinstance(dec, ast.Call):
            keywords = {kw.arg: kw.value for kw in dec.keywords}
            extra = {name for name in keywords if name not in {"init", "frozen", "slots"}}
            if dec.args or extra:
                return False
            for value in keywords.values():
                if not isinstance(value, ast.Constant) or value.value is not True:
                    return False
            target = dec.func
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            return binding is not None and binding.module == "dataclasses" and binding.attr == "dataclass"
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            return resolved is not None and resolved[1] == "dataclasses" and resolved[2] == "dataclass"
        return False

    def _is_class_synthesis_deco(self, dec: ast.expr) -> bool:
        """A stdlib decorator whose call attaches methods to the class.

        `dataclasses.dataclass` in any spelling (taken or not) and
        `functools.total_ordering` synthesize dunders at runtime; erasing
        them loses behavior, so they never take the erase.
        """
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            if binding is None:
                return False
            return (binding.module, binding.attr) in _CLASS_SYNTHESIS_DECORATORS
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            if resolved is None:
                return False
            return (resolved[1], resolved[2]) in _CLASS_SYNTHESIS_DECORATORS
        return False

    def _method_decorator(self, fn: ast.FunctionDef) -> str:
        # Erased typing decorators (`@abstractmethod`) drop out first, so
        # `@property @abstractmethod` still resolves to a property. A user
        # or unknown decorator that does not synthesize behavior takes the
        # advisory erase (divergence 54, issue #732); a synthesizing one
        # keeps the fence.
        decos = [d for d in fn.decorator_list if self._erased_deco_name(d) is None]
        if not decos:
            return "method"
        if self._is_contextmanager_deco(fn) and enabled():
            # `@contextmanager`: the coro pass owns it; the method still
            # lowers as a plain method (the body is the resume body). The
            # check comes before the synthesis guard: the decorator is in
            # the closed set, and it lowers as a context manager.
            return "method"
        if any(self._is_method_synthesis_deco(d) for d in decos):
            self.fail(fn, "PS1201", "decorators are not lowered")
        if any(self._is_descriptor_deco(d) for d in decos):
            # A descriptor-style decorator (`@v.setter`, `@v.deleter`) does
            # not wrap the function: it mutates the descriptor the getter
            # made. Erasing it would leave the property half-written, so
            # the stack keeps the fence.
            self.fail(fn, "PS1201", "decorators are not lowered")
        builtin = self._builtin_method_kind(decos)
        if builtin is not None:
            return builtin
        if any(self._is_builtin_method_deco(d) for d in decos):
            # A closed-set builtin (`property`, `staticmethod`,
            # `classmethod`) in a stack with other members: the builtin
            # selects the kind and the other member has no place in it.
            self.fail(fn, "PS1201", "decorators are not lowered")
        self._erase_method_decorators(fn)
        return "method"

    def _is_builtin_method_deco(self, dec: ast.expr) -> bool:
        """True when the decorator names a closed-set builtin method kind."""
        target = dec.func if isinstance(dec, ast.Call) else dec
        return isinstance(target, ast.Name) and target.id in {"property", "staticmethod", "classmethod"}

    def _builtin_method_kind(self, decos: list[ast.expr]) -> str | None:
        """The method kind a single closed-set builtin decorator selects.

        `property`, `staticmethod` and `classmethod` name the builtin
        kinds; a decorator that reads as one of them (`@v.setter`) is not
        the builtin and selects nothing.
        """
        if len(decos) != 1:
            return None
        target = decos[0].func if isinstance(decos[0], ast.Call) else decos[0]
        if isinstance(target, ast.Name) and target.id in {"property", "staticmethod", "classmethod"}:
            return {"property": "property", "staticmethod": "static", "classmethod": "class"}[target.id]
        return None

    def _is_descriptor_deco(self, dec: ast.expr) -> bool:
        """True when the decorator is a descriptor-style member access.

        `@v.setter`, `@v.deleter` and `@v.getter` do not wrap the
        function: they mutate the descriptor the getter made, and
        CPython's class body keeps one attribute for the pair. The
        spelling is the descriptor protocol's own member names, so the
        module member spellings (`@functools.lru_cache`) do not match and
        a user's own attribute decorator (`@register.filter`) keeps the
        advisory erase.
        """
        target = dec.func if isinstance(dec, ast.Call) else dec
        if not isinstance(target, ast.Attribute):
            return False
        return target.attr in {"setter", "deleter", "getter"}

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
        annotation's element, and the caller narrows the slot to the frame.
        An `any` slot (an Any container's value, issue #781) accepts every
        concrete value: the write site stamps what it wrote, and the read
        site proves its own type."""
        if _type_kind(want) != "class" or _type_kind(got) != "class":
            return _any_accepts(want, got)
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
        if want_name == "CompletedProcess" and got_name == "CompletedProcessText":
            # The text-mode run record: the annotation names the stdlib
            # class, the value is the str-field variant (issue #680).
            return True
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
        if _maybe_type_kind(want) != "class" or _maybe_type_kind(got) != "class":
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
            return _enum_base(enum)
        if isinstance(node, ast.Name) and node.id in EXCEPTIONS:
            return _exception_base(node.id)
        # boxed:begin
        # An island class of another module has no native class record, so a
        # native subclass cannot extend it: the class definition fences and
        # rides the island instead (issue #628, one module down). The fence
        # names the defining module so the refusal reads the way the
        # single-module refusal does.
        if isinstance(node, ast.Name) and node.id in self.island_floor_classes:
            module, name = self.island_floor_classes[node.id]
            self.fail(
                node,
                "PS2002",
                f"class {node.id} runs on the island floor of {module} and cannot be a native base class: "
                "the subclass joins the island",
            )
        # boxed:end
        if isinstance(node, ast.Name) and node.id in self.classes:
            info = self.classes[node.id]
            if info.get("synthetic"):
                self.fail(node, "PS2002", "subclassing a stdlib type is not lowered")
            if info.get("enum"):
                self.fail(node, "PS2002", "an enum is not a base")
            return _class_type(node.id)
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

    def _isinstance_type(self, node: ast.expr) -> dict[str, Any]:
        if isinstance(node, ast.Name) and node.id in {"int", "float", "bool", "str", "bytes"}:
            return _leaf_type(node.id)
        if isinstance(node, ast.Name) and node.id in {"list", "dict"}:
            return _bare_leaf(node.id)
        if isinstance(node, ast.Name) and (
            node.id in self.classes or node.id in self.class_aliases or node.id in EXCEPTIONS
        ):
            name = self.class_aliases.get(node.id, node.id)
            if self.classes.get(name, {}).get("protocol"):
                self.fail(node, "PS2001", "isinstance on a Protocol is not lowered")
            return _class_type(name)
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None:
                alias = alias_name(binding.module, binding.attr)
                if alias is not None:
                    return _class_type(alias)
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None:
                alias = alias_name(resolved[1], resolved[2])
                if alias is not None:
                    return _class_type(alias)
        self.fail(node, "PS2001", "isinstance type is not in the lowered subset")
        raise AssertionError("unreachable")

    def _isinstance_holds(self, got: dict[str, Any], want: dict[str, Any]) -> bool:
        if want.get("bare"):
            if _type_kind(got) == _type_kind(want):
                return True
            # A bare list/dict target the kind check missed is a definite
            # False: no lowered operand kind is a subclass of the bare
            # container, and `type_key` cannot spell the bare dict anyway.
            return False
        if _type_kind(want) == "int" and _type_kind(got) == "bool":
            return True
        if _type_kind(want) == "class" and _type_kind(got) == "class":
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

    def _exception_codes(self, name: str, node: ast.AST | None = None) -> list[int]:
        # boxed:begin
        # Another module's island exception class has no native exc code:
        # the native lane cannot match it, so the handler that names it is
        # not lowered and the definition joins the island, where CPython's
        # own matching applies.
        if name in self.island_floor_classes:
            module, cls = self.island_floor_classes[name]
            self.fail(
                node if node is not None else ast.Pass(),
                "PS2002",
                f"class {name} runs on the island floor of {module}: except {name} does not lower natively",
            )
        # boxed:end
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

    # Spellings of `collections.abc` / `typing` iterator forms that appear in
    # annotations even when the import sat under `if TYPE_CHECKING:` and was
    # never bound at runtime. A user class of the same name still wins.
    _IMPLICIT_ITER_FORMS = frozenset({"Iterator", "Iterable", "Generator", "Sequence"})
