"""Leaf definitions shared by every module in the `lower` package.

`Fence`, `Binding`, and the small pure helpers live here so sibling modules
(`_stdlib.py`, `__init__.py`) can import them without a cycle. Moved verbatim
from `lower.py`; no signature or behavior change.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.ir import types_equal


class Fence(Exception):
    def __init__(self, code: str, message: str, span: dict[str, Any], function: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span
        self.function = function

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "function": self.function,
            "span": self.span,
        }


class Binding:
    def __init__(
        self,
        kind: str,
        module: str,
        attr: str,
        queued: bool,
        typ: dict[str, Any] | None = None,
        rooted: bool = False,
    ) -> None:
        self.kind = kind
        self.module = module
        self.attr = attr
        self.queued = queued
        self.type = typ
        # True when `import urllib.parse` binds `urllib`, not the whole module.
        self.rooted = rooted


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


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


def _name(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


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


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}


def _const_none(span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": None, "type": {"kind": "none"}, "span": span}


def _const_truthy(value: dict[str, Any] | None) -> bool | None:
    """Python truthiness of a folded constant. None means not decidable."""
    if value is None:
        return None
    kind = value["kind"]
    if kind == "const":
        typ = value["type"]["kind"]
        if typ == "bool":
            return bool(value["value"])
        if typ == "int":
            return int(value["value"]) != 0
        if typ == "float":
            return float(value["value"]) != 0.0
        if typ in {"str", "bytes"}:
            return len(value["value"]) > 0
        if typ == "none":
            return False
        return None
    if kind == "list":
        return len(value["elems"]) > 0
    if kind == "dict":
        return len(value["pairs"]) > 0
    return None


def _module_closed(value: dict[str, Any]) -> bool:
    """A storable module constant: no `name` references anywhere inside.

    A `name` points at a prelude temp that only exists inside a function
    body, so storing one at module scope dangles. Every other kind re-emits
    at each read, which is what stored calls and literals already do.

    A value with `identity` set re-emits too, but must NOT: an argparse
    parser keeps runtime state (its registered options), and a list or dict
    keeps its elements, so every read has to be the same object. The
    assignment lowers inside the entry instead.
    """
    kind = value["kind"]
    if kind == "name":
        return False
    if value.get("identity"):
        return False
    if kind == "list":
        return all(_module_closed(elem) for elem in value["elems"])
    if kind == "tuple-const":
        return all(_module_closed(elem) for elem in value["elems"])
    if kind == "dict":
        return all(_module_closed(pair["key"]) and _module_closed(pair["value"]) for pair in value["pairs"])
    return True


def module_needs_slot(value: dict[str, Any]) -> bool:
    """A module value whose reads must be the same object every time.

    A container literal (list, dict) and a value already marked with
    `identity` (the argparse parser) hold runtime state. Re-emitting the
    constructor at every read would give each read a fresh copy, so the
    binding takes a global slot instead. A frozen set list keeps the
    constant path: the mutation fence on it needs each read to be a fresh
    copy, and a frozenset has no in-place state to lose.
    """
    if value.get("frozen") or value["kind"] not in {"list", "dict"}:
        return bool(value.get("identity"))
    return True


def _len_call(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }


def _binary_int(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "op": op, "left": left, "right": right, "type": {"kind": "int"}, "span": span}


def _compare(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": {"kind": "bool"}, "span": span}


_OP_SYMBOLS = {
    "Add": "+", "Sub": "-", "Mult": "*", "Div": "/", "FloorDiv": "//", "Mod": "%", "Pow": "**",
    "LShift": "<<", "RShift": ">>", "BitOr": "|", "BitAnd": "&", "BitXor": "^", "MatMult": "@",
    "UAdd": "+", "USub": "-", "Invert": "~", "Not": "not",
    "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=",
    "Is": "is", "IsNot": "is not", "In": "in", "NotIn": "not in",
}  # fmt: skip

_NODE_WORDS = {
    "JoinedStr": "f-string", "NamedExpr": "walrus assignment (:=)", "Starred": "starred expression",
    "Await": "await", "Yield": "yield", "YieldFrom": "yield from", "Slice": "slice",
    "Set": "set display", "Dict": "dict display", "Subscript": "subscript", "TemplateStr": "t-string",
    "Interpolation": "t-string interpolation",
}  # fmt: skip


def _op_text(op: ast.AST) -> str:
    """The operator as written in source (`*`, `not in`), not the AST class name (`Mult`)."""
    name = type(op).__name__
    return f"`{_OP_SYMBOLS[name]}`" if name in _OP_SYMBOLS else name


def _node_text(node: ast.AST) -> str:
    """A source-level name for an unsupported expression node, not the AST class name."""
    name = type(node).__name__
    return _NODE_WORDS.get(name, name)


def _show(typ: dict[str, Any]) -> str:
    if typ["kind"] == "list":
        head = "set" if typ.get("set") else "list"
        return f"{head}[{_show(typ['elem'])}]"
    if typ["kind"] == "dict":
        return f"dict[{_show(typ['key'])}, {_show(typ['value'])}]"
    if typ["kind"] == "class":
        return str(typ.get("label", typ["name"]))
    return str(typ["kind"])


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None


def _ir_kinds_of(node: ast.expr) -> set[str]:
    """The IR kinds one lowering pass over `node` could emit, structurally.

    The inference peek uses it to refuse expressions whose value is
    produced outside the emitted IR (abi thunks, handle frees and
    consumes): the peek lowers the expression a second time, and a runtime
    call must not run twice. The shape is a manifest ffi call, not a plain
    method dispatch: an attribute call whose receiver is a name is a value
    read plus a pure method, safe to lower twice; an attribute call on a
    computed receiver (a call result, a subscript) may be a handle verb.
    Attribute reads on a name (`mod.func(x)` style) are module calls, and
    so is any call whose callee is a bare name.
    """

    def _safe_receiver(value: ast.expr, depth: int) -> bool:
        if isinstance(value, ast.Name):
            return True
        if depth >= 4:
            return False
        if isinstance(value, ast.Call):
            # `g().method()`: the receiver's type comes from a lowered call
            # the peek evaluates speculatively, and the dispatch itself is
            # pure. Recurse into the callee shape instead of refusing.
            return _safe_callee(value.func, depth + 1)
        return False

    def _safe_callee(func: ast.expr, depth: int = 0) -> bool:
        if isinstance(func, ast.Name):
            return True
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Constant):
                # `'-'.join(parts)`: a str method dispatch, pure.
                return True
            if isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
                # `os.path.basename(p)`: a module chain callee, a lowered
                # rest call, not a handle verb on a computed receiver.
                return True
            return _safe_receiver(func.value, depth)
        return False

    found: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Call) and not _safe_callee(item.func):
            found.add("consume")
    return found


def _peek_annotated_params(lowerer: Any, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, dict[str, Any]]:
    """The typed parameters of one `def`, as an IR local table.

    The return peek lowers returned expressions to learn their types, so
    the body's typed parameters must resolve like they do in a real body.
    An annotation is the fact; a default of a proven type is the same fact
    the signature pass binds (`def f(times=3)` reads `times: int`). A
    parameter with neither stays out: the peek must not guess it.
    """

    table: dict[str, dict[str, Any]] = {}
    plain = fn.args.posonlyargs + fn.args.args
    missing = len(plain) - len(fn.args.defaults)
    for index, arg in enumerate(plain):
        if arg.annotation is not None:
            table[arg.arg] = _peek_annotation(arg.annotation)
        elif index >= missing:
            default = lowerer._infer_type_of(fn.args.defaults[index - missing])
            if default is not None and default["kind"] not in {"none", "func"}:
                table[arg.arg] = default
    for arg, default_node in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
        if arg.annotation is not None:
            table[arg.arg] = _peek_annotation(arg.annotation)
        elif default_node is not None:
            default = lowerer._infer_type_of(default_node)
            if default is not None and default["kind"] not in {"none", "func"}:
                table[arg.arg] = default
    return table


def _peek_annotation(node: ast.AST) -> dict[str, Any]:
    """The IR type of one parameter annotation, read structurally.

    Only the forms a returned expression can be checked against matter:
    the scalar kinds, list[T], dict[K, V], set[T] and tuple[...] over the
    same forms. Anything else (a class name the peek cannot resolve, a
    typing form) returns a poison type the returned expression never
    equals, so the peek refuses and the fence stands: never a guess.
    """

    if isinstance(node, ast.Name):
        if node.id in {"int", "float", "bool", "str", "bytes"}:
            return {"kind": node.id}
    if isinstance(node, ast.Constant) and node.value is None:
        return {"kind": "none"}
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        elem = _peek_annotation(node.slice) if not isinstance(node.slice, ast.Tuple) else None
        if node.value.id == "list" and elem is not None:
            return {"kind": "list", "elem": elem}
        if node.value.id == "set" and elem is not None and elem["kind"] in {"int", "str"}:
            return {"kind": "list", "elem": elem, "set": True}
        if node.value.id == "dict" and isinstance(node.slice, ast.Tuple) and len(node.slice.elts) == 2:
            key = _peek_annotation(node.slice.elts[0])
            value = _peek_annotation(node.slice.elts[1])
            if key["kind"] in {"int", "str"}:
                return {"kind": "dict", "key": key, "value": value}
        if node.value.id == "tuple":
            # A fixed-arity tuple lowers as a synthetic class keyed by its
            # field types; the peek has no class table to build one in, so
            # a tuple parameter refuses the peek rather than approximate.
            return {"kind": "__peek_unresolved__"}
    return {"kind": "__peek_unresolved__"}


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


_CMP = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}
_SCALAR = {"int", "float", "str", "bytes"}
_TRUTHY = frozenset({"str", "bytes", "int", "list", "dict"})
_LIST_FORMS = frozenset({"List"})
_DICT_FORMS = frozenset({"Dict"})
_SET_FORMS = frozenset({"Set"})


_FUTURE_OK = frozenset(
    {
        "annotations",
        "generator_stop",
        "nested_scopes",
        "generators",
        "division",
        "absolute_import",
        "with_statement",
        "print_function",
        "unicode_literals",
    }
)


def _package_base(module_name: str, is_package: bool, level: int) -> list[str] | None:
    if level < 1 or not module_name:
        return None
    bits = module_name.split(".")
    if not is_package:
        bits = bits[:-1]
    drop = level - 1
    if drop > len(bits):
        return None
    return bits[: len(bits) - drop]


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


def _zero(typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    if typ["kind"] == "int":
        return _const_int(0, span)
    if typ["kind"] == "bool":
        return _const_bool(False, span)
    if typ["kind"] == "float":
        return {"kind": "const", "value": "0.0", "type": typ, "span": span}
    if typ["kind"] == "list":
        return {"kind": "list", "elems": [], "type": typ, "span": span}
    if typ["kind"] == "dict":
        return {"kind": "dict", "pairs": [], "type": typ, "span": span}
    if typ["kind"] == "class":
        # Fresh for-targets of a synthetic tuple class start as a zeroed
        # instance (ps_alloc calloc); the loop step then rebinding every field.
        return {
            "kind": "call",
            "callee": {"kind": "class", "name": typ["name"]},
            "args": [],
            "type": typ,
            "span": span,
        }
    return {"kind": "const", "value": "", "type": typ, "span": span}


def _attr(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "attr", "value": value, "name": name, "type": typ, "span": span}


def _item(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "subscript", "value": value, "index": index, "type": typ, "span": span}


def _const_str(value: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": "str"}, "span": span}


def _const_float(value: float, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": repr(value), "type": {"kind": "float"}, "span": span}


def _const_bytes(value: bytes, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value.decode("latin1"), "type": {"kind": "bytes"}, "span": span}


_MISSING: Any = object()


def _raw_const(value: dict[str, Any] | None) -> Any:
    if not isinstance(value, dict) or value.get("kind") != "const":
        return _MISSING
    typ = value.get("type", {}).get("kind")
    val = value.get("value")
    if typ == "int":
        return int(val)
    if typ == "float":
        return float(val)
    if typ == "bool":
        return bool(val)
    if typ == "str":
        return str(val)
    if typ == "bytes":
        return val.encode("latin1") if isinstance(val, str) else bytes(val)
    if typ == "none":
        return None
    return _MISSING


def _make_const(val: Any, span: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(val, bool):
        return _const_bool(val, span)
    if isinstance(val, int):
        return _const_int(val, span)
    if isinstance(val, float):
        return _const_float(val, span)
    if isinstance(val, str):
        return _const_str(val, span)
    if isinstance(val, bytes):
        return _const_bytes(val, span)
    if val is None:
        return _const_none(span)
    return None


def _raise_value(message: dict[str, Any] | None, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "raise", "exc": "ValueError", "message": message, "span": span}


def _is_none_expr(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _declared_globals(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Global):
                names.update(node.names)
    return names


def _has_typevar(typ: dict[str, Any]) -> bool:
    """True when a type carries a `{"kind": "typevar"}` placeholder."""
    if typ["kind"] == "typevar":
        return True
    for key in ("elem", "key", "value", "return"):
        inner = typ.get(key)
        if isinstance(inner, dict) and _has_typevar(inner):
            return True
    return any(isinstance(param, dict) and _has_typevar(param) for param in typ.get("params", []))


def _has_any(typ: dict[str, Any]) -> bool:
    """True when a type carries an unresolved `{"kind": "any"}` element.

    A `dict[K, Any]` or `list[Any]` annotation lowers to a container whose
    element type is still unresolved (issue #814). The container's shape
    is known, its element type is not, and every read site has to prove
    it before the value can be loaded.
    """
    if typ["kind"] == "any":
        return True
    for key in ("elem", "key", "value", "return"):
        inner = typ.get(key)
        if isinstance(inner, dict) and _has_any(inner):
            return True
    return any(isinstance(param, dict) and _has_any(param) for param in typ.get("params", []))


def _resolve_any_elem(typ: dict[str, Any], elem: dict[str, Any]) -> dict[str, Any]:
    """A container type with its unresolved element type replaced by `elem`.

    The container keeps its shape (the key type, the list flag); only the
    element type changes. A type with no unresolved element is returned
    unchanged.
    """
    if typ["kind"] == "list":
        if not _has_any(typ["elem"]):
            return typ
        return {**typ, "elem": elem}
    if typ["kind"] == "dict":
        if not _has_any(typ["value"]):
            return typ
        return {**typ, "value": elem}
    return typ


def _accepts_any_elem(want: dict[str, Any], got: dict[str, Any]) -> bool:
    """Whether a concrete value satisfies an `any`-element container type.

    A `dict[K, Any]` parameter accepts a `dict[K, V]` value for any V:
    the annotation says the value type is not pinned, and the value
    carries one. The key type still has to match, because the key type
    is part of the container's shape (the runtime's map is keyed by it).
    """
    if not _has_any(want):
        return False
    if want["kind"] == "dict":
        if got["kind"] != "dict":
            return False
        if not types_equal(want["key"], got["key"]):
            return False
        return not _has_any(got["value"])
    if want["kind"] == "list":
        if got["kind"] != "list":
            return False
        if bool(want.get("set")) != bool(got.get("set")):
            return False
        return not _has_any(got["elem"])
    return False


def _sig_typevars(types: list[dict[str, Any]]) -> list[str]:
    """TypeVar names appearing in `types`, first-appearance order."""
    found: list[str] = []

    def walk(typ: dict[str, Any]) -> None:
        if typ["kind"] == "typevar":
            if typ["name"] not in found:
                found.append(typ["name"])
            return
        for key in ("elem", "key", "value", "return"):
            inner = typ.get(key)
            if isinstance(inner, dict):
                walk(inner)
        for param in typ.get("params", []):
            if isinstance(param, dict):
                walk(param)

    for typ in types:
        walk(typ)
    return found


def _subst_type(typ: dict[str, Any], mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace `{"kind": "typevar"}` placeholders by the mapped concrete type."""
    kind = typ["kind"]
    if kind == "typevar":
        return mapping.get(typ["name"], typ)
    if kind == "list":
        return {**typ, "elem": _subst_type(typ["elem"], mapping)}
    if kind == "dict":
        return {
            **typ,
            "key": _subst_type(typ["key"], mapping),
            "value": _subst_type(typ["value"], mapping),
        }
    if kind == "func":
        return {
            **typ,
            "params": [_subst_type(param, mapping) for param in typ["params"]],
            "return": _subst_type(typ["return"], mapping),
        }
    if "elem" in typ and isinstance(typ["elem"], dict):
        return {**typ, "elem": _subst_type(typ["elem"], mapping)}
    return typ
