"""Leaf definitions shared by every module in the `lower` package.

`Fence`, `Binding`, and the small pure helpers live here so sibling modules
(`_stdlib.py`, `__init__.py`) can import them without a cycle. Moved verbatim
from `lower.py`; no signature or behavior change.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc._irnode import P_BOOL, P_FLOAT, P_INT, P_NONE, P_STR, t_leaf, t_list
from pyscriptc.ir import types_equal
from pyscriptc.tirserde import dict_to_node, dict_to_span, dict_to_type, node_to_dict


def _mk_span(span: dict[str, Any]) -> Any:
    """The Span record the dict-IR span spells."""

    return dict_to_span(span)


def _mk_type(typ: dict[str, Any]) -> Any:
    """The T record the dict-IR type spells."""

    return dict_to_type(typ)


def _type_rt(typ: dict[str, Any]) -> dict[str, Any]:
    """The exact type dict one dict-IR type spells, through the T record."""

    return node_to_dict(dict_to_type(typ))


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record: int, float, bool, str, bytes, list,
    dict, class, exc, func, any or none. Every type dispatch reads the
    record through this accessor instead of the raw dict key."""

    kind: str = typ["kind"]
    return kind


def _maybe_type_kind(typ: dict[str, Any]) -> str | None:
    """The kind tag of a type record, or None when the record carries no
    kind. Optional-shape dispatch reads the record through this accessor
    instead of the raw dict key."""

    if "kind" not in typ:
        return None
    kind: str = typ["kind"]
    return kind


def _node_kind(node: dict[str, Any]) -> str:
    """The kind tag of a dict-IR node (assign, expr, raise, ...). Statement
    and expression dispatch reads the node through this accessor instead of
    the raw dict key."""

    kind: str = node["kind"]
    return kind


def _maybe_node_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of a dict-IR node, or None when the node carries no
    kind. Optional-shape dispatch reads the node through this accessor
    instead of the raw dict key."""

    if "kind" not in node:
        return None
    kind: str = node["kind"]
    return kind


def _node_type(node: dict[str, Any]) -> dict[str, Any]:
    """The type record a dict-IR node carries. A consumer of the node's
    type reads it through this accessor instead of the raw dict key."""

    typ: dict[str, Any] = node["type"]
    return typ


def _node_type_kind(node: dict[str, Any]) -> str:
    """The kind tag of the type record a dict-IR node carries. A dispatch
    on the node's own type reads it through this accessor instead of the
    raw nested dict key."""

    return _type_kind(_node_type(node))


def _maybe_node_type_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of the type record a dict-IR node carries, or None when
    the node carries no type record or the type record carries no kind."""

    if "type" not in node:
        return None
    return _maybe_type_kind(_node_type(node))


def _flag(record: dict[str, Any], name: str) -> bool:
    """The truthiness of an optional bool field on a dict-IR record
    (`identity`, `frozen`, `set`), or False when the field is absent."""

    if name not in record:
        return False
    flag: bool = record[name]
    return flag


