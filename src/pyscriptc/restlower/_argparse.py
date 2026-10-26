"""argparse lowering: the script subset of the command-line parser.

The subset covers the shape a script uses: one `ArgumentParser`, a few
`add_argument` calls, and `parse_args`. The parser keeps its options in a
list of encoded spec strings; `parse_args` walks the process argv and fills
a `Namespace` whose fields are declared in registration order.

The spec string is `dest|action|const|default|flags|required`. `dest` is the
attribute the namespace carries, `action` is `store`, `store_true`,
`store_false` or `store_const`, `const` is the value a `store_const`
action sets, `default` is the value the option keeps when it does not
appear, `flags` is the comma-separated list of option spellings (empty
for positional arguments), and `required` is "1" or "0".

The keyword set is the closed one scripts use: `dest`, `action`, `const`,
`default`, `type`, `required` and `help`. `type` accepts `int`, `float` and `str`.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.ir import types_equal
from pyscriptc.lower._core import _assign, _attr, _name
from pyscriptc.reflection import kwargs_call_note

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


def _bool_const(node: ast.AST) -> str | None:
    """The text of a bool constant ('1' or '0'), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return "1" if node.value else "0"
    return None


def _folded_constant(lo: Any, node: ast.expr) -> ast.expr:
    """A module constant name read as the literal it folds to, else the node itself."""
    if not isinstance(node, ast.Name):
        return node
    found = lo.constants.get(node.id)
    if found is None or found.get("kind") != "const":
        return node
    kind = found["type"]["kind"]
    value = found["value"]
    if kind == "str" and isinstance(value, str):
        return ast.Constant(value=value)
    if kind == "bool":
        return ast.Constant(value=value in {True, "1", "True", "true"})
    if kind == "int":
        return ast.Constant(value=int(value))
    if kind == "float":
        return ast.Constant(value=float(value))
    return node


def _is_path_class(lo: Any, node: ast.expr) -> bool:
    """`Path` or `pathlib.Path` as an argparse `type=`."""
    if isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        return binding is not None and binding.module == "pathlib" and binding.attr == "Path"
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        return resolved is not None and resolved[1] == "pathlib" and resolved[2] == "Path"
    return False


