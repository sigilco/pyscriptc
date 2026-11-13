"""Class-body assignments with a runtime value (issue #732).

A class body may bind an attribute to a value the compiler computes:
`x = f()`, `x = [i for i in xs]`, `x = OTHER.name`. CPython runs the class
body once, at class creation, and the value lives in the class dictionary
for the life of the process. pyscriptc has no class dictionary: a class is
a compile-time struct of instance fields.

This module gives those attributes a runtime home. The value lowers in a
synthesized per-class initializer function, and it is stored in the
process-wide slot registry under a class-qualified key (`C.x`). Every read
of the attribute - `C.x`, `obj.x`, `self.x` - resolves through that slot,
so the value is computed once and every read sees the same object, the
identity CPython's class dictionary gives.

The closed set is deliberately small:

* the value's type must be storable in a slot (`str`, `int`, `list`,
  `dict`, `Path`, a pointer class, or a callable): the runtime registry
  holds a str, an i64, or a pointer;
* the value must lower in the initializer's own scope: a call to a lowered
  function, a container literal, a comprehension, a name bound at module
  scope, or another class's attribute;
* an annotated attribute must match its annotation.

Everything else keeps its `PS1204` fence and routes to the island tier.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.errors import span_of
from pyscriptc.lower._core import Fence, _mk_type
from pyscriptc.stdmore import PTR_CLASSES

# The prefix of a synthesized class-attribute initializer. The name is
# module-local: a program refuses two classes of the same name across
# modules, so the key `C.x` is unique program-wide.
INIT_PREFIX = "__clsattr_"


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record."""
    return _mk_type(typ).kind


def storable_slot_type(typ: dict[str, Any]) -> bool:
    """True when a value of this type can live in a runtime slot.

    The registry holds a str, an i64, a path or a pointer. A list and a
    dict are pointers to a heap object, so they keep their identity
    through the slot. A float, a bool, a tuple and a user class instance
    have no slot form, so an attribute of those types keeps its fence.
    """
    kind = _type_kind(typ)
    if kind in {"str", "int", "list", "dict", "func"}:
        return True
    if kind == "class" and (typ.get("name") == "Path" or typ.get("name") in PTR_CLASSES):
        return True
    return False


def attr_slot_key(class_name: str, attr: str) -> str:
    """The runtime registry key one class attribute reads and writes."""
    return f"{class_name}.{attr}"


def slot_symbol(typ: dict[str, Any]) -> str:
    """The runtime symbol that stores a value of this type in a slot."""
    kind = _type_kind(typ)
    if kind == "str":
        return "ps_global_set_str"
    if kind == "int":
        return "ps_global_set_i64"
    return "ps_global_set_ptr"


def get_symbol(typ: dict[str, Any]) -> str:
    """The runtime symbol that reads a value of this type from a slot."""
    kind = _type_kind(typ)
    if kind == "str":
        return "ps_global_get_str"
    if kind == "int":
        return "ps_global_get_i64"
    return "ps_global_get_ptr"


