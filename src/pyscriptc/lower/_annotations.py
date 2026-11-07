"""Typing-annotation lowering, split verbatim from `lower._classes`.

`AnnotationMixin` holds the annotation family plus the tuple, iterator and
optional shapes it owns; `ClassesMixin` composes it, so every method stays
reachable as `self._annotation(...)` and friends. The module-level helpers
(`_param`, `_type_slug` and friends) moved with the family so nothing here
back-imports `lower._classes`. Every body below is the code that lived in
`lower._classes`, unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any, NoReturn

from pyscriptc import _irnode as _ir
from pyscriptc.boxed import island_class_type
from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.ffi_manifest import handle_type
from pyscriptc.ir import type_key
from pyscriptc.lower._core import (
    _DICT_FORMS,
    _LIST_FORMS,
    _SET_FORMS,
    _UNRESOLVED_KIND,
    _show,
)
from pyscriptc.reflection import kwargs_parameter_note
from pyscriptc.restlower import alias_name
from pyscriptc.tirserde import dict_to_node, dict_to_span, dict_to_type, node_to_dict


def _type(typ: dict[str, Any]) -> dict[str, Any]:
    """The exact type dict one dict-IR type spells, via the typed record.

    Every type this module returns rounds through a `_irnode.T` record and
    back: `node_to_dict` reproduces the dict key for key, and `dict_to_type`
    refuses a shape the typed record cannot carry, so a drifted type dict
    fails here instead of at the emitter.
    """
    return node_to_dict(dict_to_type(typ))


_UNWRAP_FORMS = frozenset({"Final", "ClassVar", "Annotated", "Required", "NotRequired"})
_NEVER_ALIASES = frozenset({"NoReturn", "Never"})
_DYNAMIC_ANNOTATIONS = frozenset({"object", "Any", "Protocol", "TypeVar", "Generic", "Callable", "ABC"})
_IO_HANDLES = {"BinaryIO": "BytesIO", "TextIO": "TextIO"}


class AnnotationMixin:
    def _signature(
        self, fn: ast.FunctionDef, class_name: str | None, kind: str = "method"
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """The lowered signature of one `def`, or None for an unprovable one.

        The optional-failure mode serves the return-type inference peek
        (#500): a default peeked inside `_signature` sees parameters with
        no default at all (the default is only a value at the call site,
        never during the body), and any other fence means the signature
        never proves, so the peek returns None and the caller keeps its
        fence.
        """
        if fn.args.defaults and self.inf_peek:
            # The peek at this def's own return type: strip the defaults so
            # a typed default never doubles as the body's parameter type.
            saved_defaults = fn.args.defaults
            fn.args.defaults = []
            try:
                return self._signature(fn, class_name, kind)
            finally:
                fn.args.defaults = saved_defaults
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
            params.append({"name": "self", "type": _type({"kind": "class", "name": class_name})})
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
            params.append({"name": "self", "type": _type({"kind": "class", "name": class_name})})
            args = args[1:]
            pos_marks = pos_marks[1:]
        defaults = list(fn.args.defaults)
        missing = len(args) - len(defaults)
        for index, arg in enumerate(args):
            default = defaults[index - missing] if index >= missing else None
            param = _param(self, arg, fn, default)
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
            params.append({"name": vararg.arg, "type": _type({"kind": "list", "elem": elem}), "vararg": True})
        for karg, kdef in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
            param = _param(self, karg, fn, kdef)
            param["kwonly"] = True
            if kdef is not None:
                self._param_default(karg, kdef, param)
            params.append(param)
        if fn.args.kwarg is not None:
            kwarg = fn.args.kwarg
            if kwarg.annotation is None:
                self.fail(fn, "PS1202", f"{fn.name} declares **{kwarg.arg}: {kwargs_parameter_note()}")
            value = self._annotation(kwarg.annotation)
            if value["kind"] == "bool":
                self.fail(kwarg, "PS2002", "dict values of bool are not lowered")
            params.append(
                {
                    "name": kwarg.arg,
                    "type": _type({"kind": "dict", "key": {"kind": "str"}, "value": value}),
                    "kwarg": True,
                }
            )
        if fn.returns is None:
            # Inference from the body (issue #500): the return peek runs the
            # signature pass on this def and keeps the return type when the
            # body alone proves one. Defaults are stripped for the peek, so
            # a typed default never doubles as the body's parameter type
            # (the body sees call-site arguments, not defaults). No proof,
            # no fence from the peek: the caller keeps its own diagnostic.
            bridged = self._infer_signature(fn)
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

    def _reject_banned_type(self, node: ast.AST, typ: dict[str, Any]) -> None:
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
            return _type({"kind": "typevar", "name": node.id})
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            opt = self._optional_pipe(node)
            if opt is not None:
                return opt
            self.fail(node, "PS1101", "union types are not in the IR")
        formed = self._typing_annotation(node)
        if formed is not None:
            return formed
        if isinstance(node, ast.Attribute):
            # `typing.NoReturn` / `typing.Never` (issue #645): the qualified
            # spelling of the bottom type.
            resolved = self._resolve_module(node)
            if resolved is not None and resolved[3] == "erased" and resolved[2] in _NEVER_ALIASES:
                return _type({"kind": "none"})
        if isinstance(node, ast.Name) and node.id in {"int", "float", "bool", "str", "bytes"}:
            return _type({"kind": node.id})
        if isinstance(node, ast.Constant) and node.value is None:
            return _type({"kind": "none"})
        if isinstance(node, ast.Name) and (
            node.id in EXCEPTIONS or node.id == "BaseException" or self.classes.get(node.id, {}).get("exception")
        ):
            return _type({"kind": "exc"})
        # boxed:begin
        # An island class annotation is its handle type (issue #628): the
        # class lives in the embedded interpreter, and an instance crossed
        # into a compiled local is an opaque handle.
        if isinstance(node, ast.Name) and node.id in self.island_classes:
            return _type(island_class_type(node.id))
        # boxed:end
        if isinstance(node, ast.Name) and (node.id in self.declared or node.id in self.known_classes):
            return _type({"kind": "class", "name": self.class_aliases.get(node.id, node.id)})
        handle = self._handle_annotation(node)
        if handle is not None:
            return handle
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "list":
            typ = _type({"kind": "list", "elem": self._any_annotation(node.slice)})
            self._reject_banned_type(node, typ)
            return typ
        if isinstance(node, ast.Name) and (node.id == "Any" or node.id in _DYNAMIC_ANNOTATIONS):
            # Issue #781: bare `Any` and every other dynamic or structural
            # name still fences with PS1104. Only a container annotation
            # (dict[K, Any], list[Any]) resolves through the `any` element,
            # because there the shape is known.
            self.fail(node, "PS1104", f"{node.id} is a dynamic or structural type and is not lowered")
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "set":
            elem = self._annotation(node.slice)
            if elem["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "set elements are int or str")
            return _type({"kind": "list", "elem": elem, "set": True})
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "tuple":
            return self._tuple_annotation(node)
        aliased = self._aliased_annotation(node)
        if aliased is not None:
            return aliased
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "dict":
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "dict annotations are dict[key, value]")
            # Issue #817: the PEP 585 spelling matches the typing-module
            # spelling. The value slot goes through `_any_annotation` like the
            # Dict branch, so `dict[K, Any]` lowers with an unresolved value
            # instead of a PS1104 fence.
            typ = _type(
                {
                    "kind": "dict",
                    "key": self._annotation(node.slice.elts[0]),
                    "value": self._any_annotation(node.slice.elts[1]),
                }
            )
            self._reject_banned_type(node, typ)
            return typ
        named = self._annotation_class(node)
        if named is not None:
            return _type({"kind": "class", "name": named})
        inst = self._generic_alias_annotation(node)
        if inst is not None:
            return inst
        self._fail_annotation(node)

    def _any_annotation(self, node: ast.AST) -> dict[str, Any]:
        """The value slot of an Any container annotation (issue #781).

        A concrete type annotates through unchanged; bare `Any` becomes the
        `any` placeholder instead of a PS1104 fence: the container's shape is
        known (the key type, the element slots), only the value type is not,
        and every read of the container proves it from a local source or
        fences with PS1107 at the read site. `object` and the structural
        names (Protocol, TypeVar, Callable and friends) keep their own
        fence: they describe a shape, not an unresolved value.
        """
        if isinstance(node, ast.Name) and node.id == "Any":
            return _type({"kind": _UNRESOLVED_KIND})
        if isinstance(node, ast.Name) and node.id in _DYNAMIC_ANNOTATIONS:
            self.fail(node, "PS1104", f"{node.id} is a dynamic or structural type and is not lowered")
        return self._annotation(node)

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
        return _type({"kind": "class", "name": name})

    def _aliased_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        if not isinstance(node, ast.Subscript):
            return None
        alias = self._annotation_alias(node.value)
        if alias == "Deque":
            elem = self._annotation(node.slice)
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            return _type({"kind": "class", "name": "Deque", "elem": elem})
        if alias == "Task":
            # `asyncio.Task[T]`: the task handle carries the coroutine's
            # result type, so `await task` reads the right shape.
            elem = self._annotation(node.slice)
            return _type({"kind": "class", "name": "Task", "elem": elem})
        if alias == "OrderedDict":
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "OrderedDict annotations are OrderedDict[key, value]")
            typ = _type(
                {
                    "kind": "dict",
                    "key": self._annotation(node.slice.elts[0]),
                    "value": self._any_annotation(node.slice.elts[1]),
                }
            )
            self._reject_banned_type(node, typ)
            return typ
        if alias == "Counter":
            # `counter` marks the dict so a `c[k]` read yields 0 on a miss.
            typ = _type(
                {"kind": "dict", "key": self._annotation(node.slice), "value": {"kind": "int"}, "counter": True}
            )
            self._reject_banned_type(node, typ)
            return typ
        if alias == "defaultdict":
            # `defaultdict` marks the dict so a `d[k]` miss inserts a fresh
            # default (the zero of the value type) before the read.
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "defaultdict annotations are defaultdict[key, value]")
            typ = _type(
                {
                    "kind": "dict",
                    "key": self._annotation(node.slice.elts[0]),
                    "value": self._annotation(node.slice.elts[1]),
                    "defaultdict": True,
                }
            )
            self._reject_banned_type(node, typ)
            return typ
        if alias == "Queue":
            # The queue record holds element-sized slots, so the annotation
            # carries the element type the way a deque annotation does.
            elem = self._annotation(node.slice)
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            return _type({"kind": "class", "name": "Queue", "elem": elem})
        if alias == "AsyncQueue":
            # `asyncio.Queue[T]`: the same element-sized slots as the sync
            # queue, on the asyncio record.
            elem = self._annotation(node.slice)
            if elem["kind"] == "bool":
                self.fail(node, "PS2002", "list[bool] is not lowered")
            return _type({"kind": "class", "name": "AsyncQueue", "elem": elem})
        return None

    def _handle_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        """The IR type of a manifest handle annotation, or None.

        A handle is named two ways: imported (`from demo import Conn`, bound
        as a `handle` binding) or qualified (`demo.Conn`, an attribute of a
        manifest module). Both lower to the opaque `class` type the emitter
        rides as a `ptr` (issue #224).
        """
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None and binding.kind == "handle":
                return _type(handle_type(binding.attr))
            return None
        if isinstance(node, ast.Attribute):
            resolved = self._resolve_module(node)
            if resolved is None:
                return None
            _dotted, module, member, status = resolved
            if status not in {"lowered", "value"}:
                return None
            manifest = self._ffi_module(module)
            if manifest is None or manifest.handle(member) is None:
                return None
            return _type(handle_type(member))
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
            if binding.module == "asyncio" and binding.attr in {"Lock", "Event", "Semaphore"}:
                return alias_name(binding.module, binding.attr)
            if binding.module == "asyncio" and binding.attr == "Queue":
                return "AsyncQueue"
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
            if resolved[1] == "asyncio" and resolved[2] == "Task":
                return "Task"
            if resolved[1] == "asyncio" and resolved[2] in {"Lock", "Event", "Semaphore"}:
                return alias_name(resolved[1], resolved[2])
            if resolved[1] == "asyncio" and resolved[2] == "Queue":
                return "AsyncQueue"
        return None

    def _typing_annotation(self, node: ast.AST) -> dict[str, Any] | None:
        # `-> NoReturn` / `-> Never` (issue #645): the function never returns
        # a value to its caller. The IR has no bottom type, so the signature
        # carries `none`: callers read no result, and the body's exit (a
        # raise or an exit call) is what actually ends the process.
        if isinstance(node, ast.Name) and node.id in _NEVER_ALIASES:
            return _type({"kind": "none"})
        if isinstance(node, ast.Name) and node.id in self.type_aliases:
            alias = self.type_aliases[node.id]
            if alias in _NEVER_ALIASES:
                return _type({"kind": "none"})
            handle = _IO_HANDLES.get(alias)
            if handle is not None:
                return _type({"kind": "class", "name": handle})
            if alias == "Any":
                # Issue #817: bare `Any` is the unresolved leaf, the same
                # `any` kind an Any container's value slot already spells.
                # Reads of an `any`-typed value prove their type at the read
                # site or fence there; the annotation itself lowers.
                return _type({"kind": _UNRESOLVED_KIND})
            code = "PS1104" if alias in _DYNAMIC_ANNOTATIONS else "PS1103"
            self.fail(node, code, f"{alias} is not a lowered type in the IR")
        if not isinstance(node, ast.Subscript):
            return None
        alias = self._typing_alias(node.value)
        if alias is None:
            return None
        if alias == "IO":
            handle = _io_parameter(node.slice)
            if handle is None:
                self.fail(node, "PS1001", "IO is not a lowered type in the IR")
            return _type({"kind": "class", "name": handle})
        if alias in _LIST_FORMS:
            typ = _type({"kind": "list", "elem": self._any_annotation(node.slice)})
            self._reject_banned_type(node, typ)
            return typ
        if alias in _SET_FORMS:
            elem = self._annotation(node.slice)
            if elem["kind"] not in {"int", "str"}:
                self.fail(node, "PS2002", "set elements are int or str")
            return _type({"kind": "list", "elem": elem, "set": True})
        if alias in _DICT_FORMS:
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail(node, "PS1001", "Dict annotations are Dict[key, value]")
            typ = _type(
                {
                    "kind": "dict",
                    "key": self._annotation(node.slice.elts[0]),
                    "value": self._any_annotation(node.slice.elts[1]),
                }
            )
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
            return _type({"kind": "func", "params": params, "return": ret})
        if alias == "Tuple":
            return self._tuple_annotation(node)
        if alias == "Iterable" or alias == "Sequence":
            typ = _type({"kind": "list", "elem": self._annotation(node.slice)})
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
        self.fail(node, "PS1103", f"{alias} is not a lowered type in the IR")

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
                typ = _type({"kind": "list", "elem": elem})
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
        return _type({"kind": "class", "name": name, "label": label})

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
        self._reject_banned_type(node, _type({"kind": "list", "elem": elem}))
        name = f"iter_{_type_slug(elem)}"
        existing = self.classes.get(name)
        if existing is None:
            if name in self.declared:
                self.fail(node, "PS2002", f"{name} clashes with an iterator type")
            self.classes[name] = {
                "fields": [
                    {"name": "seq", "type": _type({"kind": "list", "elem": elem})},
                    {"name": "idx", "type": _type({"kind": "int"})},
                ],
                "methods": {},
                "synthetic": True,
                "iterator": True,
            }
            self.declared.add(name)
        elif not existing.get("iterator"):
            self.fail(node, "PS2002", f"{name} clashes with an iterator type")
        return _type({"kind": "class", "name": name, "elem": elem})

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
        return node_to_dict(
            _ir.attr(field["name"], dict_to_type(field["type"]), dict_to_node(value), dict_to_span(span))
        )

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
            return _type({"kind": "none"})
        if elem.get("optional"):
            return elem
        name = "opt_" + _type_slug(elem)
        existing = self.classes.get(name)
        if existing is None:
            if name in self.declared:
                self.fail(node, "PS2002", f"{name} clashes with an optional type")
            self.classes[name] = {
                "fields": [
                    {"name": "_tag", "type": _type({"kind": "bool"})},
                    {"name": "_val", "type": elem},
                ],
                "methods": {},
                "synthetic": True,
                "optional": True,
            }
            self.declared.add(name)
        elif not existing.get("optional"):
            self.fail(node, "PS2002", f"{name} clashes with an optional type")
        return _type({"kind": "class", "name": name, "label": _show(elem) + " | None", "optional": True, "elem": elem})


def _param(
    lowerer: Any, arg: ast.arg, fn: ast.FunctionDef | None = None, default: ast.expr | None = None
) -> dict[str, Any]:
    if arg.annotation is None:
        # Checker bridge: the checker inferred the parameter's type (from the
        # default, the callers, or the body). Without a bridged answer the
        # fence stands (PS1001, param-inference family). Inside the return
        # peek the refusal becomes a caught fence, so the peek returns None.
        if fn is not None:
            inferred = lowerer._bridge_param_type(fn, arg)
            if inferred is not None:
                return {"name": arg.arg, "type": inferred}
        # Issue #500: an unannotated parameter with a default of a proven
        # type binds to that type. The default is a value the call site may
        # rely on, so its own lowered type is the fact, not a guess: this is
        # CPython's `def f(x=0)` reading as `x: int`. A default the peek
        # cannot type (None, an empty display, a call that does not lower)
        # keeps the fence.
        if default is not None:
            inferred = lowerer._infer_type_of(default)
            if inferred is not None and inferred["kind"] not in {"none", "func"}:
                return {"name": arg.arg, "type": inferred}
        # Both paths raise PS1001; the return peek catches it and returns
        # None. An unannotated parameter is named by its own fence.
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
