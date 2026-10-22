"""argparse lowering: the script subset of the command-line parser.

The subset covers the shape a script uses: one `ArgumentParser`, a few
`add_argument` calls, and `parse_args`. The parser keeps its options in a
list of encoded spec strings; `parse_args` walks the process argv and fills
a `Namespace` whose fields are declared in registration order.

The spec string is `dest|action|const|default|flags`. `dest` is the
attribute the namespace carries, `action` is `store`, `store_true`,
`store_false` or `store_const`, `const` is the value a `store_const`
action sets, `default` is the value the option keeps when it does not
appear, and `flags` is the comma-separated list of option spellings.

The keyword set is the closed one scripts use: `dest`, `action`, `const`,
`default`, `type` and `help`. `type` accepts `int`, `float` and `str`;
`help` is accepted and dropped, because the subset does not print help.
"""

from __future__ import annotations

import ast
from typing import Any

_STR = {"kind": "str"}
_INT = {"kind": "int"}
_FLOAT = {"kind": "float"}
_BOOL = {"kind": "bool"}

# The kind codes the runtime reads off the kind table.
_KIND_STR = 0
_KIND_INT = 1
_KIND_BOOL = 2
_KIND_FLOAT = 3

_ACTIONS = {"store", "store_true", "store_false", "store_const"}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": _INT, "span": span}