def _names_in(value: ast.expr) -> set[str]:
    """Every plain name a value expression reads, including comprehension terms."""
    return {n.id for n in ast.walk(value) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class ClassAttrsMixin:
    """The class-attribute runtime path: the values, the slots, the reads.

    The mixin is composed into `ClassesMixin`, so every method stays
    reachable as `self._lower_class_attrs(...)`.
    """

    def _class_attr_value_type(self, stmt: ast.stmt) -> dict[str, Any] | None:
        """The annotated type of a class-body assignment, or None.

        Only an annotated assignment has a type before the value lowers.
        An unannotated one takes the value's own type, which the value
        lowering proves.
        """
        if not isinstance(stmt, ast.AnnAssign):
            return None
        return self._annotation(stmt.annotation)

    def _class_body_names(self, node: ast.ClassDef) -> set[str]:
        """The names the class body binds.

        A class body's names are not locals: they live in the class
        dictionary. A value that reads one of them (`b = a + 1` under
        `a = f()`) would need the class dictionary's own evaluation order,
        which the compile-time class has no room for, so it fences.
        """
        names: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
        return names

    def _lower_class_attrs(self, class_nodes: list[ast.ClassDef], fences: list[Fence]) -> list[dict[str, Any]]:
        """Lower every class's runtime attributes into its initializer.

        Each class with runtime attributes gets one synthesized module
        function: `__clsattr_<Class>`. Its body computes the attribute
        values in source order and stores each into the runtime slot keyed
        `C.<attr>`. The function is returned so the caller appends it to
        the module's functions and calls it at module init.

        A value that does not lower, or whose type has no slot form, keeps
        its `PS1204` fence: the class stays a fence and routes to the
        island tier.
        """
        inits: list[dict[str, Any]] = []
        for node in class_nodes:
            info = self.classes.get(node.name)
            if info is None:
                continue
            runtime_attrs = info.get("runtime_attrs")
            if not runtime_attrs:
                continue
            mark = len(fences)
            record = self._lower_class_attr_init(node, runtime_attrs, fences)
            if record is not None:
                inits.append(record)
                continue
            # boxed:begin
            # Issue #628: a class whose runtime attributes fence routes to
            # the island, and its fences drop with it: the island seed
            # defines the class in CPython with the real values. The
            # planner re-runs so a compiled caller of the now-island class
            # demotes with it.
            if self.fallback_definitions:
                self._boxed_island_class(node)
                del fences[mark:]
                self._boxed_replan(fences)
            # boxed:end
        return inits

    def _lower_class_attr_init(
        self,
        node: ast.ClassDef,
        runtime_attrs: list[tuple[str, ast.stmt]],
        fences: list[Fence],
    ) -> dict[str, Any] | None:
        """Lower one class's runtime attributes into its initializer function.

        The values lower in a function scope of their own: a class body has
        no locals, so a name the value reads is a module global or another
        class's attribute. The scope is a fresh one, so the value's temps
        never leak into the module.
        """
        name = f"{INIT_PREFIX}{node.name}"
        ir_name = self._ir_name(name)
        saved_function = self.function
        saved_locals = dict(self.locals)
        saved_prelude = self.prelude
        saved_temp_n = self.temp_n
        saved_globals = self.fn_globals
        saved_captures = self.captures
        saved_nonlocals = self.fn_nonlocals
        saved_opt_unwrap = dict(self.opt_unwrap)
        self.function = name
        self.locals = {}
        self.prelude = []
        self.temp_n = 0
        self.captures = {}
        self.fn_nonlocals = set()
        self.opt_unwrap = {}
        body: list[dict[str, Any]] = []
        types: dict[str, dict[str, Any]] = {}
        scope: dict[str, dict[str, Any]] = {}
        try:
            for attr, stmt in runtime_attrs:
                span = span_of(stmt, self.filename)
                value_node = stmt.value
                if value_node is None:
                    continue
                want = self._class_attr_value_type(stmt)
                try:
                    local = self._class_body_names(node)
                    for used in sorted(_names_in(value_node)):
                        if used in local:
                            self.fail(stmt, "PS1204", f"a class body value reads the class-local name {used}")
                    value = self._expr(value_node, want)
                    if want is not None:
                        self._expect(stmt, value["type"], want, attr)
                    if not storable_slot_type(value["type"]):
                        self.fail(stmt, "PS1204", f"{type(stmt).__name__} in a class body is not lowered")
                except Fence as exc:
                    fences.append(exc)
                    self.caught.append(exc)
                    continue
                key = attr_slot_key(node.name, attr)
                body.extend(self.prelude)
                self.prelude = []
                body.append(self._class_attr_store(key, value, span))
                types[attr] = value["type"]
                info = self.classes.get(node.name)
                if info is not None:
                    info.setdefault("runtime_attr_types", {})[attr] = value["type"]
            # The initializer's own locals are the value's temps: the scope
            # the values lowered in.
            scope = dict(self.locals)
        finally:
            self.function = saved_function
            self.locals = saved_locals
            self.prelude = saved_prelude
            self.temp_n = saved_temp_n
            self.fn_globals = saved_globals
            self.captures = saved_captures
            self.fn_nonlocals = saved_nonlocals
            self.opt_unwrap = saved_opt_unwrap
        if self.caught:
            return None
        return {
            "name": ir_name,
            "params": [],
            "returnType": {"kind": "none"},
            "locals": [{"name": local, "type": typ} for local, typ in scope.items()],
            "body": body,
            "span": span_of(node, self.filename),
        }

    def _class_attr_store(self, key: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """The statement that stores a class attribute into its slot."""
        from pyscriptc.lower import _text_ir

        set_call = self._abi_call(
            "builtins",
            "global_set",
            [_text_ir(key, "str", span), value],
            {"kind": "none"},
            slot_symbol(value["type"]),
            span,
        )
        return {"kind": "expr", "expr": set_call, "span": span}

    def _class_attr_init_call(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        """The call that runs a class's attribute initializer at module init.

        The call rides the module's global initializer list, so it runs
        before the program's entry and before any read of the attribute.
        """
        from pyscriptc.lower._classes import _function_call

        return _function_call(name, [], {"kind": "none"}, span)

    def _class_attr_read(self, owner: str, attr: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """The read of a class attribute through its runtime slot.

        The key is the *defining* class's: a subclass inherits the
        attribute, and the slot holds one value under the name of the
        class whose body bound it.
        """
        from pyscriptc.lower import _text_ir

        key = _text_ir(attr_slot_key(owner, attr), "str", span)
        return self._abi_call(
            "builtins",
            "global_get",
            [key],
            typ,
            get_symbol(typ),
            span,
        )

    def _class_runtime_attr(self, class_name: str, attr: str) -> tuple[str, dict[str, Any]] | None:
        """The (definer, type) of a class's runtime attribute `attr`, or None.

        The lookup follows the MRO the class dictionary does: the nearest
        ancestor that defines the attribute wins, and the definer's name is
        the slot key's class part. A method defined on the class itself
        keeps precedence over an attribute of the same name, the class
        dictionary's own-name rule.
        """
        info = self.classes.get(class_name)
        if info is None:
            return None
        if info.get("method_owner", {}).get(attr) == class_name:
            return None
        mro = info.get("mro") or [class_name]
        for ancestor in mro:
            ancestor_info = self.classes.get(ancestor)
            if ancestor_info is None:
                continue
            found = (ancestor_info.get("runtime_attr_types") or {}).get(attr)
            if found is not None:
                return ancestor, found
        return None
