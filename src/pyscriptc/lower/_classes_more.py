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
from pyscriptc.lower._call_attr import _function_call_node
from pyscriptc.lower._core import Fence, _any_accepts, _mk_type
from pyscriptc.lower._expr import _const_of
from pyscriptc.lower._stmt import _is_docstring
from pyscriptc.restlower import alias_name
from pyscriptc.restlower._httpserver import HANDLER_BASE
from pyscriptc.tirserde import dict_to_type, node_to_dict

# The enum bases the subset lowers (issue #451): the plain and integer
# ones the subset always carried, plus StrEnum and the two flag bases.
ENUM_BASE_NAMES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


def _const_str_value(node: ast.expr) -> str | None:
    """The string a constant expression spells, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


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


def _enum_base(name: str, parent: str = "") -> dict[str, str]:
    """The enum base descriptor one `_resolve_base` spells.

    The `parent` field names the enum class the base is (the enum subclass
    case, issue #1314); it is empty for the `Enum`/`IntEnum` bases
    themselves.
    """
    return {"kind": _BASE_ENUM, "name": name, "parent": parent}


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
# `typing.NamedTuple` as a class base: the class body declares the fields,
# so the base is not erased — it selects the tuple-record lowering.
_NAMEDTUPLE_BASES = frozenset({"NamedTuple"})
# The `pyscriptc._annotations` shadow module's decorators (issue #1306):
# both are the identity, so the compiler erases them with no PS3099 note.
_SHADOW_DECORATORS = frozenset({"island", "specialize"})


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

# The method decorators that lower as their own method kind: the closed
# builtin set (`property`, `staticmethod`, `classmethod`) plus the stdlib
# decorator whose behavior the compiler synthesizes in the class lowering
# itself (issue #440). `cached_property` never takes the advisory erase:
# erasing it would turn the one-shot compute into a per-read method call.
_LOWERING_METHOD_DECORATORS = {("functools", "cached_property")}


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
        # The shadow module's decorators are the identity (issue #1306), so
        # the class lowers undecorated with no PS3099 note: nothing is lost
        # where CPython applies the wrap.
        node.decorator_list = [d for d in node.decorator_list if self._shadow_deco_name(d) is None]
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

    def _is_cached_property_deco(self, dec: ast.expr) -> bool:
        """True when the decorator is the lowered `functools.cached_property`.

        Both spellings (`@cached_property` after `from functools import
        cached_property` and `@functools.cached_property`) resolve through
        the same binding tables the synthesis guard reads.
        """
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            binding = self.bindings.get(target.id)
            if binding is not None:
                return (binding.module, binding.attr) in _LOWERING_METHOD_DECORATORS
            imported = self.stdlib_imports.get(target.id)
            return imported is not None and imported in _LOWERING_METHOD_DECORATORS
        if isinstance(target, ast.Attribute):
            resolved = self._resolve_module(target)
            if resolved is None:
                return False
            return (resolved[1], resolved[2]) in _LOWERING_METHOD_DECORATORS
        return False

    def _is_cached_property_call(self, dec: ast.expr) -> bool:
        """True when the decorator calls the member: `@cached_property()`.

        CPython raises a `TypeError` there (the descriptor takes the getter
        as its constructor argument, so the call form is not a spelling it
        accepts), and pyscriptc refuses it the same way: the name never
        takes the advisory erase, because erasing it would silently lower
        a program CPython refuses at class creation.
        """
        if not isinstance(dec, ast.Call):
            return False
        return self._is_cached_property_deco(dec)

    def _setter_target(self, dec: ast.expr) -> str | None:
        """The property name a `@<name>.setter` decorator writes, or None.

        The descriptor member spelling `setter` is what distinguishes the
        property-setter sugar from a user's own attribute decorator; the
        bare name before the dot is the property the setter attaches to
        (CPython's class body binds it there).
        """
        if not isinstance(dec, ast.Attribute) or dec.attr != "setter":
            return None
        if not isinstance(dec.value, ast.Name):
            return None
        return dec.value.id

    def _method_decorator(self, fn: ast.FunctionDef) -> str:
        # Erased typing decorators (`@abstractmethod`) drop out first, so
        # `@property @abstractmethod` still resolves to a property. A user
        # or unknown decorator that does not synthesize behavior takes the
        # advisory erase (divergence 54, issue #732); a synthesizing one
        # keeps the fence.
        decos = [
            d for d in fn.decorator_list if self._erased_deco_name(d) is None and self._shadow_deco_name(d) is None
        ]
        if not decos:
            # Only shadow decorators (or none): the method lowers plain and
            # the identity wrap has nothing to erase (issue #1306).
            fn.decorator_list = []
            return "method"
        if self._is_contextmanager_deco(fn) and enabled():
            # `@contextmanager`: the coro pass owns it; the method still
            # lowers as a plain method (the body is the resume body). The
            # check comes before the synthesis guard: the decorator is in
            # the closed set, and it lowers as a context manager.
            return "method"
        if any(self._is_method_synthesis_deco(d) for d in decos):
            self.fail(fn, "PS1201", "decorators are not lowered")
        if any(self._is_cached_property_call(d) for d in decos):
            # `@cached_property()` is not a spelling CPython accepts: the
            # descriptor takes the getter as its constructor argument. The
            # name refuses it instead of erasing the decorator and lowering
            # a program CPython rejects at class creation.
            self.fail(fn, "PS1201", "decorators are not lowered")
        if any(self._is_cached_property_deco(d) for d in decos):
            # `@cached_property` (issue #440): the compiler lowers the
            # one-shot compute itself, so the decorator is not erased and
            # the stack may not carry another member (the cached value's
            # slot and the getter's body are the whole contract).
            if len(decos) != 1:
                self.fail(fn, "PS1201", "decorators are not lowered")
            fn.decorator_list = []
            return "cached_property"
        setter_targets = [name for name in (self._setter_target(d) for d in decos) if name is not None]
        if setter_targets:
            # `@a.setter`: the descriptor write the property getter made
            # (issue #439, D4a). The body lowers as the property's setter
            # method; the instance-write sites dispatch to it.
            if len(decos) != 1 or len(setter_targets) != 1:
                self.fail(fn, "PS1201", "decorators are not lowered")
            fn.decorator_list = []
            return "setter"
        if any(self._is_descriptor_deco(d) for d in decos):
            # A descriptor-style decorator (`@v.deleter`, `@v.getter`, or a
            # non-name receiver) does not wrap the function: it mutates the
            # descriptor the getter made. Erasing it would leave the
            # property half-written, so the stack keeps the fence.
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

    def _namedtuple_base_name(self, node: ast.expr) -> str | None:
        """`typing.NamedTuple` used as a class base, or None.

        The name arrives through an erased import (`from typing import
        NamedTuple`) or through the qualified spelling (`typing.NamedTuple`).
        """
        member: str | None = None
        if isinstance(node, ast.Name):
            member = self.type_aliases.get(node.id)
        elif isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[3] == "erased":
                member = resolved[2]
        return member if member in _NAMEDTUPLE_BASES else None

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

    def _shadow_deco_name(self, dec: ast.expr) -> str | None:
        """`island`/`specialize` through the erased shadow-module import, or None.

        The shadow module's decorators are the identity function, so the
        wrap erases with no observable difference and the PS3099 note stays
        silent (unlike a user decorator, where the divergence is real).
        """
        member: str | None = None
        if isinstance(dec, ast.Name):
            member = self.type_aliases.get(dec.id)
        elif isinstance(dec, ast.Attribute):
            resolved = self._resolve_module(dec)
            if resolved is not None and resolved[3] == "erased" and resolved[1] == "pyscriptc._annotations":
                member = resolved[2]
        return member if member in _SHADOW_DECORATORS else None

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
            return self._accepts_protocol_value(want, got)
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

    def _callable_frame_return(self, frame_t: dict[str, Any], ret: dict[str, Any]) -> dict[str, Any]:
        """The return type a frame-returning callable's copy keeps.

        G3b (issue #442): the copy's parameter is the frame-returning
        callable, so the body hands the frame back. The copy's return type
        is the frame, the way a local keeps the frame type under an
        iterator-shaped annotation: the cursor layout would make the
        caller's `for` read the frame's fields as a list cell.
        """
        got = frame_t.get("return")
        if not isinstance(got, dict) or _type_kind(got) != "class":
            return ret
        got_name = got.get("name")
        if not isinstance(got_name, str):
            return ret
        frame = self.classes.get(got_name)
        if frame is None or frame.get("coro") != "generator":
            return ret
        if _type_kind(ret) != "class" or not self.classes.get(ret.get("name", ""), {}).get("iterator"):
            return ret
        next_ret = frame["methods"].get("__next__", (None, None))[1]
        elem = ret.get("elem")
        if next_ret is None or elem is None or not types_equal(next_ret, elem):
            return ret
        return dict(got)

    def _accepts_protocol_value(self, want: dict[str, Any], got: dict[str, Any]) -> bool:
        """The value-shaped half of `_accepts_protocol`.

        A `Callable[..., Iterator[T]]` parameter accepts a frame-returning
        callable (G3b, issue #442): the parameter's return annotation is
        the iterator cursor, the callable hands back a generator frame, and
        the frame's element proves against the annotation's element. The
        parameters themselves must agree: the callable takes the same
        arguments the parameter declares.
        """
        if _type_kind(want) != "func" or _type_kind(got) != "func":
            return _any_accepts(want, got)
        want_params = want.get("params")
        got_params = got.get("params")
        if not isinstance(want_params, list) or not isinstance(got_params, list):
            return False
        if len(want_params) != len(got_params):
            return False
        if not all(types_equal(a, b) for a, b in zip(want_params, got_params, strict=True)):
            return False
        return self._accepts_protocol(want.get("return", {}), got.get("return", {}))

    def _generator_return_type(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef, want: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The generator frame a def returns under an iterator annotation.

        G3b (issue #442): a def that returns a generator frame under an
        `Iterator[T]`-shaped annotation keeps the frame as its signature
        return type. The synthetic `iter_` cursor is a materialized list
        with an index, so a call site that took the annotation's type
        would read the frame's fields as a list cell (the #441 segfault
        shape). Every `return` must hand back the same frame class, or
        the carve-out proves nothing and the caller keeps the annotation.

        The annotation stays the accepted contract: the return site checks
        it through `_accepts_protocol`'s iterator arm, which proves the
        frame's element against the annotation's element.
        """
        if _type_kind(want) != "class" or want.get("elem") is None:
            return None
        want_name = want.get("name")
        if not isinstance(want_name, str) or not self.classes.get(want_name, {}).get("iterator"):
            return None
        found: set[str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            name = self._returned_frame_name(node.value)
            if name is None:
                return None
            found.add(name)
        if len(found) != 1:
            return None
        frame_name = found.pop()
        frame = self.classes.get(frame_name)
        if frame is None or frame.get("coro") != "generator":
            return None
        next_ret = frame["methods"].get("__next__", (None, None))[1]
        if next_ret is None or not types_equal(next_ret, want["elem"]):
            return None
        return {"kind": "class", "name": frame_name}

    def _returned_frame_name(self, node: ast.expr) -> str | None:
        """The generator frame class a returned expression produces, or None.

        The expression is a call to a generator function (the frame the
        call allocates) or a name bound to one. Anything else returns
        None: the carve-out never guesses a frame.
        """
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node, ast.Name):
            name = node.id
        else:
            return None
        plans = self._coro_plans()
        # The lifted nested generator (G3b) is keyed `owner.inner`; the
        # module-level one is keyed by its own name.
        for key in (name, f"{self.function or ''}.{name}"):
            plan = plans.get(key)
            if plan is not None and plan.kind == "generator":
                return plan.frame
        return None

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
            self.fail_base(
                node,
                "PS2002",
                f"class {node.id} runs on the island floor of {module} and cannot be a native base class: "
                "the subclass joins the island",
                node,
            )
        # boxed:end
        if isinstance(node, ast.Name) and node.id in self.classes:
            info = self.classes[node.id]
            if info.get("synthetic"):
                self.fail_base(node, "PS2002", "subclassing a stdlib type is not lowered", node)
            if info.get("enum"):
                # An enum base lowers only in CPython's own shape: the base
                # enum carries no member yet, so the subclass is the first
                # one to fill it (issue #1314). A base with members keeps
                # the fence: CPython refuses that subclass outright. The
                # integer and flag bases carry the same shape (issue #451).
                if info["base"] not in {"IntEnum", "int", "StrEnum", "Flag", "IntFlag"} or info["members"]:
                    self.fail_base(node, "PS2002", "an enum is not a base", node)
                if node.id in self.enum_subclasses:
                    self.fail_base(node, "PS2002", f"duplicate enum subclass {node.id}", node)
                self.enum_subclasses.add(node.id)
                return _enum_base(str(info["base"]), node.id)
            return _class_type(node.id)
        # The http.server handler base (issue #1270): the spelling is the
        # module attribute (`http.server.BaseHTTPRequestHandler`) or the
        # imported name; both resolve to the class the import installed.
        base = self._http_handler_base_name(node)
        if base is not None:
            self._install_http_handler_base()
            return _class_type(base)
        self.fail_base(node, "PS2002", "this class base is not lowered", node)
        raise AssertionError("unreachable")

    def _http_handler_base_name(self, node: ast.expr) -> str | None:
        """`BaseHTTPRequestHandler` as a base-class spelling, or None.

        Both spellings resolve: the module attribute
        (`http.server.BaseHTTPRequestHandler`) and the imported name. None
        means this base is not the handler base.
        """
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[1] == "http.server" and resolved[2] == "BaseHTTPRequestHandler":
                return "BaseHTTPRequestHandler"
            return None
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None and binding.module == "http.server" and binding.attr == "BaseHTTPRequestHandler":
                return "BaseHTTPRequestHandler"
        return None

    def is_http_handler_instance(self, class_name: str | None) -> bool:
        """True when `class_name` is a handler class (it extends the base).

        The handler's request-backed attributes (`self.path`, `self.rfile`,
        `self.wfile`, `self.headers`) and its three response methods lower
        through the http.server handler path, so every read on a handler
        instance asks this first.
        """
        if class_name is None:
            return False
        info = self.classes.get(class_name)
        if info is None:
            return False
        return info.get("base") == HANDLER_BASE

    def _enum_base_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None and binding.module == "enum" and binding.attr in ENUM_BASE_NAMES:
                return binding.attr
            return None
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[1] == "enum" and resolved[2] in ENUM_BASE_NAMES:
                return resolved[2]
        return None

    def _collect_enum(self, node: ast.ClassDef, which: str) -> None:
        self.declared.add(node.name)
        str_members = which == "StrEnum"
        members: dict[str, int | str] = {}
        # An enum subclass (issue #1314) starts from the base's members and
        # adds its own: the members of the base stay reachable through the
        # subclass, the way CPython's enum inheritance keeps them.
        inherited = self.enum_base_members.get(node.name, {})
        members.update(inherited)
        next_value = max((v for v in inherited.values() if isinstance(v, int)), default=0) + 1
        next_auto = next_value
        # The values spelled so far, the ones the `auto()` counter reads:
        # CPython's `auto()` takes the largest value the body has spelled
        # plus one, so `A = 5` then `auto()` gives 6 and a later `C = 2`
        # then `auto()` gives 7, not 3.
        spelled: list[int] = [v for v in inherited.values() if isinstance(v, int)]
        methods: list[tuple[ast.FunctionDef, str]] = []
        for stmt in node.body:
            if _is_docstring(stmt) or isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # An enum method or property (issue #451): CPython attaches
                # it to the enum class, so every member answers it. The
                # signature pass below takes the same shape a normal class
                # method takes; the emitted body runs on the member's value.
                try:
                    kind = self._method_decorator(stmt)  # type: ignore[arg-type]
                    methods.append((stmt, kind))  # type: ignore[arg-type]
                except Fence as exc:
                    self.caught.append(exc)
                continue
            try:
                target, value = self._enum_assign(stmt)
                auto = self._enum_auto(value)
                member_value: int | str
                if auto is None:
                    if str_members:
                        # A StrEnum member is a string constant (issue #451):
                        # CPython's StrEnum members are strs, so the member
                        # value is the text the body spells.
                        text = _const_str_value(value)
                        if text is None:
                            self.fail(stmt, "PS2002", "a StrEnum member is a string constant")
                            raise AssertionError("unreachable")
                        member_value = text
                    else:
                        number = _const_int_index(value)
                        if number is None:
                            self.fail(stmt, "PS2002", "an enum member is an integer constant")
                            raise AssertionError("unreachable")
                        member_value = number
                else:
                    if not auto:
                        self.fail(stmt, "PS2003", "enum.auto takes no arguments")
                        raise AssertionError("unreachable")
                    if str_members:
                        # CPython's StrEnum `auto()` spells the member's
                        # name in lower case as the value (issue #451).
                        member_value = target.lower()
                    else:
                        member_value = next_auto
                if isinstance(member_value, int):
                    spelled.append(member_value)
                    next_auto = max(spelled) + 1
                if target in members:
                    self.fail(stmt, "PS2002", f"duplicate enum member {target}")
                members[target] = member_value
            except Fence as exc:
                self.caught.append(exc)
        if self.caught:
            return
        # An empty enum body is a base another enum extends (issue #1314):
        # CPython allows exactly that shape, so the record exists with no
        # members and the subclass fills them. An empty enum with nothing
        # extending it is the `an enum needs a member` fence, checked at
        # the end of the collection pass.
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
        self._enum_methods[node.name] = methods
        if methods:
            self._enum_method_sigs(node, methods)

    def _enum_method_sigs(self, node: ast.ClassDef, methods: list[tuple[ast.FunctionDef, str]]) -> None:
        """The signatures of the methods an enum class body spells (issue #451).

        CPython attaches an enum's methods to the enum class, so every
        member answers them. The signature pass is the class one, with one
        retyping: `self` takes the member value kind (the int or str the
        members carry), not the enum class type, because the lowered
        member is the value itself. A method whose signature does not
        prove drops out and the enum keeps the rest.
        """
        info = self.classes[node.name]
        value_kind = self._enum_value_kind(node.name)
        for stmt, kind in methods:
            try:
                sig = self._signature(stmt, node.name, kind)
                if kind in {"method", "property"} and sig[0] and sig[0][0]["name"] == "self":
                    sig = ([{"name": "self", "type": {"kind": value_kind}}, *sig[0][1:]], sig[1])
            except Fence as exc:
                self.caught.append(exc)
                continue
            if sig is None:
                continue
            info["methods"][stmt.name] = sig
            info["method_kind"][stmt.name] = kind
            info["method_owner"][stmt.name] = node.name

    def _enum_member_of(self, node: ast.expr) -> tuple[str, str, int | str] | None:
        """The enum member a node spells, as (enum class, member name, value).

        The node is the member read itself (`Color.RED`), either the class
        name or a class alias. None means the node is not an enum member
        read, so the caller keeps the ordinary path.
        """
        if not isinstance(node, ast.Attribute):
            return None
        class_name = self.class_aliases.get(node.value.id, node.value.id) if isinstance(node.value, ast.Name) else None
        if class_name is None or class_name not in self.classes:
            return None
        info = self.classes[class_name]
        if not info.get("enum"):
            return None
        value = info["members"].get(node.attr)
        if value is None:
            return None
        return class_name, node.attr, value

    def _enum_member_attr(self, node: ast.Attribute, span: dict[str, Any]) -> dict[str, Any] | None:
        """The `.value` and `.name` reads of an enum member (issue #451).

        CPython gives every member its value and its name. The lowered
        member is a compile-time constant, so both reads answer from the
        member's own record: the value the body spelled and the name the
        body spelled it under. A member of a plain `Enum` answers its
        value as the integer the body spelled; a `StrEnum` member answers
        the text.
        """
        found = self._enum_member_of(node.value)
        if found is None:
            return None
        class_name, member_name, value = found
        if node.attr == "value":
            return _const_of(str(value), self._enum_value_kind(class_name), span)
        if node.attr == "name":
            return _const_of(member_name, "str", span)
        info = self.classes[class_name]
        if info["method_kind"].get(node.attr) == "property":
            # A property on the enum class (issue #451): CPython reads it off
            # the member, and the lowered member is the value, so the
            # property's getter runs with the member bound as `self`.
            sig = info["methods"][node.attr]
            owner = info.get("method_owner", {}).get(node.attr, class_name)
            self_arg = _const_of(str(value), self._enum_value_kind(class_name), span)
            return _function_call_node(f"cls.{owner}.{node.attr}", [self_arg], sig[1], span)
        return None

    def _enum_value_kind(self, class_name: str) -> str:
        """The kind an enum class's member values answer: int or str."""
        base = self.classes.get(class_name, {}).get("base")
        return "str" if base == "StrEnum" else "int"

    def _enum_member_call(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any] | None:
        """The call of a method on an enum member (issue #451).

        CPython attaches an enum's methods to the enum class, so every
        member answers them with the member bound as `self`. The lowered
        member is the value itself, so the call binds the member's value
        to `self` and calls the enum class's emitted method.
        """
        found = self._enum_member_of(node.func.value) if isinstance(node.func, ast.Attribute) else None
        if found is None:
            return None
        class_name, _member_name, value = found
        info = self.classes[class_name]
        method = node.func.attr
        sig = info["methods"].get(method)
        if sig is None:
            self.fail(node, "PS2001", f"{class_name}.{method} is not a method")
        kind = info["method_kind"].get(method, "method")
        if kind == "property":
            self.fail(node, "PS2002", "a property is not called")
        if kind not in {"method", "static", "class"}:
            self.fail(node, "PS2001", f"{class_name}.{method} is not a method")
        params = list(sig[0])
        if kind == "method":
            # The receiver is the member: the value rides as the `self`
            # argument the emitted method takes.
            self_arg = _const_of(str(value), self._enum_value_kind(class_name), span)
            params = [param for param in params if param["name"] != "self"]
            args = [self_arg, *self._fill_args(node, params, f"{class_name}.{method}")]
        else:
            args = self._fill_args(node, params, f"{class_name}.{method}")
        owner = info.get("method_owner", {}).get(method, class_name)
        return _function_call_node(f"cls.{owner}.{method}", args, sig[1], span)

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

    def _nominal_subclass_field(self, want: dict[str, Any], got: dict[str, Any]) -> str | None:
        """The added field a nominal subclass carries over its base, or None.

        Issue #1243's narrow carve-out: a slot annotated with a class
        accepts a value whose class is a nominal subclass of it (declared
        in the same compile) when the subclass adds no instance fields.
        The empty answer means the layouts agree (`got`'s field list is a
        prefix extension of `want`'s, so shared-base methods GEP
        correctly); a non-empty answer is the first added field, which
        the fence message names.
        """

        if _type_kind(want) != "class" or _type_kind(got) != "class":
            return None
        want_name = want.get("name")
        got_name = got.get("name")
        if not isinstance(want_name, str) or not isinstance(got_name, str) or want_name == got_name:
            return None
        if want_name not in self.classes or got_name not in self.classes:
            return None
        want_info = self.classes[want_name]
        got_info = self.classes[got_name]
        # Structural markers are not nominal bases: a Protocol or a
        # synthetic record has no nominal-subclass relation to check.
        if want_info.get("protocol") or want_info.get("synthetic") or got_info.get("synthetic"):
            return None
        if not self._is_subclass_name(got_name, want_name):
            return None
        own = got_info.get("own_fields", got_info.get("fields", []))
        want_fields = {field["name"]: field["type"] for field in want_info["fields"]}
        for field in own:
            prior = want_fields.get(field["name"])
            if prior is None or not types_equal(prior, field["type"]):
                return field["name"]
        return None

    def _covariant_slot_type(self, want: dict[str, Any], got: dict[str, Any]) -> dict[str, Any] | None:
        """The concrete type a covariant binding keeps, or None.

        The carve-out's binding rule (issue #1243): the annotated slot is
        accepted, but the slot's own type stays the value's concrete
        class, because dispatch is static (`cls.{owner}.{method}`) and a
        `Base`-typed slot would call `Base`'s method on a `Sub` value.
        Keeping `Sub` gives CPython's answer for every read the slot
        allows: the overridden method resolves to the subclass body and
        the inherited fields read through the same layout prefix. The
        value still must satisfy the no-added-field rule; anything else
        keeps its fence.
        """

        if self._nominal_subclass_field(want, got) is not None:
            return None
        if _type_kind(want) != "class" or _type_kind(got) != "class":
            return None
        want_name = want.get("name")
        got_name = got.get("name")
        if not isinstance(want_name, str) or not isinstance(got_name, str) or want_name == got_name:
            return None
        if not self._is_subclass_name(got_name, want_name):
            return None
        # The annotation is the accepted contract: the base class names
        # the slot, the concrete class names the dispatch.
        return dict(got)

    def _covariant_return_names(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, want_name: str) -> list[str]:
        """The distinct subclass names a def's returns construct, or [].

        Every returned expression must be a direct construction of a
        nominal subclass of `want_name`; any other return shape (a bare
        construction of the base itself, a call whose class is unknown,
        a branch over two subclasses) returns [] and the caller keeps
        the annotation: the carve-out never guesses a concrete class.
        """

        found: list[str] = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            name = self._constructed_class_name(node.value)
            if name is None or name == want_name or not self._is_subclass_name(name, want_name):
                return []
            if name not in found:
                found.append(name)
        return found

    def _covariant_return_type(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef, want: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The concrete return type a def proves over its annotation.

        Issue #1243's carve-out, signature side: a def annotated to return
        a class may return an instance of a nominal subclass (declared in
        the same compile). Dispatch is static, so the call site must see
        the concrete class to call the subclass's method; the annotation
        stays the accepted contract and rides along on the type as
        `declared`. One subclass proves it; two or more prove nothing and
        the caller fences (the static call would answer the base's body).
        """

        if _type_kind(want) != "class":
            return None
        want_name = want.get("name")
        if not isinstance(want_name, str) or want_name not in self.classes:
            return None
        found = self._covariant_return_names(fn, want_name)
        if len(found) != 1:
            return None
        concrete = {"kind": "class", "name": found[0]}
        if self._nominal_subclass_field(want, concrete) is not None:
            # The subclass adds a field: the carve-out refuses it, so the
            # signature keeps the annotation and the return site fences.
            return None
        return dict(concrete)

    def _constructed_class_name(self, node: ast.expr) -> str | None:
        """The class a direct construction expression builds, or None.

        `Sub()` names `Sub`; `Sub` (a name) names the class only when this
        def constructed it, which the caller proves by binding; anything
        else is not a construction.
        """

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in self.classes:
                return name
        return None

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
