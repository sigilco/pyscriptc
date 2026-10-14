"""JSON IR for the spike subset.

The schema is Python's, not scriptc's. `int` is a Python int (stored as a
decimal string so JSON cannot round it). JavaScript kinds such as `f64` and
`nullT` are rejected.
"""

from __future__ import annotations

import sys
from typing import Any

from pyscriptc.errors import IRError, VersionError

IR_VERSION = 1
SUBSET = "gates-v1"

TYPE_KINDS = {"int", "float", "bool", "none", "str", "bytes", "list", "dict", "class", "unknown"}
EXPR_KINDS = {
    "name",
    "const",
    "unary",
    "binary",
    "compare",
    "call",
    "list",
    "dict",
    "subscript",
    "attr",
    "cast",
    "contains",
}
STMT_KINDS = {"assign", "if", "while", "return", "expr", "pass", "setitem", "setattr", "try", "raise"}
# Kinds that would import a JavaScript object model. Named so a reused
# scriptc node fails closed instead of being misread.
FORBIDDEN_KINDS = {
    "f64",
    "bigint",
    "nullT",
    "undefinedT",
    "jsval",
    "dyn",
    "map",
    "array",
    "httpReq",
    "promise",
    "date",
}

I64_MIN = -9223372036854775808
I64_MAX = 9223372036854775807


def cpython_version() -> str:
    return sys.version.split()[0]


def type_key(typ: dict[str, Any]) -> str:
    if typ["kind"] == "list":
        head = "set" if typ.get("set") else "list"
        return f"{head}[{type_key(typ['elem'])}]"
    if typ["kind"] == "dict":
        return f"dict[{type_key(typ['key'])}, {type_key(typ['value'])}]"
    if typ["kind"] == "class":
        if "elem" in typ:
            return f"{typ['name']}[{type_key(typ['elem'])}]"
        return str(typ["name"])
    return str(typ["kind"])


def types_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return type_key(a) == type_key(b)


def fits_i64(value: int) -> bool:
    return I64_MIN <= value <= I64_MAX


def _reject_forbidden(kind: str, where: str) -> None:
    if kind in FORBIDDEN_KINDS:
        raise IRError(f"{where}: kind {kind!r} is a JavaScript/scriptc type and is not in {SUBSET}")


def validate_type(typ: Any, where: str) -> None:
    if not isinstance(typ, dict) or "kind" not in typ:
        raise IRError(f"{where}: type must be an object with kind")
    kind = typ["kind"]
    _reject_forbidden(kind, where)
    if kind not in TYPE_KINDS:
        raise IRError(f"{where}: unknown type kind {kind!r}")
    if kind == "list":
        if "elem" not in typ:
            raise IRError(f"{where}: list type needs elem")
        validate_type(typ["elem"], f"{where}.elem")
    elif kind == "dict":
        if "key" not in typ or "value" not in typ:
            raise IRError(f"{where}: dict type needs key and value")
        validate_type(typ["key"], f"{where}.key")
        validate_type(typ["value"], f"{where}.value")
    elif kind == "class":
        if not isinstance(typ.get("name"), str):
            raise IRError(f"{where}: class type needs a name")
        if "elem" in typ:
            validate_type(typ["elem"], f"{where}.elem")


def validate_expr(expr: Any, where: str) -> None:
    if not isinstance(expr, dict) or "kind" not in expr:
        raise IRError(f"{where}: expression must be an object with kind")
    kind = expr["kind"]
    _reject_forbidden(kind, where)
    if kind not in EXPR_KINDS:
        raise IRError(f"{where}: unknown expression kind {kind!r}")
    validate_type(expr.get("type"), f"{where}.type")
    if "span" not in expr:
        raise IRError(f"{where}: expression needs a span")
    if kind == "name":
        if not isinstance(expr.get("id"), str):
            raise IRError(f"{where}: name needs id")
    elif kind == "const":
        if "value" not in expr:
            raise IRError(f"{where}: const needs value")
    elif kind == "unary":
        if expr.get("op") not in {"-", "not"}:
            raise IRError(f"{where}: unsupported unary op")
        validate_expr(expr.get("operand"), f"{where}.operand")
    elif kind == "binary":
        if expr.get("op") not in {"+", "-", "*", "//"}:
            raise IRError(f"{where}: unsupported binary op")
        validate_expr(expr.get("left"), f"{where}.left")
        validate_expr(expr.get("right"), f"{where}.right")
    elif kind == "compare":
        if expr.get("op") not in {"<", "<=", ">", ">=", "==", "!="}:
            raise IRError(f"{where}: unsupported compare")
        validate_expr(expr.get("left"), f"{where}.left")
        validate_expr(expr.get("right"), f"{where}.right")
    elif kind == "call":
        _validate_callee(expr.get("callee"), where)
        args = expr.get("args")
        if not isinstance(args, list):
            raise IRError(f"{where}: call needs args")
        for i, arg in enumerate(args):
            validate_expr(arg, f"{where}.args[{i}]")
    elif kind == "list":
        elems = expr.get("elems")
        if not isinstance(elems, list):
            raise IRError(f"{where}: list needs elems")
        for i, elem in enumerate(elems):
            validate_expr(elem, f"{where}.elems[{i}]")
    elif kind == "dict":
        pairs = expr.get("pairs")
        if not isinstance(pairs, list):
            raise IRError(f"{where}: dict needs pairs")
        for i, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                raise IRError(f"{where}.pairs[{i}] must be an object")
            validate_expr(pair.get("key"), f"{where}.pairs[{i}].key")
            validate_expr(pair.get("value"), f"{where}.pairs[{i}].value")
    elif kind == "subscript":
        validate_expr(expr.get("value"), f"{where}.value")
        validate_expr(expr.get("index"), f"{where}.index")
    elif kind == "attr":
        validate_expr(expr.get("value"), f"{where}.value")
        if not isinstance(expr.get("name"), str):
            raise IRError(f"{where}: attr needs a name")
    elif kind == "cast":
        if expr.get("op") != "int_to_float":
            raise IRError(f"{where}: unsupported cast")
        validate_expr(expr.get("value"), f"{where}.value")
    elif kind == "contains":
        validate_expr(expr.get("hay"), f"{where}.hay")
        validate_expr(expr.get("needle"), f"{where}.needle")
        if not isinstance(expr.get("negated"), bool):
            raise IRError(f"{where}: contains needs negated")