def path_attribute(
    lo: Any, value: dict[str, Any], attr: str, read: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """A `type=Path` namespace field is stored as str; reading it builds the Path."""
    state = getattr(lo, "argparse_state", None)
    if state is None or attr not in state.path_dests or value["type"].get("name") != "Namespace":
        return read
    return lo._abi_call("pathlib", "Path", [read], {"kind": "class", "name": "Path"}, "ps_path_new", span)


def peek_namespace_fields(lo: Any, body: list[ast.stmt]) -> list[dict[str, Any]] | None:
    """The Namespace fields the `add_argument` calls in `body` register, or None.

    The call-site pin pass runs before any body lowers, so an argument that
    is a namespace attribute (`batch_rename(args.work_dir, ...)`) needs the
    field types before the real `add_argument` lowering registers them. This
    reads the same closed keyword set the real handler accepts, purely from
    the AST: no lowering runs, no state mutates, and anything the real
    handler would fence (a non-constant name, an unknown keyword) returns
    None, so the pin refuses rather than guessing. `nargs="*"` positional
    lists are the one shape the runtime stores as a list; the pin reads the
    element type for them, exactly like the runtime fills it.
    """

    fields: list[dict[str, Any]] = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument" or not node.args:
            continue
        first = _text(node.args[0])
        if first is None:
            return None
        flags: list[str] = []
        is_positional = not first.startswith("-")
        if is_positional:
            if len(node.args) != 1 or not first.isidentifier():
                return None
            dest: str | None = first
        else:
            dest = None
            for arg_node in node.args:
                text = _text(arg_node)
                if text is None or not text.startswith("-"):
                    return None
                flags.append(text)
        type_name = "str"
        action = "store"
        for keyword in node.keywords:
            name = keyword.arg
            if name == "dest" and not is_positional:
                text = _text(keyword.value)
                if text is None or not text.isidentifier():
                    return None
                dest = text
            elif name == "action" and not is_positional:
                text = _text(keyword.value)
                if text is None or text not in _ACTIONS:
                    return None
                action = text
            elif name == "type":
                if _is_path_class(lo, keyword.value):
                    type_name = "Path"
                elif isinstance(keyword.value, ast.Name) and keyword.value.id in {"str", "int", "float"}:
                    type_name = keyword.value.id
                else:
                    return None
            elif name == "default":
                if not isinstance(keyword.value, ast.Constant):
                    return None
            elif name in {"const", "required", "help"}:
                pass
            else:
                return None
        if dest is None:
            dest = _dest_from_flags(flags)
        if dest is None:
            return None
        if action in {"store_true", "store_false"}:
            field_type: dict[str, Any] = {"kind": "bool"}
        elif type_name == "int":
            field_type = {"kind": "int"}
        elif type_name == "float":
            field_type = {"kind": "float"}
        elif type_name == "Path":
            field_type = {"kind": "class", "name": "Path"}
        else:
            field_type = {"kind": "str"}
        fields.append({"name": dest, "type": field_type})
    return fields or None


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
        # Dests declared type=Path: stored as str, read back as a Path.
        self.path_dests: set[str] = set()
        self.registered_args: list[dict[str, Any]] = []
        self.parser_name: str | None = None
        self.prog: str = ""
        self.description: str = ""

    def add(self, dest: str, kind: int, field_type: dict[str, Any], arg_meta: dict[str, Any]) -> None:
        self.dests[dest] = len(self.fields)
        self.fields.append({"name": dest, "type": field_type})
        self.kinds.append(kind)
        self.registered_args.append(arg_meta)

    def dest_index(self, dest: str) -> int | None:
        return self.dests.get(dest)


def _state(lo: Any) -> ArgparseState:
    state = getattr(lo, "argparse_state", None)
    if state is None:
        state = ArgparseState()
        lo.argparse_state = state
    return state


def _parser_key(lo: Any, node: ast.Call) -> str:
    """The parser a registration belongs to, for the help probe (issue #710).

    The receiver of `parser.add_argument(...)` names the parser: a bare
    name qualified by the enclosing function is its identity (two
    functions may each build their own `parser`), and an anonymous
    receiver (a call result) gets a key of its own, so its registrations
    never merge with another's.
    """
    value = node.func.value
    function = getattr(lo, "function", None)
    scope = f"{function}." if function else ""
    if isinstance(value, ast.Name):
        return f"{scope}{value.id}"
    return f"<anonymous {id(node)}>"


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
    prog = ""
    description = ""
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if keyword.arg not in {"prog", "description"}:
            lo.fail(node, "PS2003", f"argparse.ArgumentParser keyword {keyword.arg} is not lowered")
        if keyword.arg == "prog":
            text = _text(keyword.value)
            if text is None:
                lo.fail(node, "PS2003", "argparse.ArgumentParser prog is a str constant")
            prog = text
        elif keyword.arg == "description":
            text = _text(keyword.value)
            if text is None:
                lo.fail(node, "PS2003", "argparse.ArgumentParser description is a str constant")
            description = text
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
    state.description = description
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
    """`parser.add_argument(...)`: register one argument or option.

    Positional arguments take a name without a leading dash. Options start
    with a dash. The closed keyword set covers type, default, action,
    required, dest and help.
    """
    if receiver["type"].get("name") != "ArgParser":
        lo.fail(node, "PS2001", "add_argument needs an argparse parser")
    if any(keyword.arg is None for keyword in node.keywords):
        lo.fail(node, "PS2002", kwargs_call_note())
    if not node.args:
        lo.fail(node, "PS2003", "argparse.add_argument needs an argument or option name")

    first_arg = _text(node.args[0])
    if first_arg is None:
        lo.fail(node, "PS2003", "argparse argument names are str constants")

    flags: list[str] = []
    if not first_arg.startswith("-"):
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "argparse positional argument takes exactly one name")
        if not first_arg.isidentifier():
            lo.fail(node, "PS2003", "argparse positional argument name is an identifier")
        is_positional = True
        dest: str | None = first_arg
    else:
        is_positional = False
        dest = None
        for arg_node in node.args:
            text = _text(arg_node)
            if text is None:
                lo.fail(node, "PS2003", "argparse option names are str constants")
            if not text.startswith("-"):
                lo.fail(node, "PS2003", "argparse option names start with a dash")
            flags.append(text)

    action = "store"
    const = ""
    default = ""
    raw_default: Any = None
    type_name = "str"
    required = is_positional
    help_text: str | None = None

    for keyword in node.keywords:
        name = keyword.arg
        if name == "dest":
            if is_positional:
                lo.fail(node, "PS2003", "argparse positional argument does not take dest")
            text = _text(keyword.value)
            if text is None or not text.isidentifier():
                lo.fail(node, "PS2003", "argparse dest is an identifier")
            dest = text
        elif name == "action":
            if is_positional:
                lo.fail(node, "PS2003", "argparse positional argument does not take action")
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
            value_node = _folded_constant(lo, keyword.value)
            text = _text(value_node)
            number = _number(value_node)
            bconst = _bool_const(value_node)
            if text is None and number is None and bconst is None:
                lo.fail(node, "PS2003", "argparse default is a str, int, float or bool constant")
            default = text if text is not None else (number if number is not None else (bconst or ""))
            raw_default = value_node.value if isinstance(value_node, ast.Constant) else default
        elif name == "type":
            if _is_path_class(lo, keyword.value):
                type_name = "Path"
            elif isinstance(keyword.value, ast.Name) and keyword.value.id in {"str", "int", "float"}:
                type_name = keyword.value.id
            else:
                text = _text(keyword.value)
                if text is None or text not in {"str", "int", "float"}:
                    lo.fail(node, "PS2003", "argparse type is str, int or float")
                type_name = text
        elif name == "required":
            if is_positional:
                lo.fail(node, "PS2003", "argparse positional argument does not take required")
            if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, bool):
                lo.fail(node, "PS2003", "argparse required is a bool constant")
            required = keyword.value.value
        elif name == "help":
            text = _text(keyword.value)
            if text is None:
                lo.fail(node, "PS2003", "argparse help is a str constant")
            help_text = text
        else:
            lo.fail(node, "PS2003", f"argparse.add_argument keyword {name} is not lowered")

    if dest is None:
        dest = _dest_from_flags(flags)
    if dest is None:
        lo.fail(node, "PS2003", "argparse.add_argument needs a dest")
    if type_name == "Path" and action != "store":
        lo.fail(node, "PS2003", "argparse type=Path takes the store action")
    if type_name == "Path" and not required and not default:
        # An absent option is None in CPython, and the namespace has no None slot.
        lo.fail(node, "PS2003", "argparse type=Path needs required=True or a str default")

    if action in {"store_true", "store_false"}:
        kind = _KIND_BOOL
        field_type = _BOOL
        if default == "":
            default = "0" if action == "store_true" else "1"
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
    req_str = "1" if required else "0"
    spec = "|".join([dest, action, const, default, ",".join(flags), req_str])
    arg_meta = {
        "dest": dest,
        "flags": flags,
        "is_positional": is_positional,
        "action": action,
        "const": const,
        "raw_default": raw_default,
        "type_name": type_name,
        "required": required,
        "help": help_text,
        # The parser the registration belongs to (issue #710): the help
        # probe replays each parser's options on a parser of its own, so
        # two parsers reusing a flag never collide. The receiver's source
        # name qualified by the enclosing function is the parser's
        # identity; an anonymous receiver (a call result) registers under
        # a name of its own.
        "parser": _parser_key(lo, node),
    }
    state.add(dest, kind, field_type, arg_meta)
    if type_name == "Path":
        state.path_dests.add(dest)
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
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "parse_args expects 0 or 1 argument(s)")
    given: dict[str, Any] | None = None
    if node.args and not (isinstance(node.args[0], ast.Constant) and node.args[0].value is None):
        given = lo._expr(node.args[0])
        want = {"kind": "list", "elem": _STR}
        if not types_equal(given["type"], want) and not (
            given["type"].get("optional") and types_equal(given["type"]["elem"], want)
        ):
            lo.fail(node, "PS2003", "parse_args takes a list of str or None")
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

    import argparse as _c_argparse

    # The replay runs on a fresh parser per source parser (issue #710): the
    # registration list holds every option the module's parsers declare,
    # and replaying them merged onto one parser crashed the compile with
    # `ArgumentError: conflicting option string` the moment two parsers
    # reused a flag. Grouping the replay by the receiver name keeps each
    # parser's options on a parser of its own. A duplicate inside one
    # parser stays a fence: a real `tool.py` run errors the same way.

    prog_sentinel = "\x1fPROG\x1f"

    def _replay(args: list[dict[str, Any]]) -> str:
        hp = _c_argparse.ArgumentParser(
            prog=state.prog or prog_sentinel,
            description=state.description or None,
        )
        for arg in args:
            kwargs: dict[str, Any] = {}
            if arg["help"] is not None:
                kwargs["help"] = arg["help"]
            if arg["is_positional"]:
                if arg["type_name"] == "int":
                    kwargs["type"] = int
                elif arg["type_name"] == "float":
                    kwargs["type"] = float
                elif arg["type_name"] == "str":
                    kwargs["type"] = str
                if arg["raw_default"] is not None:
                    kwargs["default"] = arg["raw_default"]
                hp.add_argument(arg["dest"], **kwargs)
            else:
                if arg["action"] in {"store_true", "store_false"}:
                    kwargs["action"] = arg["action"]
                elif arg["action"] == "store_const":
                    kwargs["action"] = "store_const"
                    kwargs["const"] = arg["const"]
                else:
                    if arg["type_name"] == "int":
                        kwargs["type"] = int
                    elif arg["type_name"] == "float":
                        kwargs["type"] = float
                    elif arg["type_name"] == "str":
                        kwargs["type"] = str
                if arg["dest"] != _dest_from_flags(arg["flags"]):
                    kwargs["dest"] = arg["dest"]
                if arg["required"]:
                    kwargs["required"] = True
                if arg["raw_default"] is not None:
                    kwargs["default"] = arg["raw_default"]
                hp.add_argument(*arg["flags"], **kwargs)
        return hp.format_help()

    try:
        by_parser: dict[str, list[dict[str, Any]]] = {}
        for arg in state.registered_args:
            by_parser.setdefault(arg["parser"], []).append(arg)
        help_text = "\n".join(_replay(args) for args in by_parser.values())
    except _c_argparse.ArgumentError:
        lo.fail(node, "PS2003", "argparse re-registers a conflicting option string")
    help_expr = _const_str(help_text, span)
    help_expr = _const_str(help_text, span)

    namespace = {"kind": "class", "name": "Namespace"}

    def parse(args: dict[str, Any] | None) -> dict[str, Any]:
        if args is None:
            return lo._abi_call(
                "argparse", "parse_args", [receiver, kinds_expr, help_expr], namespace, "ps_argparse_parse", span
            )
        return lo._abi_call(
            "argparse", "parse_args", [receiver, kinds_expr, help_expr, args], namespace, "ps_argparse_parse_list", span
        )

    if given is None or not given["type"].get("optional"):
        return parse(given)
    # parse_args(argv) with argv a `list[str] | None`: None reads the process arguments.
    if given["kind"] != "name":
        holder = lo._temp("argv")
        lo.locals[holder] = given["type"]
        lo.prelude.append(_assign(holder, given, span))
        given = _name(holder, given["type"], span)
    result = lo._temp("ns")
    lo.locals[result] = namespace
    present = parse(_attr(given, "_val", given["type"]["elem"], span))
    absent = parse(None)
    lo.prelude.append(
        {
            "kind": "if",
            "cond": _attr(given, "_tag", _BOOL, span),
            "then": [_assign(result, present, span)],
            "else": [_assign(result, absent, span)],
            "span": span,
        }
    )
    return _name(result, namespace, span)