def _const_str(value: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": _STR, "span": span}


def _text(node: ast.AST) -> str | None:
    """The text of a str constant, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _number(node: ast.AST) -> str | None:
    """The text of an int or float constant, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return repr(node.value) if isinstance(node.value, float) else str(node.value)
    return None


def _dest_from_flags(flags: list[str]) -> str | None:
    """The dest argparse derives from the option spellings.

    The first long option wins; a short-only option uses its single letter.
    A dash inside the name becomes an underscore.
    """
    for flag in flags:
        if flag.startswith("--"):
            name = flag[2:]
            if name:
                return name.replace("-", "_")
    for flag in flags:
        if flag.startswith("-") and len(flag) == 2:
            return flag[1]
    return None


class ArgparseState:
    """The parser state one module keeps while lowering.

    The namespace fields are declared as the arguments are registered, so
    the class layout is known before the emitter runs.
    """

    def __init__(self) -> None:
        self.specs: list[str] = []
        self.kinds: list[int] = []
        self.fields: list[dict[str, Any]] = []
        self.dests: dict[str, int] = {}
        self.parser_name: str | None = None
        self.prog: str = ""

    def add(self, dest: str, kind: int, field_type: dict[str, Any]) -> None:
        self.dests[dest] = len(self.fields)
        self.fields.append({"name": dest, "type": field_type})
        self.kinds.append(kind)

    def dest_index(self, dest: str) -> int | None:
        return self.dests.get(dest)


def _state(lo: Any) -> ArgparseState:
    state = getattr(lo, "argparse_state", None)
    if state is None:
        state = ArgparseState()
        lo.argparse_state = state
    return state


def _install_classes(lo: Any) -> None:
    """Declare the parser and namespace classes once the module imports argparse.

    The import path already declares them through `MODULE_CLASSES`; this
    covers the `from argparse import ...` spelling, where the import binds
    the class name directly.
    """
    info = lo.classes.get("ArgParser")
    if info is None:
        fields = [{"name": "_specs", "type": {"kind": "list", "elem": _STR}}]
        lo.classes["ArgParser"] = {
            "fields": fields,
            "own_fields": list(fields),
            "methods": {},
            "method_kind": {},
            "method_owner": {},
            "synthetic": True,
            "base": None,
        }
        lo.declared.add("ArgParser")
    namespace = lo.classes.get("Namespace")
    if namespace is None:
        lo.classes["Namespace"] = {
            "fields": [],
            "own_fields": [],
            "methods": {},
            "method_kind": {},
            "method_owner": {},
            "synthetic": True,
            "deferred": True,
            "base": None,
        }
        lo.declared.add("Namespace")


def _parser(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`argparse.ArgumentParser(prog=..., description=...)`.

    The prog name is the program name the usage line prints; the default
    is the script name, which the runtime takes from argv.
    """
    lo._reject_keywords(node)
    prog = ""
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", "**kwargs are not lowered")
        if keyword.arg not in {"prog", "description", "epilog", "formatter_class"}:
            lo.fail(node, "PS2003", f"argparse.ArgumentParser keyword {keyword.arg} is not lowered")
        if keyword.arg == "prog":
            text = _text(keyword.value)
            if text is None:
                lo.fail(node, "PS2003", "argparse.ArgumentParser prog is a str constant")
            prog = text
    if node.args:
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "argparse.ArgumentParser expects 0 or 1 argument(s)")
        text = _text(node.args[0])
        if text is None:
            lo.fail(node, "PS2003", "argparse.ArgumentParser prog is a str constant")
        prog = text
    _install_classes(lo)
    state = _state(lo)
    state.prog = prog
    expr = lo._abi_call(
        "argparse",
        "ArgumentParser",
        [],
        {"kind": "class", "name": "ArgParser"},
        "ps_argparse_new",
        span,
    )
    # The parser keeps runtime state, so a module-level binding must not
    # re-emit the constructor on every read.
    expr["identity"] = True
    return expr


def _add_argument(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`parser.add_argument(...)`: register one option.

    The option spellings come first, then the closed keyword set. The
    dest, action and type decide the namespace field this option fills.
    """
    if receiver["type"].get("name") != "ArgParser":
        lo.fail(node, "PS2001", "add_argument needs an argparse parser")
    if any(keyword.arg is None for keyword in node.keywords):
        lo.fail(node, "PS2002", "**kwargs are not lowered")
    flags: list[str] = []
    for arg_node in node.args:
        text = _text(arg_node)
        if text is None:
            lo.fail(node, "PS2003", "argparse option names are str constants")
        if not text.startswith("-"):
            lo.fail(node, "PS2003", "argparse option names start with a dash")
        flags.append(text)
    if not flags:
        lo.fail(node, "PS2003", "argparse.add_argument needs an option name")

    action = "store"
    const = ""
    default = ""
    type_name = "str"
    dest: str | None = None
    for keyword in node.keywords:
        name = keyword.arg
        if name == "dest":
            text = _text(keyword.value)
            if text is None or not text.isidentifier():
                lo.fail(node, "PS2003", "argparse dest is an identifier")
            dest = text
        elif name == "action":
            text = _text(keyword.value)
            if text is None or text not in _ACTIONS:
                lo.fail(node, "PS2003", "argparse action is store, store_true, store_false or store_const")
            action = text
        elif name == "const":
            text = _text(keyword.value)
            if text is None:
                lo.fail(node, "PS2003", "argparse const is a str constant")
            const = text
        elif name == "default":
            text = _text(keyword.value)
            number = _number(keyword.value)
            if text is None and number is None:
                lo.fail(node, "PS2003", "argparse default is a str, int or float constant")
            default = text if text is not None else (number or "")
        elif name == "type":
            if isinstance(keyword.value, ast.Name) and keyword.value.id in {"str", "int", "float"}:
                type_name = keyword.value.id
                continue
            text = _text(keyword.value)
            if text is None or text not in {"str", "int", "float"}:
                lo.fail(node, "PS2003", "argparse type is str, int or float")
            type_name = text
        elif name == "help":
            if _text(keyword.value) is None:
                lo.fail(node, "PS2003", "argparse help is a str constant")
        else:
            lo.fail(node, "PS2003", f"argparse.add_argument keyword {name} is not lowered")

    if dest is None:
        dest = _dest_from_flags(flags)
    if dest is None:
        lo.fail(node, "PS2003", "argparse.add_argument needs a dest")
    if action == "store_true" or action == "store_false":
        kind = _KIND_BOOL
        field_type = _BOOL
        if default == "":
            default = "0"
    elif type_name == "int":
        kind = _KIND_INT
        field_type = _INT
    elif type_name == "float":
        kind = _KIND_FLOAT
        field_type = _FLOAT
    else:
        kind = _KIND_STR
        field_type = _STR

    state = _state(lo)
    _install_classes(lo)
    spec = "|".join([dest, action, const, default, ",".join(flags)])
    state.add(dest, kind, field_type)
    lo.classes["Namespace"]["fields"] = list(state.fields)
    lo.classes["Namespace"]["own_fields"] = list(state.fields)
    spec_expr = _const_str(spec, span)
    return lo._abi_call(
        "argparse",
        "add_argument",
        [receiver, spec_expr],
        {"kind": "none"},
        "ps_argparse_add",
        span,
    )


def _parse_args(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`parser.parse_args()`: the namespace with one field per option."""
    lo._reject_keywords(node)
    if receiver["type"].get("name") != "ArgParser":
        lo.fail(node, "PS2001", "parse_args needs an argparse parser")
    state = _state(lo)
    _install_classes(lo)
    lo.classes["Namespace"]["fields"] = list(state.fields)
    lo.classes["Namespace"]["own_fields"] = list(state.fields)
    kinds = [_const_int(kind, span) for kind in state.kinds]
    kinds_expr: dict[str, Any] = {
        "kind": "list",
        "elems": kinds,
        "type": {"kind": "list", "elem": _INT},
        "span": span,
    }
    return lo._abi_call(
        "argparse",
        "parse_args",
        [receiver, kinds_expr],
        {"kind": "class", "name": "Namespace"},
        "ps_argparse_parse",
        span,
    )