def validate_stmt(stmt: Any, where: str) -> None:
    if not isinstance(stmt, dict) or "kind" not in stmt:
        raise IRError(f"{where}: statement must be an object with kind")
    kind = stmt["kind"]
    _reject_forbidden(kind, where)
    if kind not in STMT_KINDS:
        raise IRError(f"{where}: unknown statement kind {kind!r}")
    if "span" not in stmt:
        raise IRError(f"{where}: statement needs a span")
    if kind == "assign":
        if not isinstance(stmt.get("target"), str):
            raise IRError(f"{where}: assign needs a target name")
        validate_expr(stmt.get("value"), f"{where}.value")
    elif kind == "if":
        validate_expr(stmt.get("cond"), f"{where}.cond")
        _validate_body(stmt.get("then"), f"{where}.then")
        _validate_body(stmt.get("else"), f"{where}.else")
    elif kind == "while":
        validate_expr(stmt.get("cond"), f"{where}.cond")
        _validate_body(stmt.get("body"), f"{where}.body")
    elif kind == "return":
        validate_expr(stmt.get("value"), f"{where}.value")
    elif kind == "expr":
        validate_expr(stmt.get("expr"), f"{where}.expr")
    elif kind == "pass":
        return
    elif kind == "setitem":
        validate_expr(stmt.get("container"), f"{where}.container")
        validate_expr(stmt.get("index"), f"{where}.index")
        validate_expr(stmt.get("value"), f"{where}.value")
    elif kind == "setattr":
        validate_expr(stmt.get("object"), f"{where}.object")
        if not isinstance(stmt.get("name"), str):
            raise IRError(f"{where}: setattr needs a name")
        validate_expr(stmt.get("value"), f"{where}.value")
    elif kind == "raise":
        if not isinstance(stmt.get("exc"), str):
            raise IRError(f"{where}: raise needs an exception name")
        message = stmt.get("message")
        if message is not None:
            validate_expr(message, f"{where}.message")
    elif kind == "try":
        _validate_body(stmt.get("body"), f"{where}.body")
        handlers = stmt.get("handlers")
        if not isinstance(handlers, list) or not handlers:
            raise IRError(f"{where}: try needs handlers")
        for i, handler in enumerate(handlers):
            if not isinstance(handler, dict) or not isinstance(handler.get("exc"), list):
                raise IRError(f"{where}.handlers[{i}] needs exc names")
            _validate_body(handler.get("body"), f"{where}.handlers[{i}].body")


def _validate_callee(callee: Any, where: str) -> None:
    if not isinstance(callee, dict):
        raise IRError(f"{where}: call callee must be an object")
    kind = callee.get("kind")
    if kind in {"function", "builtin", "class"}:
        if not isinstance(callee.get("name"), str):
            raise IRError(f"{where}: call callee needs a name")
        return
    if kind == "module":
        if not isinstance(callee.get("module"), str) or not isinstance(callee.get("name"), str):
            raise IRError(f"{where}: module callee needs module and name")
        return
    if kind == "method":
        if callee.get("receiver") not in {"str", "bytes"} or not isinstance(callee.get("name"), str):
            raise IRError(f"{where}: method callee needs a str or bytes receiver and a name")
        return
    raise IRError(f"{where}: call callee must be function, builtin, module, method, or class")


def _validate_body(body: Any, where: str) -> None:
    if not isinstance(body, list):
        raise IRError(f"{where}: body must be a list")
    for i, stmt in enumerate(body):
        validate_stmt(stmt, f"{where}[{i}]")


