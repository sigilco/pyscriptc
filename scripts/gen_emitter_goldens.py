"""Build the emitter golden corpus under `tests/fixtures/backend/`.

Each fixture is a pair: `<name>.ir.json` (the IR module the emitter sees) and
`<name>.ll` (the byte-exact LLVM text the emitter must produce). The corpus
covers every IR node kind the emitter can encounter, plus the structural
cases (class layout, env closure, the main entry, the exception registration
preamble).

Run to regenerate the corpus:

    uv run python scripts/gen_emitter_goldens.py

The goldens are committed; this script only exists so an intentional emitter
change can be re-blessed and reviewed as a diff. The test that consumes them
is `tests/test_emitter_goldens.py`, which also has an update flag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pyscriptc.emitter import emit_ir_text, make_module  # noqa: E402
from pyscriptc.ir import cpython_version  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "backend" / "llvm"
MANIFEST = ROOT / "tests" / "fixtures" / "backend" / "manifest.json"

SPAN = {"file": "<golden>", "line": 1, "col": 1, "endLine": 1, "endCol": 2}


def typ(kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, **extra}


def expr(kind: str, type_: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "type": type_, "span": dict(SPAN), **fields}


def stmt(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "span": dict(SPAN), **fields}


def fn(
    name: str,
    body: list[dict[str, Any]],
    params: list[dict[str, Any]] | None = None,
    locals_: list[dict[str, Any]] | None = None,
    return_type: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "params": params or [],
        "returnType": return_type or typ("none"),
        "locals": locals_ or [],
        "body": body,
        **extra,
    }


def const(value: Any, kind: str = "int") -> dict[str, Any]:
    return expr("const", typ(kind), value=value)


def name(id_: str, kind: str = "int") -> dict[str, Any]:
    return expr("name", typ(kind), id=id_)


def binary(op: str, left: dict[str, Any], right: dict[str, Any], kind: str = "int") -> dict[str, Any]:
    return expr("binary", typ(kind), op=op, left=left, right=right)


def compare(op: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return expr("compare", typ("bool"), op=op, left=left, right=right)


def list_type(elem: dict[str, Any] | None = None) -> dict[str, Any]:
    return typ("list", elem=elem or typ("int"))


def func_type() -> dict[str, Any]:
    return typ("func", params=[], **{"return": typ("int")})


def module(
    functions: list[dict[str, Any]],
    classes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return make_module(
        functions,
        classes=classes,
        entry=functions[0]["name"] if functions else None,
        source_file="<golden>",
    )


def point_class() -> dict[str, Any]:
    return {"name": "Point", "fields": [{"name": "x", "type": typ("int")}], "methods": []}


# --- expression kinds -------------------------------------------------------


def _fixture_const() -> dict[str, Any]:
    return module(
        [
            fn(
                "const_int",
                [stmt("assign", target="x", value=const("7"))],
                locals_=[{"name": "x", "type": typ("int")}],
            )
        ]
    )


def _fixture_name() -> dict[str, Any]:
    return module(
        [
            fn(
                "name_expr",
                [stmt("assign", target="y", value=expr("name", typ("int"), id="x"))],
                locals_=[
                    {"name": "x", "type": typ("int")},
                    {"name": "y", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_unary() -> dict[str, Any]:
    return module(
        [
            fn(
                "unary_neg",
                [stmt("assign", target="y", value=expr("unary", typ("int"), op="-", operand=const("3")))],
                locals_=[{"name": "y", "type": typ("int")}],
            )
        ]
    )


def _fixture_binary() -> dict[str, Any]:
    return module(
        [
            fn(
                "binary_add",
                [stmt("assign", target="y", value=binary("+", name("a"), name("b")))],
                params=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                ],
                locals_=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                    {"name": "y", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_compare() -> dict[str, Any]:
    return module(
        [
            fn(
                "compare_lt",
                [stmt("assign", target="y", value=compare("<", name("a"), name("b")))],
                params=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                ],
                locals_=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                    {"name": "y", "type": typ("bool")},
                ],
            )
        ]
    )


def _fixture_call() -> dict[str, Any]:
    return module(
        [
            fn(
                "call_helper",
                [
                    stmt(
                        "assign",
                        target="y",
                        value=expr("call", typ("int"), callee={"kind": "function", "name": "helper"}, args=[]),
                    )
                ],
                locals_=[{"name": "y", "type": typ("int")}],
            ),
            fn("helper", [stmt("return", value=const("0"))], return_type=typ("int")),
        ]
    )


def _fixture_list() -> dict[str, Any]:
    return module(
        [
            fn(
                "list_expr",
                [
                    stmt(
                        "assign",
                        target="xs",
                        value=expr("list", list_type(), elems=[const("1"), const("2")]),
                    )
                ],
                locals_=[{"name": "xs", "type": list_type()}],
            )
        ]
    )


def _fixture_dict() -> dict[str, Any]:
    dict_type = typ("dict", key=typ("str"), value=typ("int"))
    return module(
        [
            fn(
                "dict_expr",
                [
                    stmt(
                        "assign",
                        target="d",
                        value=expr(
                            "dict",
                            dict_type,
                            pairs=[{"key": const("k", "str"), "value": const("1")}],
                        ),
                    )
                ],
                locals_=[{"name": "d", "type": dict_type}],
            )
        ]
    )


def _fixture_subscript() -> dict[str, Any]:
    return module(
        [
            fn(
                "subscript_expr",
                [
                    stmt(
                        "assign",
                        target="v",
                        value=expr(
                            "subscript",
                            typ("int"),
                            value=expr("name", list_type(), id="xs"),
                            index=const("0"),
                        ),
                    )
                ],
                locals_=[
                    {"name": "xs", "type": list_type()},
                    {"name": "v", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_attr() -> dict[str, Any]:
    return module(
        [
            fn(
                "attr_expr",
                [
                    stmt(
                        "assign",
                        target="v",
                        value=expr(
                            "attr",
                            typ("int"),
                            value=expr("name", typ("class", name="Point"), id="p"),
                            name="x",
                        ),
                    )
                ],
                locals_=[
                    {"name": "p", "type": typ("class", name="Point")},
                    {"name": "v", "type": typ("int")},
                ],
            )
        ],
        classes=[point_class()],
    )


def _fixture_cast() -> dict[str, Any]:
    return module(
        [
            fn(
                "cast_int_to_float",
                [
                    stmt(
                        "assign",
                        target="f",
                        value=expr("cast", typ("float"), op="int_to_float", value=const("2")),
                    )
                ],
                locals_=[{"name": "f", "type": typ("float")}],
            )
        ]
    )


def _fixture_contains() -> dict[str, Any]:
    return module(
        [
            fn(
                "contains_expr",
                [
                    stmt(
                        "assign",
                        target="has",
                        value=expr(
                            "contains",
                            typ("bool"),
                            hay=expr("name", list_type(), id="xs"),
                            needle=const("1"),
                            negated=False,
                        ),
                    )
                ],
                locals_=[
                    {"name": "xs", "type": list_type()},
                    {"name": "has", "type": typ("bool")},
                ],
            )
        ]
    )


def _fixture_closure() -> dict[str, Any]:
    return module(
        [
            fn(
                "outer",
                [
                    stmt("assign", target="x", value=const("1")),
                    stmt(
                        "assign",
                        target="inner",
                        value=expr(
                            "closure",
                            func_type(),
                            name="outer.inner",
                            env=[{"name": "x", "type": typ("int")}],
                        ),
                    ),
                ],
                locals_=[
                    {"name": "x", "type": typ("int")},
                    {"name": "inner", "type": func_type()},
                ],
            ),
            fn(
                "outer.inner",
                [stmt("return", value=name("x"))],
                locals_=[{"name": "x", "type": typ("int")}],
                return_type=typ("int"),
                env=[{"name": "x", "type": typ("int")}],
            ),
        ]
    )


# --- statement kinds --------------------------------------------------------


def _fixture_assign() -> dict[str, Any]:
    return module(
        [
            fn(
                "assign_int",
                [stmt("assign", target="x", value=const("1"))],
                locals_=[{"name": "x", "type": typ("int")}],
            )
        ]
    )


def _fixture_if() -> dict[str, Any]:
    return module(
        [
            fn(
                "if_stmt",
                [
                    stmt(
                        "if",
                        cond=compare("<", name("a"), name("b")),
                        then=[stmt("assign", target="x", value=const("1"))],
                        **{"else": [stmt("assign", target="x", value=const("2"))]},
                    )
                ],
                params=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                ],
                locals_=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                    {"name": "x", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_while() -> dict[str, Any]:
    return module(
        [
            fn(
                "while_stmt",
                [
                    stmt(
                        "while",
                        cond=compare("<", name("i"), name("n")),
                        body=[stmt("assign", target="i", value=binary("+", name("i"), const("1")))],
                    )
                ],
                params=[{"name": "n", "type": typ("int")}],
                locals_=[
                    {"name": "n", "type": typ("int")},
                    {"name": "i", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_return() -> dict[str, Any]:
    return module([fn("return_stmt", [stmt("return", value=const("9"))], return_type=typ("int"))])


def _fixture_expr() -> dict[str, Any]:
    return module(
        [
            fn(
                "expr_stmt",
                [
                    stmt(
                        "expr",
                        expr=expr(
                            "call",
                            typ("none"),
                            callee={"kind": "builtin", "name": "print"},
                            args=[const("1")],
                        ),
                    )
                ],
            )
        ]
    )


def _fixture_pass() -> dict[str, Any]:
    return module([fn("pass_stmt", [stmt("pass")])])


def _fixture_setitem() -> dict[str, Any]:
    return module(
        [
            fn(
                "setitem_stmt",
                [
                    stmt(
                        "setitem",
                        container=expr("name", list_type(), id="xs"),
                        index=const("0"),
                        value=const("1"),
                    )
                ],
                locals_=[{"name": "xs", "type": list_type()}],
            )
        ]
    )


def _fixture_setattr() -> dict[str, Any]:
    return module(
        [
            fn(
                "setattr_stmt",
                [
                    stmt(
                        "setattr",
                        object=expr("name", typ("class", name="Point"), id="p"),
                        name="x",
                        value=const("1"),
                    )
                ],
                locals_=[{"name": "p", "type": typ("class", name="Point")}],
            )
        ],
        classes=[point_class()],
    )


def _fixture_try() -> dict[str, Any]:
    return module(
        [
            fn(
                "try_stmt",
                [
                    stmt(
                        "try",
                        body=[stmt("assign", target="x", value=const("1"))],
                        handlers=[
                            {
                                "exc": ["ValueError"],
                                "body": [stmt("assign", target="x", value=const("2"))],
                            }
                        ],
                    )
                ],
                locals_=[{"name": "x", "type": typ("int")}],
            )
        ]
    )


def _fixture_raise() -> dict[str, Any]:
    return module([fn("raise_stmt", [stmt("raise", exc="ValueError", message=const("bad", "str"))])])


# --- structural cases -------------------------------------------------------


def _fixture_class_layout() -> dict[str, Any]:
    return module(
        [
            fn(
                "use_point",
                [
                    stmt(
                        "assign",
                        target="p",
                        value=expr(
                            "call",
                            typ("class", name="Point"),
                            callee={"kind": "class", "name": "Point"},
                            args=[const("1")],
                        ),
                    )
                ],
                locals_=[{"name": "p", "type": typ("class", name="Point")}],
            )
        ],
        classes=[
            {
                "name": "Point",
                "fields": [{"name": "x", "type": typ("int")}],
                "methods": [
                    {
                        "name": "__init__",
                        "params": [
                            {"name": "self", "type": typ("class", name="Point")},
                            {"name": "x", "type": typ("int")},
                        ],
                        "returnType": typ("none"),
                        "locals": [
                            {"name": "self", "type": typ("class", name="Point")},
                            {"name": "x", "type": typ("int")},
                        ],
                        "body": [
                            stmt(
                                "setattr",
                                object=expr("name", typ("class", name="Point"), id="self"),
                                name="x",
                                value=name("x"),
                            )
                        ],
                    }
                ],
            }
        ],
    )


def _fixture_env_closure() -> dict[str, Any]:
    return module(
        [
            fn(
                "make",
                [
                    stmt(
                        "assign",
                        target="f",
                        value=expr(
                            "closure",
                            func_type(),
                            name="make.counter",
                            env=[{"name": "n", "type": typ("int")}],
                        ),
                    )
                ],
                locals_=[
                    {"name": "n", "type": typ("int")},
                    {"name": "f", "type": func_type()},
                ],
            ),
            fn(
                "make.counter",
                [stmt("return", value=name("n"))],
                locals_=[{"name": "n", "type": typ("int")}],
                return_type=typ("int"),
                env=[{"name": "n", "type": typ("int")}],
            ),
        ]
    )


def _fixture_main_entry() -> dict[str, Any]:
    return module(
        [
            fn(
                "main",
                [
                    stmt(
                        "expr",
                        expr=expr(
                            "call",
                            typ("none"),
                            callee={"kind": "builtin", "name": "print"},
                            args=[const("1")],
                        ),
                    )
                ],
            )
        ]
    )


def _fixture_exception_registration() -> dict[str, Any]:
    return module(
        [
            fn("raises_custom", [stmt("raise", exc="MyError", code=900)]),
        ],
        classes=[
            {
                "name": "MyError",
                "fields": [],
                "methods": [],
                "exception": True,
                "exc_code": 900,
                "exc_parent": 0,
            }
        ],
    )


# --- dispatch-surface gaps ---------------------------------------------------
#
# The node-kind corpus above covers one fixture per IR node kind. The emitter
# dispatches further than the node kind: a constant has a type, a binary has
# an op and a type, a call has a callee kind, a try has a finally, a raise has
# a cause. Each fixture below closes one of those branches, so an emitter
# change in any of them shows up as a reviewed golden diff.


def _fixture_const_types() -> dict[str, Any]:
    """Every constant type the emitter can spell: int, bool, float, str, bytes."""

    return module(
        [
            fn(
                "const_types",
                [
                    stmt("assign", target="i", value=const("7")),
                    stmt("assign", target="b", value=const(True, "bool")),
                    stmt("assign", target="f", value=const("1.5", "float")),
                    stmt("assign", target="s", value=const("hi", "str")),
                    stmt("assign", target="y", value=const("ab", "bytes")),
                ],
                locals_=[
                    {"name": "i", "type": typ("int")},
                    {"name": "b", "type": typ("bool")},
                    {"name": "f", "type": typ("float")},
                    {"name": "s", "type": typ("str")},
                    {"name": "y", "type": typ("bytes")},
                ],
            )
        ]
    )


def _fixture_unary_ops() -> dict[str, Any]:
    """Every unary op: `-` on an int, `not` on a bool, `~` on an int."""

    return module(
        [
            fn(
                "unary_ops",
                [
                    stmt("assign", target="a", value=expr("unary", typ("int"), op="-", operand=const("3"))),
                    stmt("assign", target="b", value=expr("unary", typ("bool"), op="not", operand=const(True, "bool"))),
                    stmt("assign", target="c", value=expr("unary", typ("int"), op="~", operand=const("3"))),
                ],
                locals_=[
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("bool")},
                    {"name": "c", "type": typ("int")},
                ],
            )
        ]
    )


def _fixture_binary_ops() -> dict[str, Any]:
    """Every binary op family: the int helpers, the int bitwise ins, the float
    ops and the string concat."""

    return module(
        [
            fn(
                "binary_ops",
                [
                    stmt("assign", target="a", value=binary("+", const("1"), const("2"))),
                    stmt("assign", target="b", value=binary("-", const("1"), const("2"))),
                    stmt("assign", target="c", value=binary("*", const("1"), const("2"))),
                    stmt("assign", target="d", value=binary("//", const("1"), const("2"))),
                    stmt("assign", target="e", value=binary("%", const("1"), const("2"))),
                    stmt("assign", target="f", value=binary("&", const("1"), const("2"))),
                    stmt("assign", target="g", value=binary("|", const("1"), const("2"))),
                    stmt("assign", target="h", value=binary("^", const("1"), const("2"))),
                    stmt("assign", target="i", value=binary("<<", const("1"), const("2"))),
                    stmt("assign", target="j", value=binary(">>", const("1"), const("2"))),
                    stmt(
                        "assign",
                        target="k",
                        value=binary("+", const("1.5", "float"), const("2.5", "float"), kind="float"),
                    ),
                    stmt(
                        "assign",
                        target="l",
                        value=binary("/", const("1.5", "float"), const("2.5", "float"), kind="float"),
                    ),
                    stmt(
                        "assign",
                        target="m",
                        value=binary("+", const("a", "str"), const("b", "str"), kind="str"),
                    ),
                ],
                locals_=[{"name": name, "type": typ("int")} for name in "abcdefghij"]
                + [
                    {"name": "k", "type": typ("float")},
                    {"name": "l", "type": typ("float")},
                    {"name": "m", "type": typ("str")},
                ],
            )
        ]
    )


def _fixture_compare_ops() -> dict[str, Any]:
    """Every compare op: the int predicates, the float predicates, the string
    predicates and the identity comparison."""

    return module(
        [
            fn(
                "compare_ops",
                [
                    stmt("assign", target="a", value=compare("<", const("1"), const("2"))),
                    stmt("assign", target="b", value=compare("<=", const("1"), const("2"))),
                    stmt("assign", target="c", value=compare(">", const("1"), const("2"))),
                    stmt("assign", target="d", value=compare(">=", const("1"), const("2"))),
                    stmt("assign", target="e", value=compare("==", const("1"), const("2"))),
                    stmt("assign", target="f", value=compare("!=", const("1"), const("2"))),
                    stmt(
                        "assign",
                        target="g",
                        value=compare("<", const("1.5", "float"), const("2.5", "float")),
                    ),
                    stmt("assign", target="h", value=compare("==", const("a", "str"), const("b", "str"))),
                    stmt(
                        "assign",
                        target="i",
                        value=expr(
                            "compare",
                            typ("bool"),
                            op="is",
                            left=expr("name", list_type(), id="xs"),
                            right=expr("name", list_type(), id="xs"),
                        ),
                    ),
                ],
                locals_=[{"name": name, "type": typ("bool")} for name in "abcdefghi"]
                + [{"name": "xs", "type": list_type()}],
            )
        ]
    )


def _fixture_call_callees() -> dict[str, Any]:
    """Every callee kind the emitter dispatches on: builtin, function, module,
    method and class."""

    return module(
        [
            fn(
                "call_callees",
                [
                    stmt(
                        "assign",
                        target="a",
                        value=expr(
                            "call",
                            typ("int"),
                            callee={"kind": "builtin", "name": "len"},
                            args=[expr("name", list_type(), id="xs")],
                        ),
                    ),
                    stmt(
                        "assign",
                        target="b",
                        value=expr(
                            "call",
                            typ("int"),
                            callee={"kind": "function", "name": "helper"},
                            args=[const("1")],
                        ),
                    ),
                    stmt(
                        "assign",
                        target="c",
                        value=expr(
                            "call",
                            typ("int"),
                            callee={"kind": "module", "module": "math", "name": "sqrt"},
                            args=[const("2.0", "float")],
                        ),
                    ),
                    stmt(
                        "assign",
                        target="d",
                        value=expr(
                            "call",
                            typ("str"),
                            callee={"kind": "method", "receiver": "str", "name": "upper"},
                            args=[const("a", "str")],
                        ),
                    ),
                    stmt(
                        "assign",
                        target="e",
                        value=expr(
                            "call",
                            typ("class", name="Point"),
                            callee={"kind": "class", "name": "Point"},
                            args=[const("1")],
                        ),
                    ),
                ],
                locals_=[
                    {"name": "xs", "type": list_type()},
                    {"name": "a", "type": typ("int")},
                    {"name": "b", "type": typ("int")},
                    {"name": "c", "type": typ("int")},
                    {"name": "d", "type": typ("str")},
                    {"name": "e", "type": typ("class", name="Point")},
                ],
            ),
            fn(
                "helper",
                [stmt("return", value=const("0"))],
                params=[{"name": "x", "type": typ("int")}],
                locals_=[{"name": "x", "type": typ("int")}],
                return_type=typ("int"),
            ),
        ],
        classes=[point_class()],
    )


def _fixture_call_value() -> dict[str, Any]:
    """A call through a callable value: the indirect env-first call path."""

    return module(
        [
            fn(
                "call_value",
                [
                    stmt(
                        "assign",
                        target="f",
                        value=expr(
                            "closure",
                            func_type(),
                            name="call_value.inner",
                            env=[{"name": "n", "type": typ("int")}],
                        ),
                    ),
                    stmt(
                        "assign",
                        target="r",
                        value=expr(
                            "call",
                            typ("int"),
                            callee={"kind": "value", "target": expr("name", func_type(), id="f")},
                            args=[],
                        ),
                    ),
                ],
                locals_=[
                    {"name": "n", "type": typ("int")},
                    {"name": "f", "type": func_type()},
                    {"name": "r", "type": typ("int")},
                ],
            ),
            fn(
                "call_value.inner",
                [stmt("return", value=name("n"))],
                locals_=[{"name": "n", "type": typ("int")}],
                return_type=typ("int"),
                env=[{"name": "n", "type": typ("int")}],
            ),
        ]
    )


def _fixture_try_finally() -> dict[str, Any]:
    """A try with a finally body: the outline-and-propagate path."""

    return module(
        [
            fn(
                "try_finally",
                [
                    stmt(
                        "try",
                        body=[stmt("assign", target="x", value=const("1"))],
                        handlers=[{"exc": ["ValueError"], "body": [stmt("assign", target="x", value=const("2"))]}],
                        finalbody=[stmt("assign", target="x", value=const("3"))],
                    )
                ],
                locals_=[{"name": "x", "type": typ("int")}],
            )
        ]
    )


def _fixture_raise_cause() -> dict[str, Any]:
    """A raise with a cause: the exception record is built and linked."""

    return module(
        [
            fn(
                "raise_cause",
                [
                    stmt(
                        "raise",
                        exc="ValueError",
                        message=const("bad", "str"),
                        cause={"kind": "excmake", "exc": "TypeError"},
                    )
                ],
            )
        ]
    )


def _fixture_contains_kinds() -> dict[str, Any]:
    """Every containment haystack the emitter handles: str, list and dict."""

    return module(
        [
            fn(
                "contains_kinds",
                [
                    stmt(
                        "assign",
                        target="a",
                        value=expr(
                            "contains",
                            typ("bool"),
                            hay=const("ab", "str"),
                            needle=const("a", "str"),
                            negated=False,
                        ),
                    ),
                    stmt(
                        "assign",
                        target="b",
                        value=expr(
                            "contains",
                            typ("bool"),
                            hay=expr("name", list_type(), id="xs"),
                            needle=const("1"),
                            negated=False,
                        ),
                    ),
                    stmt(
                        "assign",
                        target="c",
                        value=expr(
                            "contains",
                            typ("bool"),
                            hay=expr("name", typ("dict", key=typ("str"), value=typ("int")), id="d"),
                            needle=const("k", "str"),
                            negated=True,
                        ),
                    ),
                ],
                locals_=[
                    {"name": "xs", "type": list_type()},
                    {"name": "d", "type": typ("dict", key=typ("str"), value=typ("int"))},
                    {"name": "a", "type": typ("bool")},
                    {"name": "b", "type": typ("bool")},
                    {"name": "c", "type": typ("bool")},
                ],
            )
        ]
    )


FIXTURE_BUILDERS: dict[str, Any] = {
    "assign": _fixture_assign,
    "const": _fixture_const,
    "name": _fixture_name,
    "unary": _fixture_unary,
    "binary": _fixture_binary,
    "compare": _fixture_compare,
    "call": _fixture_call,
    "list": _fixture_list,
    "dict": _fixture_dict,
    "subscript": _fixture_subscript,
    "attr": _fixture_attr,
    "cast": _fixture_cast,
    "contains": _fixture_contains,
    "closure": _fixture_closure,
    "if": _fixture_if,
    "while": _fixture_while,
    "return": _fixture_return,
    "expr": _fixture_expr,
    "pass": _fixture_pass,
    "setitem": _fixture_setitem,
    "setattr": _fixture_setattr,
    "try": _fixture_try,
    "raise": _fixture_raise,
    "class_layout": _fixture_class_layout,
    "env_closure": _fixture_env_closure,
    "main_entry": _fixture_main_entry,
    "exception_registration": _fixture_exception_registration,
    "const_types": _fixture_const_types,
    "unary_ops": _fixture_unary_ops,
    "binary_ops": _fixture_binary_ops,
    "compare_ops": _fixture_compare_ops,
    "call_callees": _fixture_call_callees,
    "call_value": _fixture_call_value,
    "try_finally": _fixture_try_finally,
    "raise_cause": _fixture_raise_cause,
    "contains_kinds": _fixture_contains_kinds,
}

# The function the `main` entry calls, per fixture. A fixture without one
# emits the functions only.
CALLS: dict[str, tuple[str, list[Any]]] = {
    "main_entry": ("main", []),
    "exception_registration": ("raises_custom", []),
}

# Which IR node kind each fixture covers. The manifest is the coverage record:
# `tests/test_emitter_goldens.py` fails if a kind in `ir.EXPR_KINDS` or
# `ir.STMT_KINDS` has no fixture.
COVERAGE: dict[str, list[str]] = {
    "assign": ["assign"],
    "const": ["const"],
    "name": ["name"],
    "unary": ["unary"],
    "binary": ["binary"],
    "compare": ["compare"],
    "call": ["call"],
    "list": ["list"],
    "dict": ["dict"],
    "subscript": ["subscript"],
    "attr": ["attr"],
    "cast": ["cast"],
    "contains": ["contains"],
    "closure": ["closure"],
    "if": ["if"],
    "while": ["while"],
    "return": ["return"],
    "expr": ["expr"],
    "pass": ["pass"],
    "setitem": ["setitem"],
    "setattr": ["setattr"],
    "try": ["try"],
    "raise": ["raise"],
    "class_layout": [],
    "env_closure": [],
    "main_entry": [],
    "exception_registration": [],
    "const_types": [],
    "unary_ops": [],
    "binary_ops": [],
    "compare_ops": [],
    "call_callees": [],
    "call_value": [],
    "try_finally": [],
    "raise_cause": [],
    "contains_kinds": [],
}

# Which emitter dispatch branch each fixture covers. The emitter dispatches
# further than the node kind: a constant has a type, a binary has an op and a
# type, a call has a callee kind, a try has a finally, a raise has a cause.
# This map is the emitter coverage metric; `tests/test_emitter_goldens.py`
# fails with the uncovered branches listed if one loses its fixture.
SURFACE: dict[str, list[str]] = {
    "assign": ["assign:int"],
    "const": ["const:int"],
    "const_types": ["const:bool", "const:float", "const:str", "const:bytes"],
    "name": ["name:local"],
    "unary": ["unary:int-neg"],
    "unary_ops": ["unary:not", "unary:int-invert"],
    "binary": ["binary:int-add"],
    "binary_ops": [
        "binary:int-sub",
        "binary:int-mul",
        "binary:int-floordiv",
        "binary:int-mod",
        "binary:int-shl",
        "binary:int-shr",
        "binary:int-and",
        "binary:int-or",
        "binary:int-xor",
        "binary:float-add",
        "binary:float-div",
        "binary:str-add",
    ],
    "compare": ["compare:int"],
    "compare_ops": [
        "compare:int-lte",
        "compare:int-gt",
        "compare:int-gte",
        "compare:int-eq",
        "compare:int-neq",
        "compare:float",
        "compare:str",
        "compare:is",
    ],
    "call": ["call:function"],
    "call_callees": ["call:builtin", "call:module", "call:method", "call:class"],
    "call_value": ["call:value"],
    "list": ["list:int-elem"],
    "dict": ["dict:str-int"],
    "subscript": ["subscript:list"],
    "attr": ["attr:class-field"],
    "cast": ["cast:int-to-float"],
    "contains": ["contains:list"],
    "contains_kinds": ["contains:str", "contains:dict"],
    "closure": ["closure:env-first"],
    "if": ["if:else"],
    "while": ["while"],
    "return": ["return:int"],
    "expr": ["expr:call"],
    "pass": ["pass"],
    "setitem": ["setitem:list"],
    "setattr": ["setattr:class-field"],
    "try": ["try:handlers"],
    "try_finally": ["try:finally"],
    "raise": ["raise:plain"],
    "raise_cause": ["raise:cause"],
    "class_layout": [],
    "env_closure": [],
    "main_entry": [],
    "exception_registration": [],
}

# Every dispatch branch the emitter can take. A branch with no fixture is
# reported by the coverage test.
EXPECTED_SURFACE: set[str] = {
    "assign:int",
    "const:int",
    "const:bool",
    "const:float",
    "const:str",
    "const:bytes",
    "name:local",
    "unary:int-neg",
    "unary:not",
    "unary:int-invert",
    "binary:int-add",
    "binary:int-sub",
    "binary:int-mul",
    "binary:int-floordiv",
    "binary:int-mod",
    "binary:int-shl",
    "binary:int-shr",
    "binary:int-and",
    "binary:int-or",
    "binary:int-xor",
    "binary:float-add",
    "binary:float-div",
    "binary:str-add",
    "compare:int",
    "compare:int-lte",
    "compare:int-gt",
    "compare:int-gte",
    "compare:int-eq",
    "compare:int-neq",
    "compare:float",
    "compare:str",
    "compare:is",
    "call:function",
    "call:builtin",
    "call:module",
    "call:method",
    "call:class",
    "call:value",
    "list:int-elem",
    "dict:str-int",
    "subscript:list",
    "attr:class-field",
    "cast:int-to-float",
    "contains:list",
    "contains:str",
    "contains:dict",
    "closure:env-first",
    "if:else",
    "while",
    "return:int",
    "expr:call",
    "pass",
    "setitem:list",
    "setattr:class-field",
    "try:handlers",
    "try:finally",
    "raise:plain",
    "raise:cause",
}

# Structural cases that are not IR node kinds but must be covered all the same.
STRUCTURAL: dict[str, str] = {
    "class_layout": "class type layout, constructor and method emission",
    "env_closure": "env-first function with a captured cell",
    "main_entry": "the main entry function and its caller",
    "exception_registration": "user exception class registration preamble",
    "const_types": "every constant type spelling",
    "unary_ops": "every unary op",
    "binary_ops": "every binary op family",
    "compare_ops": "every compare op family",
    "call_callees": "every call callee kind",
    "call_value": "indirect call through a callable value",
    "try_finally": "try with a finally body",
    "raise_cause": "raise with a cause chain",
    "contains_kinds": "every containment haystack kind",
}


def main(fixtures_dir: Path | None = None, manifest_path: Path | None = None) -> int:
    """Regenerate the corpus. A scratch target keeps the committed files intact,
    which is how the determinism test runs the builder without touching goldens."""

    stamp = cpython_version()
    fixtures = fixtures_dir if fixtures_dir is not None else FIXTURES
    manifest_file = manifest_path if manifest_path is not None else MANIFEST
    fixtures.mkdir(parents=True, exist_ok=True)
    for name, builder in FIXTURE_BUILDERS.items():
        ir = builder()
        ir["cpython"] = stamp
        call, args = CALLS.get(name, (None, []))
        text = emit_ir_text(ir, call=call, args=args)
        # The stamp is incidental (.agents/docs/spec/ir-canonicity.md): it never reaches the
        # emitted text, so the fixture keeps the stamp of the interpreter that
        # wrote it and the test re-stamps before emitting.
        (fixtures / f"{name}.ir.json").write_text(_dump(ir), encoding="utf-8")
        (fixtures / f"{name}.ll").write_text(text, encoding="utf-8")

    manifest = {
        "backend": "llvm",
        "fixtures": [
            {
                "name": name,
                "ir": f"llvm/{name}.ir.json",
                "ll": f"llvm/{name}.ll",
                "covers": COVERAGE[name],
                "surface": SURFACE[name],
                "structural": STRUCTURAL.get(name),
                "call": CALLS.get(name, (None, []))[0],
            }
            for name in FIXTURE_BUILDERS
        ],
    }
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(_dump(manifest), encoding="utf-8")
    print(f"wrote {len(FIXTURE_BUILDERS)} fixtures to {fixtures}")
    return 0


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
