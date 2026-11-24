"""argparse lowering: the script subset of the command-line parser.

The subset covers the shape a script uses: one `ArgumentParser`, a few
`add_argument` calls, and `parse_args`. The parser keeps its options in a
list of encoded spec strings; `parse_args` walks the process argv and fills
a `Namespace` whose fields are declared in registration order.

The spec string is `dest|action|const|default|flags|required|slot`. `dest` is the
attribute the namespace carries, `action` is `store`, `store_true`,
`store_false` or `store_const`, `const` is the value a `store_const`
action sets, `default` is the value the option keeps when it does not
appear, `flags` is the comma-separated list of option spellings (empty
for positional arguments), `required` is "1" or "0", and `slot` is the index of
the namespace field the value lands in (several parsers share one namespace).

`add_subparsers(dest=, required=)` and `add_parser(name, help=)` add a second
level: each subcommand parser keeps its own spec rows, and the runtime hands the
rest of argv to the subcommand the first positional token names. One level only.

The keyword set is the closed one scripts use: `dest`, `action`, `const`,
`default`, `type`, `required` and `help`. `type` accepts `int`, `float` and `str`.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.ir import types_equal
from pyscriptc.lower._core import _assign, _attr, _name
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import (
    _class,
    _const_int,
    _const_text,
    _if_stmt,
    _list_lit,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _t_list,
)

# The kind codes the runtime reads off the kind table.
_KIND_STR = 0
_KIND_INT = 1
_KIND_BOOL = 2
_KIND_FLOAT = 3

_ACTIONS = {"store", "store_true", "store_false", "store_const"}


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
    kind = _node_type_kind(found)
    value = found.get("value")
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
    if state is None or attr not in state.path_dests or _node_type(value).get("name") != "Namespace":
        return read
    return lo._abi_call("pathlib", "Path", [read], _class("Path"), "ps_path_new", span)


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

    The walk is breadth first, so the field order is the order of the
    statements of the scanned bodies, which is the order the real lowering
    registers them in: the namespace slot layout must not diverge between
    the two. `add_subparsers(dest=)` registers its dest field in that walk.
    """

    fields: list[dict[str, Any]] = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "add_subparsers":
            # The subcommand name is a str field, registered where the call
            # lowers; the walk visits it in the same program order as the
            # add_argument calls around it, so the slot layout agrees.
            sub_dest: str | None = None
            for keyword in node.keywords:
                if keyword.arg == "dest":
                    sub_dest = _text(keyword.value)
                    if sub_dest is None or not sub_dest.isidentifier():
                        return None
                elif keyword.arg != "required":
                    return None
            if node.args:
                return None
            if sub_dest is not None:
                fields.append({"name": sub_dest, "type": _t_leaf("str")})
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
            field_type: dict[str, Any] = _t_leaf("bool")
        elif type_name == "int":
            field_type = _t_leaf("int")
        elif type_name == "float":
            field_type = _t_leaf("float")
        elif type_name == "Path":
            field_type = _class("Path")
        else:
            field_type = _t_leaf("str")
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


class ArgGroup:
    """The arguments one parser level registers, for the help replay."""

    def __init__(self, name: str, help_text: str | None) -> None:
        self.name = name
        self.help = help_text
        self.args: list[dict[str, Any]] = []


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
        # Group 0 is the top parser; each add_parser call adds one child.
        self.groups: list[ArgGroup] = [ArgGroup("", None)]
        # Which group a call or a binding name stands for, so add_argument
        # reaches the right parser level when the help text is replayed.
        self.group_by_pos: dict[tuple[int, int], int] = {}
        self.group_of_name: dict[str, int] = {}
        self.subs_pos: tuple[int, int] | None = None
        self.sub_dest: str | None = None
        self.sub_required = False

    def add(self, dest: str, field: dict[str, Any], kind: int, arg_meta: dict[str, Any], group: int = 0) -> int:
        slot = len(self.fields)
        self.dests[dest] = slot
        self.fields.append(field)
        self.kinds.append(kind)
        self.registered_args.append(arg_meta)
        self.groups[group].args.append(arg_meta)
        return slot

    def dest_index(self, dest: str) -> int | None:
        return self.dests.get(dest)


def _assign_targets(lo: Any) -> dict[tuple[int, int], str]:
    """The name each `name = call(...)` statement binds, keyed by the call position.

    The lowerer binds a parser in several places (a local, a module slot, an
    annotated name), so the receiver of a later `add_argument` is mapped back
    to its parser level through the name the call was assigned to.
    """
    cached: dict[tuple[int, int], str] | None = getattr(lo, "_argparse_targets", None)
    if cached is not None:
        return cached
    found: dict[tuple[int, int], str] = {}
    try:
        tree = ast.parse(lo.source)
    except SyntaxError:
        tree = ast.Module(body=[], type_ignores=[])
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Call):
            found[(value.lineno, value.col_offset)] = target.id
    lo._argparse_targets = found
    return found