def validate_module(module: Any) -> None:
    if not isinstance(module, dict):
        raise IRError("module must be an object")
    if module.get("irVersion") != IR_VERSION:
        raise IRError(f"irVersion must be {IR_VERSION}")
    if module.get("subset") != SUBSET:
        raise IRError(f"subset must be {SUBSET!r}")
    if not isinstance(module.get("cpython"), str):
        raise IRError("cpython version string is required")
    if not isinstance(module.get("sourceFile"), str):
        raise IRError("sourceFile is required")
    functions = module.get("functions")
    if not isinstance(functions, list):
        raise IRError("functions must be a list")
    names: set[str] = set()
    for i, fn in enumerate(functions):
        if not isinstance(fn, dict):
            raise IRError(f"functions[{i}] must be an object")
        name = fn.get("name")
        if not isinstance(name, str) or not _dotted_identifier(name):
            raise IRError(f"functions[{i}] needs an identifier name")
        if name in names:
            raise IRError(f"duplicate function {name}")
        names.add(name)
        validate_type(fn.get("returnType"), f"{name}.returnType")
        params = fn.get("params")
        if not isinstance(params, list):
            raise IRError(f"{name}: params must be a list")
        for j, param in enumerate(params):
            if not isinstance(param, dict) or not isinstance(param.get("name"), str):
                raise IRError(f"{name}.params[{j}] needs a name")
            validate_type(param.get("type"), f"{name}.params[{j}].type")
        locals_ = fn.get("locals")
        if not isinstance(locals_, list):
            raise IRError(f"{name}: locals must be a list")
        for j, local in enumerate(locals_):
            if not isinstance(local, dict) or not isinstance(local.get("name"), str):
                raise IRError(f"{name}.locals[{j}] needs a name")
            validate_type(local.get("type"), f"{name}.locals[{j}].type")
        _validate_body(fn.get("body"), f"{name}.body")
    classes = module.get("classes")
    if not isinstance(classes, list):
        raise IRError("classes must be a list")
    class_names: set[str] = set()
    for i, cls in enumerate(classes):
        if not isinstance(cls, dict) or not isinstance(cls.get("name"), str):
            raise IRError(f"classes[{i}] needs a name")
        if cls["name"] in class_names or cls["name"] in names:
            raise IRError(f"duplicate class {cls['name']}")
        class_names.add(cls["name"])
        fields = cls.get("fields")
        if not isinstance(fields, list):
            raise IRError(f"{cls['name']}: fields must be a list")
        for j, field in enumerate(fields):
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                raise IRError(f"{cls['name']}.fields[{j}] needs a name")
            validate_type(field.get("type"), f"{cls['name']}.fields[{j}].type")
        methods = cls.get("methods")
        if not isinstance(methods, list):
            raise IRError(f"{cls['name']}: methods must be a list")
        for j, method in enumerate(methods):
            if not isinstance(method, dict) or not isinstance(method.get("name"), str):
                raise IRError(f"{cls['name']}.methods[{j}] needs a name")
            label = f"{cls['name']}.{method['name']}"
            validate_type(method.get("returnType"), f"{label}.returnType")
            params = method.get("params")
            if not isinstance(params, list):
                raise IRError(f"{label}: params must be a list")
            for k, param in enumerate(params):
                if not isinstance(param, dict) or not isinstance(param.get("name"), str):
                    raise IRError(f"{label}.params[{k}] needs a name")
                validate_type(param.get("type"), f"{label}.params[{k}].type")
            locals_ = method.get("locals")
            if not isinstance(locals_, list):
                raise IRError(f"{label}: locals must be a list")
            _validate_body(method.get("body"), f"{label}.body")
    entry = module.get("entry")
    if functions and entry not in names:
        raise IRError("entry must name a function")
    if not functions and entry is not None:
        raise IRError("entry must be null when there are no functions")
    fences = module.get("fences", [])
    if not isinstance(fences, list):
        raise IRError("fences must be a list")
    for i, fence in enumerate(fences):
        if not isinstance(fence, dict) or not isinstance(fence.get("code"), str):
            raise IRError(f"fences[{i}] needs a code")
        if "message" not in fence or "span" not in fence:
            raise IRError(f"fences[{i}] needs a message and span")


def _dotted_identifier(name: str) -> bool:
    parts = name.split(".")
    return bool(parts) and all(part.isidentifier() for part in parts)


def assert_same_cpython(module: dict[str, Any]) -> None:
    """LLVM emission refuses an IR stamped by a different CPython.

    Re-run the corpus on the new interpreter, then lower again. The stamp is
    not a hint the backend may ignore.
    """

    validate_module(module)
    running = cpython_version()
    stamped = module["cpython"]
    if stamped != running:
        raise VersionError(
            f"IR was lowered on CPython {stamped}, this process is {running}; "
            "re-lower and re-run the corpus before emitting LLVM"
        )
