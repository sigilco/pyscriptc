"""Compile-time class metadata tables.

The lowered class record already keeps the field table the compiler builds
from the checked annotations (`lower/_classes.py`): every row carries the
field name, the annotation type, the default, and the three flags
`dataclasses` publishes (kw_only, init, repr). A metadata-table call is
therefore an emission over rows the compiler already owns — the
offsetof-style descriptor adapted to the closed-world model, with no new
runtime representation: `dataclasses.fields(T)` becomes a native list of
field records built at the call site from the compile-time table, and
CPython sees the same values through `verify`.

Every function here lowers one stdlib surface name over a lowered class or
an instance of one. A class the subset does not lower (a stdlib class, an
island handle, a synthetic tuple) keeps its named fence, so the caller
never gets a guessed answer. The typing rule the tables publish is the one
the runtime enforces: a `dict` result (`asdict`, `vars`) carries one value
type, so a class whose fields do not share one declared type keeps the
same named fence a heterogeneous dict literal gets — the compiler never
guesses.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower._core import _name

# The synthetic record class `dataclasses.fields` returns, one instance per
# field row. The attribute names are CPython's `Field` names, so the
# checker accepts the reads. Two of the values are spelled differently, and
# the divergence entry says so:
#
# - `type` holds the annotation as text. The compiler has no type objects
#   in the binary, so the annotation is the only form the table can carry;
#   CPython's stub allows `Type[_T] | str | Any`, and the text form is
#   the `str` arm.
# - `default` holds the folded default as text, and the empty string means
#   "no default" (CPython uses the `MISSING` sentinel, which is a runtime
#   object the subset does not have). The value's own type varies per
#   field, so one record type cannot carry it.
_FIELD_RECORD_FIELDS: list[dict[str, Any]] = [
    {"name": "name", "type": {"kind": "str"}},
    {"name": "type", "type": {"kind": "str"}},
    {"name": "default", "type": {"kind": "str"}},
    {"name": "kw_only", "type": {"kind": "bool"}},
    {"name": "init", "type": {"kind": "bool"}},
    {"name": "repr", "type": {"kind": "bool"}},
]


def register_field_record(lo: Any, node: ast.AST) -> str:
    """Register the `dataclasses_field` synthetic record, return its name."""

    name = "dataclasses_field"
    existing = lo.classes.get(name)
    if existing is None:
        if name in lo.declared:
            lo.fail(node, "PS2002", f"{name} clashes with a declared name")
        lo.classes[name] = {
            "fields": [dict(field) for field in _FIELD_RECORD_FIELDS],
            "own_fields": [dict(field) for field in _FIELD_RECORD_FIELDS],
            "methods": {},
            "method_kind": {},
            "method_owner": {},
            "synthetic": True,
            "record": True,
        }
        lo.declared.add(name)
    elif not existing.get("synthetic") or existing.get("fields") != _FIELD_RECORD_FIELDS:
        lo.fail(node, "PS2002", f"{name} clashes with a declared class")
    return name


def _field_text(lo: Any, typ: dict[str, Any]) -> str:
    """The annotation as the text the compiler holds it as.

    `type_key` spells a type the way the annotation read: `int`, `str`,
    `list[int]`, `dict[str, int]`, the class name. That is the string form
    CPython's `Field.type` takes for a forward reference, and it is the
    only form the table can carry: there is no type object in the binary.
    """

    return type_key(typ)


def dataclass_fields(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
) -> dict[str, Any]:
    """`dataclasses.fields(x)` over a lowered dataclass, class or instance.

    CPython takes the class or an instance of one; both forms read the same
    compile-time table, so both lower identically. The result is a list of
    the synthetic `dataclasses_field` records, one per declared field row.
    """

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "fields expects 1 argument(s)")
    arg_node = node.args[0]
    class_name = _dataclass_name(lo, arg_node)
    info = lo.classes.get(class_name)
    if info is None or info.get("synthetic") or info.get("enum"):
        lo.fail(node, "PS2002", f"fields takes a lowered dataclass, not {class_name or 'that class'}")
    record = register_field_record(lo, node)
    elem_t = {"kind": "class", "name": record}
    typ = {"kind": "list", "elem": elem_t}
    if expect is not None and expect["kind"] == "list":
        lo._expect(node, typ, expect, "fields")
    elems = [_field_row(lo, field, elem_t, span) for field in info["fields"]]
    return {"kind": "list", "elems": elems, "type": typ, "span": span}


def _field_row(lo: Any, field: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """One field record instance, every attribute a compile-time constant."""

    temp = lo._temp("fld")
    lo.locals[temp] = typ
    made = {"kind": "call", "callee": {"kind": "class", "name": typ["name"]}, "args": [], "type": typ, "span": span}
    stmts = [{"kind": "assign", "target": temp, "value": made, "span": span}]
    has_default = "default" in field
    values: list[tuple[str, dict[str, Any]]] = [
        ("name", {"kind": "const", "value": field["name"], "type": {"kind": "str"}, "span": span}),
        ("type", {"kind": "const", "value": _field_text(lo, field["type"]), "type": {"kind": "str"}, "span": span}),
        (
            "default",
            {
                "kind": "const",
                "value": _default_text(field) if has_default else "",
                "type": {"kind": "str"},
                "span": span,
            },
        ),
        ("kw_only", {"kind": "const", "value": bool(field.get("kw_only")), "type": {"kind": "bool"}, "span": span}),
        ("init", {"kind": "const", "value": bool(field.get("init", True)), "type": {"kind": "bool"}, "span": span}),
        ("repr", {"kind": "const", "value": bool(field.get("repr", True)), "type": {"kind": "bool"}, "span": span}),
    ]
    for attr_name, value in values:
        stmts.append({"kind": "setattr", "object": _name(temp, typ, span), "name": attr_name, "value": value, "span": span})
    lo.prelude.extend(stmts)
    return _name(temp, typ, span)


def _default_text(field: dict[str, Any]) -> str:
    """A folded default as its literal text, the same text `verify` diffs."""

    value = field["default"]["value"]
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _dataclass_name(lo: Any, node: ast.expr) -> str | None:
    """The lowered class an argument names, class or instance, or None.

    `fields(Point)` reads the class name; `fields(p)` reads the instance's
    declared type. Either is the same table.
    """

    if isinstance(node, ast.Name):
        if node.id in lo.classes and not lo.classes[node.id].get("synthetic"):
            return node.id
    typ = lo.locals.get(node.id) if isinstance(node, ast.Name) else None
    if typ is not None and typ["kind"] == "class":
        info = lo.classes.get(typ["name"])
        if info is not None and not info.get("synthetic") and not info.get("enum"):
            return typ["name"]
    return None


def dataclass_as_dict(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
) -> dict[str, Any]:
    """`dataclasses.asdict(o)` over a lowered dataclass instance.

    The dict result carries one value type, like every dict in the subset.
    A class whose fields do not share one declared type keeps the same
    named fence a heterogeneous dict literal gets; the nested-dataclass
    recursion of CPython's `asdict` is a later step (deep copies of nested
    records), this table walk is the flat shape the metadata buys.
    """

    receiver = _instance_arg(lo, node, span, "asdict")
    info = lo.classes[receiver["type"]["name"]]
    val_t = _shared_field_type(lo, node, info, "asdict")
    typ = {"kind": "dict", "key": {"kind": "str"}, "value": val_t}
    if expect is not None and expect["kind"] == "dict":
        lo._expect(node, typ, expect, "asdict")
    pairs = [
        {
            "key": {"kind": "const", "value": field["name"], "type": {"kind": "str"}, "span": span},
            "value": {"kind": "attr", "value": receiver, "name": field["name"], "type": field["type"], "span": span},
        }
        for field in info["fields"]
    ]
    return {"kind": "dict", "pairs": pairs, "type": typ, "span": span}


def dataclass_as_tuple(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
) -> dict[str, Any]:
    """`dataclasses.astuple(o)`: the field values as a fixed-arity tuple."""

    receiver = _instance_arg(lo, node, span, "astuple")
    info = lo.classes[receiver["type"]["name"]]
    elems = [
        {"kind": "attr", "value": receiver, "name": field["name"], "type": field["type"], "span": span}
        for field in info["fields"]
    ]
    typ = lo._tuple_type([field["type"] for field in info["fields"]], node)
    if expect is not None and expect["kind"] == "class":
        lo._expect(node, typ, expect, "astuple")
    return lo._materialize_tuple(elems, span, node)


def dataclass_replace(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
) -> dict[str, Any]:
    """`dataclasses.replace(o, **changes)` over a lowered dataclass instance.

    The changes are keyword arguments matched against the field rows at
    compile time, then the constructor call reuses the synthesized init:
    `replace` is `Point(o.x, changed_y)` spelled over the table, so the
    call-site checks (arity, keyword names, types) are the constructor's
    own, and a wrong field name fences the way a wrong constructor keyword
    does.
    """

    receiver = _instance_arg(lo, node, span, "replace", allow_keywords=True)
    class_name = receiver["type"]["name"]
    info = lo.classes[class_name]
    init = info["methods"].get("__init__")
    if init is None:
        lo.fail(node, "PS2002", f"replace needs a lowered __init__ on {class_name}")
    params = [param for param in init[0] if param["name"] != "self"]
    row = {field["name"]: field for field in info["fields"]}
    if any(kw.arg is None for kw in node.keywords):
        lo.fail(node, "PS2002", "replace takes no keyword unpacking")
    changed = {kw.arg for kw in node.keywords}
    unknown = sorted(changed - set(row))
    if unknown:
        lo.fail(node, "PS2003", f"replace got an unknown field name {unknown[0]}")
    args: list[dict[str, Any]] = []
    for param in params:
        field = row[param["name"]]
        override = next((kw for kw in node.keywords if kw.arg == param["name"]), None)
        if override is not None:
            value = lo._expr(override.value, param["type"])
            lo._expect(node, value["type"], param["type"], f"replace {param['name']}")
            args.append(value)
        elif not field.get("init", True):
            continue
        elif "default" in field:
            # The declared default rides in the param row the call sites
            # use, so the constructor call keeps its own arity. An
            # optional field's default is boxed the way a call site boxes
            # it, so the constructor sees the same shape.
            default = field["default"]
            if field.get("optdefault"):
                default = lo._opt_box(
                    None if default["type"]["kind"] == "none" else default, param["type"], span
                )
            args.append(default)
        else:
            args.append({"kind": "attr", "value": receiver, "name": param["name"], "type": param["type"], "span": span})
    made = {"kind": "call", "callee": {"kind": "class", "name": class_name}, "args": args, "type": receiver["type"], "span": span}
    temp = lo._temp("repl")
    lo.locals[temp] = receiver["type"]
    lo.prelude.append({"kind": "assign", "target": temp, "value": made, "span": span})
    return _name(temp, receiver["type"], span)


def class_vars(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None
) -> dict[str, Any]:
    """`vars(o)`: the read-only field table of a lowered class instance.

    The same shape `asdict` returns — `dict[str, T]` over one shared field
    type — with the divergence noted: writes into the result are not
    writes into the instance, and the divergence entry says so.
    """

    receiver = _instance_arg(lo, node, span, "vars")
    info = lo.classes[receiver["type"]["name"]]
    val_t = _shared_field_type(lo, node, info, "vars")
    typ = {"kind": "dict", "key": {"kind": "str"}, "value": val_t}
    if expect is not None and expect["kind"] == "dict":
        lo._expect(node, typ, expect, "vars")
    pairs = [
        {
            "key": {"kind": "const", "value": field["name"], "type": {"kind": "str"}, "span": span},
            "value": {"kind": "attr", "value": receiver, "name": field["name"], "type": field["type"], "span": span},
        }
        for field in info["fields"]
    ]
    return {"kind": "dict", "pairs": pairs, "type": typ, "span": span}


def vars_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`vars(o)` over an instance of a lowered class.

    CPython's `vars()` with no argument returns the caller's globals, which
    the closed-world model has no shape for; that form keeps its fence. The
    one-argument form reads the compile-time field table.
    """

    if not node.args:
        lo.fail(node, "PS2002", "vars() with no argument reads the module globals, which are not in the subset")
    return class_vars(lo, node, span, None)