def _bind_group(lo: Any, node: ast.Call, group: int) -> None:
    state = _state(lo)
    key = (node.lineno, node.col_offset)
    state.group_by_pos[key] = group
    target = _assign_targets(lo).get(key)
    if target is not None:
        state.group_of_name[target] = group


def _receiver_group(lo: Any, node: ast.Call) -> int:
    """The parser level `node.func.value` stands for; the top parser when unknown."""
    state = _state(lo)
    base = node.func.value if isinstance(node.func, ast.Attribute) else None
    if isinstance(base, ast.Name):
        return state.group_of_name.get(base.id, 0)
    if isinstance(base, ast.Call):
        return state.group_by_pos.get((base.lineno, base.col_offset), 0)
    return 0


def _probe_type(type_name: str) -> Any:
    """The callable argparse's `type=` takes for a spec's type name."""
    if type_name == "int":
        return int
    if type_name == "float":
        return float
    if type_name == "str":
        return str
    return None


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
        fields = [
            {"name": "_specs", "type": _t_list(_t_leaf("str"))},
            {"name": "_subs", "type": _t_list(_t_leaf("str"))},
        ]
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
    if lo.classes.get("SubParsers") is None:
        sub_fields = [
            {"name": "_parent", "type": _t_list(_t_leaf("str"))},
            {"name": "_dest", "type": _t_leaf("int")},
            {"name": "_required", "type": _t_leaf("int")},
        ]
        lo.classes["SubParsers"] = {
            "fields": sub_fields,
            "own_fields": list(sub_fields),
            "methods": {},
            "method_kind": {},
            "method_owner": {},
            "synthetic": True,
            "base": None,
        }
        lo.declared.add("SubParsers")
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
        _class("ArgParser"),
        "ps_argparse_new",
        span,
    )
    # The parser keeps runtime state, so a module-level binding must not
    # re-emit the constructor on every read.
    expr["identity"] = True
    _bind_group(lo, node, 0)
    return expr


def _add_argument(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`parser.add_argument(...)`: register one argument or option.

    Positional arguments take a name without a leading dash. Options start
    with a dash. The closed keyword set covers type, default, action,
    required, dest and help.
    """
    if _node_type(receiver).get("name") != "ArgParser":
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
        field_type = _t_leaf("bool")
        if default == "":
            default = "0" if action == "store_true" else "1"
    elif type_name == "int":
        kind = _KIND_INT
        field_type = _t_leaf("int")
    elif type_name == "float":
        kind = _KIND_FLOAT
        field_type = _t_leaf("float")
    else:
        kind = _KIND_STR
        field_type = _t_leaf("str")

    state = _state(lo)
    _install_classes(lo)
    req_str = "1" if required else "0"
    slot = len(state.fields)
    spec = "|".join([dest, action, const, default, ",".join(flags), req_str, str(slot)])
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
    }
    state.add(dest, {"name": dest, "type": field_type}, kind, arg_meta, _receiver_group(lo, node))
    if type_name == "Path":
        state.path_dests.add(dest)
    lo.classes["Namespace"]["fields"] = list(state.fields)
    lo.classes["Namespace"]["own_fields"] = list(state.fields)
    spec_expr = _const_text(spec, "str", span)
    return lo._abi_call(
        "argparse",
        "add_argument",
        [receiver, spec_expr],
        _t_leaf("none"),
        "ps_argparse_add",
        span,
    )


def _add_subparsers(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`parser.add_subparsers(dest=, required=)`: the subcommand holder.

    The holder keeps the slot of the namespace field the chosen subcommand
    name lands in (`-1` without a dest) and whether a subcommand is required.
    One call per module: the second level is one level deep, and a second
    holder would need a second dispatch table.
    """
    if _node_type(receiver).get("name") != "ArgParser":
        lo.fail(node, "PS2001", "add_subparsers needs an argparse parser")
    if any(keyword.arg is None for keyword in node.keywords):
        lo.fail(node, "PS2002", kwargs_call_note())
    if node.args:
        lo.fail(node, "PS2003", "argparse.add_subparsers takes keyword arguments only")
    dest: str | None = None
    required = False
    for keyword in node.keywords:
        if keyword.arg == "dest":
            text = _text(keyword.value)
            if text is None or not text.isidentifier():
                lo.fail(node, "PS2003", "argparse.add_subparsers dest is an identifier")
            dest = text
        elif keyword.arg == "required":
            if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, bool):
                lo.fail(node, "PS2003", "argparse.add_subparsers required is a bool constant")
            required = keyword.value.value
        else:
            lo.fail(node, "PS2003", f"argparse.add_subparsers keyword {keyword.arg} is not lowered")
    state = _state(lo)
    _install_classes(lo)
    key = (node.lineno, node.col_offset)
    if state.subs_pos is not None and state.subs_pos != key:
        lo.fail(node, "PS2003", "argparse takes one add_subparsers call per module")
    if _receiver_group(lo, node) != 0:
        lo.fail(node, "PS2003", "argparse nested subparsers are not lowered")
    slot = -1
    if dest is not None:
        known = state.dests.get(dest) if state.subs_pos == key else None
        if known is not None:
            slot = known
        else:
            slot = len(state.fields)
            state.dests[dest] = slot
            state.fields.append({"name": dest, "type": _t_leaf("str")})
            state.kinds.append(_KIND_STR)
            lo.classes["Namespace"]["fields"] = list(state.fields)
            lo.classes["Namespace"]["own_fields"] = list(state.fields)
    state.subs_pos = key
    state.sub_dest = dest
    state.sub_required = required
    expr = lo._abi_call(
        "argparse",
        "add_subparsers",
        [
            receiver,
            _const_int(slot, span),
            _const_int(1 if required else 0, span),
            _const_text(dest or "", "str", span),
        ],
        _class("SubParsers"),
        "ps_argparse_subs",
        span,
    )
    expr["identity"] = True
    return expr


