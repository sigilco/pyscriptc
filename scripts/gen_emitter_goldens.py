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
}

# Structural cases that are not IR node kinds but must be covered all the same.
STRUCTURAL: dict[str, str] = {
    "class_layout": "class type layout, constructor and method emission",
    "env_closure": "env-first function with a captured cell",
    "main_entry": "the main entry function and its caller",
    "exception_registration": "user exception class registration preamble",
}


def main() -> int:
    stamp = cpython_version()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in FIXTURE_BUILDERS.items():
        ir = builder()
        ir["cpython"] = stamp
        call, args = CALLS.get(name, (None, []))
        text = emit_ir_text(ir, call=call, args=args)
        # The stamp is incidental (docs/ir-canonicity.md): it never reaches the
        # emitted text, so the fixture keeps the stamp of the interpreter that
        # wrote it and the test re-stamps before emitting.
        (FIXTURES / f"{name}.ir.json").write_text(_dump(ir), encoding="utf-8")
        (FIXTURES / f"{name}.ll").write_text(text, encoding="utf-8")

    manifest = {
        "backend": "llvm",
        "fixtures": [
            {
                "name": name,
                "ir": f"llvm/{name}.ir.json",
                "ll": f"llvm/{name}.ll",
                "covers": COVERAGE[name],
                "structural": STRUCTURAL.get(name),
                "call": CALLS.get(name, (None, []))[0],
            }
            for name in FIXTURE_BUILDERS
        ],
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(_dump(manifest), encoding="utf-8")
    print(f"wrote {len(FIXTURE_BUILDERS)} fixtures to {FIXTURES}")
    return 0


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