def enum_class(lo: Any, node: ast.expr) -> str | None:
    """The lowered enum class an expression names, or None.

    An enum class is not a general runtime value in the subset (there are
    no type objects), but the compile-time member table makes the four
    forms that read it foldable: `E["X"]`, `E(v)`, `E.__members__` and
    `list(E)`. This returns the class name for those call sites.
    """

    if not isinstance(node, ast.Name):
        return None
    name = lo.class_aliases.get(node.id, node.id)
    info = lo.classes.get(name)
    if info is None or not info.get("enum"):
        return None
    return name


def enum_lookup(lo: Any, node: ast.Subscript, span: dict[str, Any]) -> dict[str, Any]:
    """`E["X"]`: the value of the member named X, folded at compile time.

    The member table is a compile-time dict, so the lookup is a constant
    fold and a wrong name is a compile-time diagnostic, not a runtime
    `KeyError`. The divergence entry says so.
    """

    name = enum_class(lo, node.value)
    assert name is not None
    info = lo.classes[name]
    index = node.slice
    if not isinstance(index, ast.Constant) or not isinstance(index.value, str):
        lo.fail(node, "PS2002", f"{name}[...] needs a string literal member name")
    member = info["members"].get(index.value)
    if member is None:
        lo.fail(node, "PS2001", f"{index.value} is not a member of {name}")
    return {"kind": "const", "value": str(member), "type": {"kind": "int"}, "span": span}