def _add_parser(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`sub.add_parser(name, help=)`: one subcommand parser.

    The returned parser takes `add_argument` and nothing else of the
    ArgumentParser surface that is not already lowered; `help` is the line the
    parent's help lists beside the name.
    """
    if _node_type(receiver).get("name") != "SubParsers":
        lo.fail(node, "PS2001", "add_parser needs the result of add_subparsers")
    if any(keyword.arg is None for keyword in node.keywords):
        lo.fail(node, "PS2002", kwargs_call_note())
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "argparse.add_parser takes exactly one name")
    name = _text(node.args[0])
    if name is None or not name or name.startswith("-") or any(ch.isspace() or ch in "\x1e\x1f" for ch in name):
        lo.fail(node, "PS2003", "argparse.add_parser name is a str constant without dashes or spaces")
    help_text: str | None = None
    for keyword in node.keywords:
        if keyword.arg == "help":
            help_text = _text(keyword.value)
            if help_text is None:
                lo.fail(node, "PS2003", "argparse.add_parser help is a str constant")
        else:
            lo.fail(node, "PS2003", f"argparse.add_parser keyword {keyword.arg} is not lowered")
    state = _state(lo)
    _install_classes(lo)
    key = (node.lineno, node.col_offset)
    group = state.group_by_pos.get(key)
    if group is None:
        if any(existing.name == name for existing in state.groups[1:]):
            lo.fail(node, "PS2003", f"argparse.add_parser name {name} is already registered")
        state.groups.append(ArgGroup(name, help_text))
        group = len(state.groups) - 1
        _bind_group(lo, node, group)
    expr = lo._abi_call(
        "argparse",
        "add_parser",
        [receiver, _const_text(name, "str", span)],
        _class("ArgParser"),
        "ps_argparse_add_parser",
        span,
    )
    expr["identity"] = True
    return expr


def _replay(hp: Any, args: list[dict[str, Any]]) -> None:
    """Register `args` into the CPython parser `hp`, each option string at most once.

    Registration can run more than once for the same call (a call site is
    pinned before it lowers for real), so `args` may list one option several
    times; replaying it verbatim makes argparse raise
    `ArgumentError: conflicting option string` and crash the compile
    (#710). The probe is best-effort metadata: each option string and
    positional dest is replayed at most once, and a residual conflict
    skips the argument instead of failing the build.
    """
    import argparse as _c_argparse

    seen_strings: set[str] = set()
    for arg in args:
        names = arg["flags"] if not arg["is_positional"] else [arg["dest"]]
        if any(name in seen_strings for name in names):
            continue
        kwargs: dict[str, Any] = {}
        if arg["help"] is not None:
            kwargs["help"] = arg["help"]
        if arg["is_positional"]:
            probe_type = _probe_type(arg["type_name"])
            if probe_type is not None:
                kwargs.update({"type": probe_type})
            if arg["raw_default"] is not None:
                kwargs["default"] = arg["raw_default"]
            try:
                hp.add_argument(arg["dest"], **kwargs)
            except _c_argparse.ArgumentError:
                continue
        else:
            if arg["action"] in {"store_true", "store_false"}:
                kwargs["action"] = arg["action"]
            elif arg["action"] == "store_const":
                kwargs["action"] = "store_const"
                kwargs["const"] = arg["const"]
            else:
                probe_type = _probe_type(arg["type_name"])
                if probe_type is not None:
                    kwargs.update({"type": probe_type})
            if arg["dest"] != _dest_from_flags(arg["flags"]):
                kwargs["dest"] = arg["dest"]
            if arg["required"]:
                kwargs["required"] = True
            if arg["raw_default"] is not None:
                kwargs["default"] = arg["raw_default"]
            try:
                hp.add_argument(*arg["flags"], **kwargs)
            except _c_argparse.ArgumentError:
                continue
        seen_strings.update(names)


def _parse_args(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`parser.parse_args()`: the namespace with one field per option."""
    lo._reject_keywords(node)
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "parse_args expects 0 or 1 argument(s)")
    given: dict[str, Any] | None = None
    if node.args and not (isinstance(node.args[0], ast.Constant) and node.args[0].value is None):
        given = lo._expr(node.args[0])
        want = _t_list(_t_leaf("str"))
        given_type = _node_type(given)
        if not types_equal(given_type, want) and not (
            given_type.get("optional") and types_equal(given_type["elem"], want)
        ):
            lo.fail(node, "PS2003", "parse_args takes a list of str or None")
    if _node_type(receiver).get("name") != "ArgParser":
        lo.fail(node, "PS2001", "parse_args needs an argparse parser")
    state = _state(lo)
    _install_classes(lo)
    lo.classes["Namespace"]["fields"] = list(state.fields)
    lo.classes["Namespace"]["own_fields"] = list(state.fields)
    kinds = [_const_int(kind, span) for kind in state.kinds]
    kinds_expr: dict[str, Any] = _list_lit(kinds, _t_leaf("int"), span)

    import argparse as _c_argparse

    prog_sentinel = "\x1fPROG\x1f"
    hp = _c_argparse.ArgumentParser(
        prog=state.prog or prog_sentinel,
        description=state.description or None,
    )
    # The help probe replays each parser level's registered arguments into a
    # fresh CPython parser to synthesize the help text the runtime prints
    # (and the usage line its error paths print).
    _replay(hp, state.groups[0].args)
    # One blob carries every level: `name RS prog RS help` triples joined by
    # RS (0x1e), the top parser first under the empty name. The runtime finds
    # a level by name and prints its help or its usage and error prefix.
    pieces = ["", hp.prog, ""]
    if state.subs_pos is not None:
        sub_kwargs: dict[str, Any] = {}
        if state.sub_dest:
            sub_kwargs["dest"] = state.sub_dest
        if state.sub_required:
            sub_kwargs["required"] = True
        holder = hp.add_subparsers(**sub_kwargs)
        children = []
        for group in state.groups[1:]:
            child_kwargs: dict[str, Any] = {}
            if group.help is not None:
                child_kwargs["help"] = group.help
            child = holder.add_parser(group.name, **child_kwargs)
            _replay(child, group.args)
            children.append((group.name, child))
        # The top help is formatted after the children register, so its
        # subcommand listing is complete.
        for name, child in children:
            pieces += [name, child.prog, child.format_help()]
    pieces[2] = hp.format_help()
    help_expr = _const_text("\x1e".join(pieces), "str", span)

    namespace = _class("Namespace")

    def parse(args: dict[str, Any] | None) -> dict[str, Any]:
        if args is None:
            return lo._abi_call(
                "argparse", "parse_args", [receiver, kinds_expr, help_expr], namespace, "ps_argparse_parse", span
            )
        return lo._abi_call(
            "argparse", "parse_args", [receiver, kinds_expr, help_expr, args], namespace, "ps_argparse_parse_list", span
        )

    if given is None or not _node_type(given).get("optional"):
        return parse(given)
    # parse_args(argv) with argv a `list[str] | None`: None reads the process arguments.
    if given.get("kind") != "name":
        holder = lo._temp("argv")
        given_type = _node_type(given)
        lo.locals[holder] = given_type
        lo.prelude.append(_assign(holder, given, span))
        given = _name(holder, given_type, span)
    result = lo._temp("ns")
    lo.locals[result] = namespace
    present = parse(_attr(given, "_val", _node_type(given)["elem"], span))
    absent = parse(None)
    lo.prelude.append(
        _if_stmt(
            _attr(given, "_tag", _t_leaf("bool"), span),
            [_assign(result, present, span)],
            [_assign(result, absent, span)],
            span,
        )
    )
    return _name(result, namespace, span)
