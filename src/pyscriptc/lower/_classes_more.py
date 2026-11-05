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


class ClassesMoreMixin:
    def _class_decorator(self, node: ast.ClassDef) -> str | None:
        if not node.decorator_list:
            return None
        if len(node.decorator_list) != 1 or not self._is_dataclass_deco(node.decorator_list[0]):
            self.fail(node, "PS1201", "decorators are not lowered")
        return "dataclass"

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
        if want.get("bare") and _type_kind(got) == _type_kind(want):
            return True
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

    # Spellings of `collections.abc` / `typing` iterator forms that appear in
    # annotations even when the import sat under `if TYPE_CHECKING:` and was
    # never bound at runtime. A user class of the same name still wins.
    _IMPLICIT_ITER_FORMS = frozenset({"Iterator", "Iterable", "Generator", "Sequence"})