def enum_value_lookup(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`E(v)`: the member whose value is v, folded at compile time.

    The member table is a compile-time dict, so the lookup is a constant
    fold and a value that names no member is a compile-time diagnostic.
    CPython looks the *value* up here, not the member name (the name
    lookup is `E["X"]`), so only the int form lowers.
    """

    if len(node.args) != 1 or node.keywords:
        lo.fail(node, "PS2003", "an enum lookup expects 1 argument(s)")
    name = enum_class(lo, node.func)
    assert name is not None
    info = lo.classes[name]
    arg = lo._expr(node.args[0])
    if arg["kind"] != "const" or arg["type"]["kind"] != "int":
        lo.fail(node, "PS2002", f"{name}(v) needs a constant integer value")
    wanted = int(str(arg["value"]))
    for member_name, member_value in info["members"].items():
        if member_value == wanted:
            return {"kind": "const", "value": str(member_value), "type": {"kind": "int"}, "span": span}
    lo.fail(node, "PS2001", f"{wanted} is not a value of {name}")


def enum_members(lo: Any, node: ast.Attribute, span: dict[str, Any]) -> dict[str, Any]:
    """`E.__members__`: the member table as a `dict[str, int]`.

    The names map to the member values, the same shape CPython's
    `__members__` has (it maps names to the members, and the members are
    the int values in this subset).
    """

    name = enum_class(lo, node.value)
    assert name is not None
    info = lo.classes[name]
    typ = {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "int"}}
    pairs = [
        {
            "key": {"kind": "const", "value": member, "type": {"kind": "str"}, "span": span},
            "value": {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span},
        }
        for member, value in info["members"].items()
    ]
    return {"kind": "dict", "pairs": pairs, "type": typ, "span": span}


def enum_list(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    """`list(E)`: the member values, in declaration order."""

    if len(node.args) != 1 or node.keywords:
        lo.fail(node, "PS2003", "list expects 1 argument(s)")
    name = enum_class(lo, node.args[0])
    assert name is not None
    info = lo.classes[name]
    typ = {"kind": "list", "elem": {"kind": "int"}}
    if expect is not None and expect["kind"] == "list":
        lo._expect(node, typ, expect, "list")
    elems = [
        {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}
        for value in info["members"].values()
    ]
    return {"kind": "list", "elems": elems, "type": typ, "span": span}


def _instance_arg(
    lo: Any, node: ast.Call, span: dict[str, Any], what: str, allow_keywords: bool = False
) -> dict[str, Any]:
    """Lower the one instance argument and check it names a lowered class."""

    if not allow_keywords:
        lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"{what} expects 1 argument(s)")
    receiver = lo._expr(node.args[0])
    if receiver["type"]["kind"] != "class":
        lo.fail(node, "PS2002", f"{what} takes an instance of a lowered class")
    info = lo.classes.get(receiver["type"]["name"])
    if info is None or info.get("synthetic") or info.get("enum"):
        lo.fail(node, "PS2002", f"{what} takes an instance of a lowered dataclass")
    return receiver


def _shared_field_type(lo: Any, node: ast.AST, info: dict[str, Any], what: str) -> dict[str, Any]:
    """The one declared field type every row shares, or the named fence.

    A `dict[str, T]` result carries one value type in the IR; a class
    whose fields do not share one type keeps the same fence a
    heterogeneous dict literal gets (`PS1003`), never a widened guess.
    """

    fields = info["fields"]
    if not fields:
        lo.fail(node, "PS2002", f"{what} on a class with no fields returns an empty dict: annotate the result")
    shared = fields[0]["type"]
    for field in fields[1:]:
        if not types_equal(field["type"], shared):
            lo.fail(
                node,
                "PS1003",
                f"{what} on {info.get('node', None) and info['node'].name or ''} needs one field type: "
                f"{field['name']} is not the type of {fields[0]['name']}; a dict entry has one value type",
            )
    if shared["kind"] == "bool":
        lo.fail(node, "PS2002", "dict values of bool are not lowered")
    return shared