def _dict_field(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The sub-record under `name`, or None when absent or not a dict."""

    if name not in record:
        return None
    field: dict[str, Any] = record[name]
    if not isinstance(field, dict):
        return None
    return field


def _list_field(record: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The list of sub-records under `name`, or [] when absent."""

    if name not in record:
        return []
    items: list[dict[str, Any]] = record[name]
    return items


def _const_str_value(node: dict[str, Any]) -> str:
    """The text payload of a str const node."""

    value: str = node["value"]
    return value


class Fence(Exception):
    def __init__(self, code: str, message: str, span: dict[str, Any], function: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span
        self.function = function

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["code"] = self.code
        out["message"] = self.message
        out["function"] = self.function
        out["span"] = self.span
        return out


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
    return node_to_dict(irnode_assign(target, value, span))


def irnode_assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> Any:
    return _ir.assign(target, dict_to_node(value), _mk_span(span))


def _const_str_join(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level f-string pieces that are already str constants are one str constant."""

    if any(_maybe_node_kind(part) != "const" or _maybe_node_type_kind(part) != "str" for part in parts):
        return None
    text = "".join(str(_const_str_value(part)) for part in parts)
    return _const_str(text, span)


def _concat_str(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    if not parts:
        return _const_str("", span)
    acc = parts[0]
    for part in parts[1:]:
        acc = node_to_dict(
            _ir.binary(
                "+",
                dict_to_node(acc),
                dict_to_node(part),
                t_leaf("str"),
                _mk_span(span),
            )
        )
    return acc


def _const_list_concat(left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """Two constant lists of one element type are one list. A runtime list stays fenced."""

    if _maybe_node_kind(left) != "list" or _maybe_node_kind(right) != "list":
        return None
    ltyp = _node_type(left)
    rtyp = _node_type(right)
    if _type_kind(ltyp) != "list" or _type_kind(rtyp) != "list":
        return None
    if _flag(ltyp, "set") or _flag(rtyp, "set"):
        return None
    lelem: dict[str, Any] = ltyp["elem"]
    relem: dict[str, Any] = rtyp["elem"]
    if not types_equal(lelem, relem):
        return None
    return node_to_dict(
        _ir.list_lit(
            [dict_to_node(elem) for elem in [*left["elems"], *right["elems"]]],
            t_list(dict_to_type(left["type"]["elem"])),
            _mk_span(span),
        )
    )


def _name(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.name_expr(name, dict_to_type(typ), _mk_span(span)))


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_node(P_INT, str(value), t_leaf("int"), _mk_span(span)))


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


def _static_int(expr: dict[str, Any]) -> int | None:
    if _maybe_node_kind(expr) == "const" and _maybe_node_type_kind(expr) == "int" and "value" in expr:
        value: str = expr["value"]
        if isinstance(value, str) and _int_text(value):
            return int(value)
    if _maybe_node_kind(expr) == "unary" and "op" in expr and "operand" in expr:
        op: str = expr["op"]
        operand: dict[str, Any] = expr["operand"]
        if op == "-" and isinstance(operand, dict):
            inner = _static_int(operand)
            if inner is not None:
                return -inner
    return None


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_node(P_BOOL, "true" if value else "false", t_leaf("bool"), _mk_span(span)))


def _const_none(span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_node(P_NONE, "", t_leaf("none"), _mk_span(span)))


def _const_truthy(value: dict[str, Any] | None) -> bool | None:
    """Python truthiness of a folded constant. None means not decidable."""
    if value is None:
        return None
    kind = _node_kind(value)
    if kind == "const":
        typ = _node_type_kind(value)
        if typ == "bool":
            payload: bool = value["value"]
            return bool(payload)
        if typ == "int":
            itext: str = value["value"]
            return int(itext) != 0
        if typ == "float":
            ftext: str = value["value"]
            return float(ftext) != 0.0
        if typ in {"str", "bytes"}:
            stext: str = value["value"]
            return len(stext) > 0
        if typ == "none":
            return False
        return None
    if kind == "list":
        elems: list[dict[str, Any]] = value["elems"]
        return len(elems) > 0
    if kind == "dict":
        pairs: list[dict[str, Any]] = value["pairs"]
        return len(pairs) > 0
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
    kind = _node_kind(value)
    if kind == "name":
        return False
    if _flag(value, "identity"):
        return False
    if kind in {"list", "tuple-const"}:
        elems: list[dict[str, Any]] = value["elems"]
        return all(_module_closed(elem) for elem in elems)
    if kind == "dict":
        pairs: list[dict[str, Any]] = value["pairs"]
        return all(_module_closed(pair["key"]) and _module_closed(pair["value"]) for pair in pairs)
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
    if _flag(value, "frozen") or _node_kind(value) not in {"list", "dict"}:
        return _flag(value, "identity")
    return True


def _len_call(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(
        _ir.call(
            _ir.builtin("len"),
            [dict_to_node(seq)],
            t_leaf("int"),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _binary_int(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.binary(op, dict_to_node(left), dict_to_node(right), t_leaf("int"), _mk_span(span)))


def _compare(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.compare(op, dict_to_node(left), dict_to_node(right), t_leaf("bool"), _mk_span(span)))


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
    kind = _type_kind(typ)
    if kind == "list":
        head = "set" if _flag(typ, "set") else "list"
        elem: dict[str, Any] = typ["elem"]
        return f"{head}[{_show(elem)}]"
    if kind == "dict":
        key: dict[str, Any] = typ["key"]
        val: dict[str, Any] = typ["value"]
        return f"dict[{_show(key)}, {_show(val)}]"
    if kind == "class":
        if "label" in typ:
            label: str = typ["label"]
            return str(label)
        name: str = typ["name"]
        return str(name)
    if kind == "any":
        return "Any"
    return str(kind)


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
        if node.id == "dict":
            # The bare container names peek as the same `any`-slot shape the
            # real annotation pass gives them (issue #483); a read the peek
            # cannot prove still refuses downstream.
            return {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "any"}}
        if node.id in {"list", "set"}:
            out: dict[str, Any] = {"kind": "list", "elem": {"kind": "any"}}
            if node.id == "set":
                out["set"] = True
            return out
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
# Issue #781: `dict[K, Any]` and `list[Any]` annotate as `any` elements; the
# container's shape (the key type, the element slots) is known, the value type
# is not, and every read proves it from a local source or fences with PS1107.
_UNRESOLVED_KIND = "any"
# Issue #483: the bare container names `list`, `dict` and `set` annotate as
# the container shape with `any` element slots. `tuple` stays out: a tuple's
# shape is its arity, and the bare name carries none.
_BARE_CONTAINER_NAMES = frozenset({"list", "dict", "set"})


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
    made: dict[str, Any] = node_to_dict(
        _ir.call(
            _ir.class_callee(dict_to_type(typ).name),
            [],
            dict_to_type(typ),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )
    stmts: list[dict[str, Any]] = [_assign(dest, made, span)]
    for index in range(len(elems)):
        elem = elems[index]
        stmts.append(
            node_to_dict(
                _ir.setattr_stmt(
                    _ir.name_expr(dest, dict_to_type(typ), _mk_span(span)),
                    f"_{index}",
                    dict_to_node(elem),
                    _mk_span(span),
                )
            )
        )
    return stmts


def _zero(typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    kind = _type_kind(typ)
    if kind == "int":
        return _const_int(0, span)
    if kind == "bool":
        return _const_bool(False, span)
    if kind == "float":
        return node_to_dict(_ir.const_node(P_FLOAT, "0.0", dict_to_type(typ), _mk_span(span)))
    if kind == "list":
        return node_to_dict(_ir.list_lit([], dict_to_type(typ), _mk_span(span)))
    if kind == "dict":
        return node_to_dict(_ir.dict_lit([], dict_to_type(typ), _mk_span(span)))
    if kind == "any":
        # The unresolved slot has no zero: `d: dict[str, Any]` with no
        # initializer starts as an empty dict under the container's own
        # annotation, never under the element slot, so this arm answers
        # only stray walks. An str zero keeps the const shape honest.
        out: dict[str, Any] = {}
        out["kind"] = "const"
        out["value"] = ""
        out["type"] = typ
        out["span"] = span
        return out
    if kind == "class":
        # Fresh for-targets of a synthetic tuple class start as a zeroed
        # instance (ps_alloc calloc); the loop step then rebinding every field.
        return node_to_dict(
            _ir.call(
                _ir.class_callee(dict_to_type(typ).name),
                [],
                dict_to_type(typ),
                _mk_span(span),
                "",
                "",
                -1,
            )
        )
    return node_to_dict(_ir.const_node(P_STR, "", dict_to_type(typ), _mk_span(span)))


def _attr(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.attr(name, dict_to_type(typ), dict_to_node(value), _mk_span(span)))


def _item(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.subscript(dict_to_node(value), dict_to_node(index), dict_to_type(typ), _mk_span(span)))


def _const_str(value: str, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_str(value, t_leaf("str"), _mk_span(span)))


def _const_float(value: float, span: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["kind"] = "const"
    out["value"] = repr(value)
    out["type"] = {"kind": "float"}
    out["span"] = span
    return out


def _const_bytes(value: bytes, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value.decode("latin1"), "type": {"kind": "bytes"}, "span": span}


_MISSING: dict[str, Any] = {}


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
    return node_to_dict(_ir.raise_stmt("ValueError", _mk_span(span), -1, dict_to_node(message)))


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
    if _type_kind(typ) == "typevar":
        return True
    for key in ("elem", "key", "value", "return"):
        inner = _dict_field(typ, key)
        if inner is not None and _has_typevar(inner):
            return True
    return any(isinstance(param, dict) and _has_typevar(param) for param in _list_field(typ, "params"))


def _sig_typevars(types: list[dict[str, Any]]) -> list[str]:
    """TypeVar names appearing in `types`, first-appearance order."""
    found: list[str] = []

    def walk(typ: dict[str, Any]) -> None:
        if _type_kind(typ) == "typevar":
            name: str = typ["name"]
            if name not in found:
                found.append(name)
            return
        for key in ("elem", "key", "value", "return"):
            inner = _dict_field(typ, key)
            if inner is not None:
                walk(inner)
        for param in _list_field(typ, "params"):
            if isinstance(param, dict):
                walk(param)

    for typ in types:
        walk(typ)
    return found


def _subst_type(typ: dict[str, Any], mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace `{"kind": "typevar"}` placeholders by the mapped concrete type."""
    kind = _type_kind(typ)
    if kind == "typevar":
        name: str = typ["name"]
        return mapping.get(name, typ)
    tail_elem = _dict_field(typ, "elem")
    if kind not in {"list", "dict", "func"} and tail_elem is None:
        return typ
    out: dict[str, Any] = {}
    out.update(typ)
    if kind == "list":
        out["elem"] = _subst_type(typ["elem"], mapping)
    elif kind == "dict":
        out["key"] = _subst_type(typ["key"], mapping)
        out["value"] = _subst_type(typ["value"], mapping)
    elif kind == "func":
        params: list[dict[str, Any]] = typ["params"]
        out["params"] = [_subst_type(param, mapping) for param in params]
        out["return"] = _subst_type(typ["return"], mapping)
    elif tail_elem is not None:
        out["elem"] = _subst_type(tail_elem, mapping)
    return out


def _any_accepts(want: dict[str, Any], got: dict[str, Any]) -> bool:
    """An `any` slot (an Any container's value, issue #781) accepts every
    concrete value; the read site proves the value's type on its own."""
    return _any_contains(want) and not _any_contains(got)


def _any_contains(typ: dict[str, Any]) -> bool:
    """True when a type carries the `any` unresolved slot (issue #781)."""
    if _maybe_type_kind(typ) == "any":
        return True
    for key in ("elem", "key", "value", "return"):
        inner = _dict_field(typ, key)
        if inner is not None and _any_contains(inner):
            return True
    return any(isinstance(param, dict) and _any_contains(param) for param in _list_field(typ, "params"))
