"""The own-inference arm: a local, checker-free cross-module fixpoint.

The `own` lane answers the same question `inferrunner` answers with the
pyrefly arm — what a package proves about its unannotated defs — without
spawning a checker. Three evidence sources, applied as a bounded fixpoint
over the package's state tables (`def_params[qualified][param] -> TypeFacts`,
`def_returns[qualified] -> TypeFacts`):

  1. defaults: an unannotated parameter with a literal default binds to it.
  2. call sites: an unannotated parameter whose every resolved call site in
     the package passes one proven type binds to it (`pins_from_calls`, the
     shared all-or-nothing soundness rule — a site the engine cannot type
     is a disagreement, so the parameter stays fenced).
  3. returns: a def whose return expressions all agree on one type.

The lane stays inside the subset's type vocabulary (scalars, homogeneous
containers, simple class names) and never invents a fact it cannot prove
locally; where pyrefly proves more (method calls on user classes, flow
analysis through branches, `self.<field>` attributes), the own arm proves
less and the fences stay. The coverage numbers live on the compat site;
here the contract is only: every fact this lane reports is provable from
the package's own source by the rules above.

The per-file `BridgeFacts` this lane builds feed the same
`BridgeProvider(pins=..., pinned=...)` channel the pyrefly arm uses, so the
`infer --json` report shape is identical between arms and deltas compare
directly.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pyscriptc.factsbridge import BridgeFacts, DefFacts
from pyscriptc.inferfacts import CallSiteFacts, InferFacts, parse_pin_type, pins_from_calls
from pyscriptc.inferrunner import _imported_names, _module_name, _qualified_signatures
from pyscriptc.typefacts import TypeFacts

_SCALARS = {"int", "float", "bool", "str", "bytes", "none"}
_CONTAINERS = {"list", "dict", "set", "tuple"}
_OWN_ROUNDS = 4

_BUILTIN_RETURNS: dict[str, str] = {
    "len": "int",
    "abs": "int",
    "ord": "int",
    "hash": "int",
    "repr": "str",
    "ascii": "str",
    "bin": "str",
    "hex": "str",
    "oct": "str",
    "chr": "str",
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "bytes": "bytes",
    "sorted": "list",
    "sum": "int",
    "round": "int",
    "format": "str",
    "input": "str",
    "divmod": "tuple",
}

# The method tables: a plain kind a call on a value of that base kind
# returns. These are the subset methods the lowerer itself lowers natively;
# a method outside the tables stays untyped here, never guessed.
_STR_METHODS: dict[str, str] = {
    "strip": "str",
    "lstrip": "str",
    "rstrip": "str",
    "lower": "str",
    "upper": "str",
    "title": "str",
    "capitalize": "str",
    "casefold": "str",
    "replace": "str",
    "format": "str",
    "format_map": "str",
    "join": "str",
    "zfill": "str",
    "center": "str",
    "ljust": "str",
    "rjust": "str",
    "removeprefix": "str",
    "removesuffix": "str",
    "encode": "bytes",
    "split": "list",
    "rsplit": "list",
    "splitlines": "list",
    "partition": "tuple",
    "rpartition": "tuple",
    "startswith": "bool",
    "endswith": "bool",
    "isdigit": "bool",
    "isalpha": "bool",
    "isalnum": "bool",
    "isspace": "bool",
    "isupper": "bool",
    "islower": "bool",
    "istitle": "bool",
    "isidentifier": "bool",
    "isascii": "bool",
    "isnumeric": "bool",
    "find": "int",
    "rfind": "int",
    "index": "int",
    "rindex": "int",
    "count": "int",
}
_LIST_METHODS = {"copy": "list", "index": "int", "count": "int"}
_DICT_METHODS = {"copy": "dict", "keys": "list", "values": "list", "items": "list", "get": "dict"}
_SET_METHODS = {"copy": "set"}
_BYTES_METHODS = {"decode": "str", "hex": "str"}

_METHOD_TABLES: dict[str, dict[str, str]] = {
    "str": _STR_METHODS,
    "bytes": _BYTES_METHODS,
    "list": _LIST_METHODS,
    "dict": _DICT_METHODS,
    "set": _SET_METHODS,
}


def _widen(facts: TypeFacts) -> TypeFacts:
    """A literal widens to its base type (`Literal[1]` to `int`)."""
    if facts.kind == "literal" and facts.args:
        return facts.args[0]
    return facts


def _same_shape(one: TypeFacts, other: TypeFacts) -> bool:
    """Whether two facts name the same type, ignoring the literal value."""
    if one.kind != other.kind or one.optional != other.optional:
        return False
    if len(one.args) != len(other.args):
        return False
    return all(_same_shape(a, b) for a, b in zip(one.args, other.args, strict=True))


def _const_facts(node: ast.expr) -> TypeFacts | None:
    """The fact a literal constant carries, or None."""
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return TypeFacts(kind="none", runtime_repr="None")
        if isinstance(value, bool):
            return TypeFacts(kind="bool", runtime_repr="bool")
        if isinstance(value, int):
            return TypeFacts(kind="int", runtime_repr="int")
        if isinstance(value, float):
            return TypeFacts(kind="float", runtime_repr="float")
        if isinstance(value, str):
            return TypeFacts(kind="str", runtime_repr="str")
        if isinstance(value, bytes):
            return TypeFacts(kind="bytes", runtime_repr="bytes")
        return None
    if isinstance(node, ast.JoinedStr):
        return TypeFacts(kind="str", runtime_repr="str")
    return None


def _display_facts(node: ast.expr) -> TypeFacts | None:
    """A display (list/dict/set/tuple literal or comprehension), no element types."""
    if isinstance(node, (ast.List, ast.ListComp)):
        return TypeFacts(kind="list", runtime_repr="list")
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return TypeFacts(kind="dict", runtime_repr="dict")
    if isinstance(node, (ast.Set, ast.SetComp)):
        return TypeFacts(kind="set", runtime_repr="set")
    if isinstance(node, ast.Tuple):
        return TypeFacts(kind="tuple", runtime_repr="tuple")
    return None


def _annotation_facts(node: ast.expr | None) -> TypeFacts | None:
    """The fact a simple annotation names: scalars, containers, one class name."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return parse_pin_type(node.value.strip())
    if isinstance(node, ast.Name):
        if node.id in _SCALARS:
            return TypeFacts(kind=node.id, runtime_repr=node.id)
        if node.id.isidentifier() and node.id[0].isupper():
            return TypeFacts(kind="class", runtime_repr=node.id)
        return None
    if isinstance(node, ast.Attribute):
        return TypeFacts(kind="class", runtime_repr=ast.unparse(node))
    if isinstance(node, ast.Subscript):
        base = _annotation_facts(node.value)
        if base is None:
            return None
        if base.kind in {"list", "set", "tuple"}:
            elem = None if isinstance(node.slice, ast.Starred) else _annotation_facts(node.slice)
            if elem is None:
                return None
            return TypeFacts(kind=base.kind, args=(elem,), runtime_repr=f"{base.kind}[{elem.runtime_repr}]")
        if base.kind == "dict":
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                return None
            key = _annotation_facts(node.slice.elts[0])
            value = _annotation_facts(node.slice.elts[1])
            if key is None or value is None:
                return None
            return TypeFacts(
                kind="dict",
                args=(key, value),
                runtime_repr=f"dict[{key.runtime_repr}, {value.runtime_repr}]",
            )
        return base
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_facts(node.left)
        right = _annotation_facts(node.right)
        if right is not None and right.kind == "none" and left is not None and not left.optional:
            return TypeFacts(kind=left.kind, args=left.args, optional=True, runtime_repr=left.runtime_repr)
        return left
    return None


class ModuleState:
    """The in-memory inference state of one module."""

    def __init__(self, path: Path, source: str, module: str) -> None:
        self.path = path
        self.source = source
        self.module = module
        self.tree = ast.parse(source, filename=str(path))
        self.imports = _imported_names(self.tree, module)
        # Module-level names with a proven type (the assignment tables).
        self.globals: dict[str, TypeFacts] = {}
        # Local def name -> {param: TypeFacts}, proven (annotations, defaults, pins).
        self.proven_params: dict[str, dict[str, TypeFacts]] = {}
        # Local def name -> proven return type, or None when unproven.
        self.proven_returns: dict[str, TypeFacts | None] = {}

    def qualified(self, name: str) -> str:
        return f"{self.module}.{name}"


def _positional_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple:
    """A def's positional parameters zipped with their defaults."""
    plain = list(node.args.posonlyargs) + list(node.args.args)
    defaults: list = [None] * (len(plain) - len(node.args.defaults))
    defaults.extend(node.args.defaults)
    return plain, defaults


def _param_defaults_facts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, TypeFacts]:
    """Facts from a def's own annotations and literal defaults."""
    out: dict[str, TypeFacts] = {}
    plain, defaults = _positional_args(node)
    for arg, default in zip(plain, defaults, strict=False):
        facts = _annotation_facts(arg.annotation)
        if facts is None and default is not None:
            facts = _const_facts(default) or _display_facts(default)
        if facts is not None and facts.kind != "none":
            out[arg.arg] = facts
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
        facts = _annotation_facts(arg.annotation)
        if facts is None and default is not None:
            facts = _const_facts(default) or _display_facts(default)
        if facts is not None and facts.kind != "none":
            out[arg.arg] = facts
    return out


class Typer:
    """Types expressions from locals, the module's globals and the def tables."""

    def __init__(self, states: dict[str, ModuleState], state: ModuleState) -> None:
        self.states = states
        self.state = state

    def def_return(self, qualified: str) -> TypeFacts | None:
        """A def's proven return type by qualified name, across modules."""
        head, _, name = qualified.rpartition(".")
        member = self.states.get(head)
        if member is not None and name in member.proven_returns:
            return member.proven_returns[name]
        return None

    def type_of(self, node: ast.expr, locals_: dict[str, TypeFacts]) -> TypeFacts | None:
        """The proven type of one expression, or None when it stays untyped."""
        facts = _const_facts(node)
        if facts is not None:
            return facts
        display = _display_facts(node)
        if display is not None:
            return display
        if isinstance(node, ast.Name):
            return locals_.get(node.id) or self.state.globals.get(node.id)
        if isinstance(node, ast.Call):
            return self.type_of_call(node, locals_)
        if isinstance(node, ast.Attribute):
            base = self.type_of(node.value, locals_)
            if base is None:
                return None
            kind = _METHOD_TABLES.get(base.kind, {}).get(node.attr)
            if kind is not None:
                return TypeFacts(kind=kind, runtime_repr=kind)
            return None
        if isinstance(node, ast.BinOp):
            left = self.type_of(node.left, locals_)
            right = self.type_of(node.right, locals_)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                if left.kind == right.kind and left.kind in {"str", "bytes", "int", "float"}:
                    return left
                if {left.kind, right.kind} == {"int", "float"}:
                    return TypeFacts(kind="float", runtime_repr="float")
                if left.kind == "list" and right.kind == "list" and _same_shape(left, right):
                    return left
            if isinstance(node.op, (ast.Sub, ast.FloorDiv, ast.Pow)):
                if left.kind == right.kind and left.kind in {"int", "float"}:
                    return left
                if {left.kind, right.kind} == {"int", "float"}:
                    return TypeFacts(kind="float", runtime_repr="float")
            if isinstance(node.op, ast.Div):
                if left.kind in {"int", "float"} and right.kind in {"int", "float"}:
                    return TypeFacts(kind="float", runtime_repr="float")
            if isinstance(node.op, ast.Mod):
                if left.kind == "str" and right.kind == "str":
                    return left
                if left.kind in {"int", "float"} and right.kind in {"int", "float"}:
                    return left
            return None
        if isinstance(node, ast.Compare):
            return TypeFacts(kind="bool", runtime_repr="bool")
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return TypeFacts(kind="bool", runtime_repr="bool")
            operand = self.type_of(node.operand, locals_)
            if operand is not None and operand.kind in {"int", "float"}:
                return operand
            return None
        if isinstance(node, ast.BoolOp):
            arms = [self.type_of(value, locals_) for value in node.values]
            known = [arm for arm in arms if arm is not None]
            if len(known) == len(node.values) and all(_same_shape(known[0], arm) for arm in known):
                return known[0]
            return None
        if isinstance(node, ast.IfExp):
            body = self.type_of(node.body, locals_)
            orelse = self.type_of(node.orelse, locals_)
            if body is not None and orelse is not None and _same_shape(body, orelse):
                return body
            return None
        if isinstance(node, ast.Subscript):
            base = self.type_of(node.value, locals_)
            if base is None:
                return None
            if base.kind == "str":
                return TypeFacts(kind="str", runtime_repr="str")
            if base.kind in {"list", "dict", "tuple"}:
                return base
            return None
        if isinstance(node, ast.Starred):
            return self.type_of(node.value, locals_)
        if isinstance(node, ast.NamedExpr):
            return self.type_of(node.value, locals_)
        return None

    def type_of_call(self, node: ast.Call, locals_: dict[str, TypeFacts]) -> TypeFacts | None:
        """The proven type of a call: a builtin, a method, or a def's return."""
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in _BUILTIN_RETURNS and func.id not in self.state.proven_returns:
                kind = _BUILTIN_RETURNS[func.id]
                return TypeFacts(kind=kind, runtime_repr=kind)
            facts = self.def_return(self.state.qualified(func.id))
            if facts is not None and facts.kind != "none":
                return facts
            imported = self.state.imports.get(func.id)
            if imported is not None:
                facts = self.def_return(imported)
                if facts is not None and facts.kind != "none":
                    return facts
            return None
        if isinstance(func, ast.Attribute):
            base = self.type_of(func.value, locals_)
            if base is None:
                return None
            kind = _METHOD_TABLES.get(base.kind, {}).get(func.attr)
            if kind is not None:
                return TypeFacts(kind=kind, runtime_repr=kind)
            # A method call on a user class has no local answer.
            return None
        return None


def bind_body_locals(
    typer: Typer,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    seed: dict[str, TypeFacts],
) -> dict[str, TypeFacts]:
    """The locals a call-site walk and a return scan can read, source order.

    A name assigned disagreeing types drops out of scope (no union is
    invented); nested defs and class bodies are opaque.
    """
    locals_: dict[str, TypeFacts] = dict(seed)

    def _bind(name: str, facts: TypeFacts | None) -> None:
        if facts is None or facts.kind == "none":
            return
        known = locals_.get(name)
        if known is not None and not _same_shape(known, facts):
            locals_.pop(name, None)
            return
        locals_[name] = facts

    def _visit(body: list[ast.stmt]) -> None:
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(item, ast.Assign):
                facts = typer.type_of(item.value, locals_)
                if len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    _bind(item.targets[0].id, facts)
                elif len(item.targets) == 1 and isinstance(item.targets[0], ast.Tuple) and facts is not None:
                    if facts.kind == "list" and facts.args:
                        for elt in item.targets[0].elts:
                            if isinstance(elt, ast.Name):
                                _bind(elt.id, facts.args[0])
                    elif facts.kind == "tuple" and len(facts.args) == len(item.targets[0].elts):
                        for elt, elem in zip(item.targets[0].elts, facts.args, strict=True):
                            if isinstance(elt, ast.Name):
                                _bind(elt.id, elem)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                facts = _annotation_facts(item.annotation)
                if facts is None and item.value is not None:
                    facts = typer.type_of(item.value, locals_)
                _bind(item.target.id, facts)
            elif isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Name):
                left = locals_.get(item.target.id)
                if left is not None:
                    binop = ast.BinOp(left=ast.Name(id=item.target.id, ctx=ast.Load()), op=item.op, right=item.value)
                    ast.copy_location(binop, item)
                    _bind(item.target.id, typer.type_of(binop, locals_))
            elif isinstance(item, (ast.For, ast.AsyncFor)):
                if isinstance(item.target, ast.Name):
                    iter_facts = typer.type_of(item.iter, locals_)
                    elem = None
                    if iter_facts is not None:
                        if iter_facts.kind == "list" and iter_facts.args:
                            elem = iter_facts.args[0]
                        elif iter_facts.kind == "str":
                            elem = TypeFacts(kind="str", runtime_repr="str")
                        elif iter_facts.kind == "dict" and iter_facts.args:
                            elem = iter_facts.args[0]
                    _bind(item.target.id, elem)
                _visit(item.body)
                _visit(item.orelse)
            elif isinstance(item, (ast.While, ast.If)):
                _visit(item.body)
                _visit(item.orelse)
            elif isinstance(item, ast.With):
                for entry in item.items:
                    if isinstance(entry.optional_vars, ast.Name):
                        _bind(entry.optional_vars.id, typer.type_of(entry.context_expr, locals_))
                _visit(item.body)
            elif isinstance(item, (ast.Try, ast.TryStar)):
                _visit(item.body)
                for handler in item.handlers:
                    _visit(handler.body)
                _visit(item.orelse)
                _visit(item.finalbody)

    _visit(fn.body)
    return locals_


class OwnEngine:
    """The own-inference arm over one tree: a bounded local fixpoint."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.states: dict[str, ModuleState] = {}
        self.signatures = _qualified_signatures(self.root)
        for path in sorted(self.root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                continue
            try:
                state = ModuleState(path, source, _module_name(path, self.root))
            except SyntaxError:
                continue
            self.states[state.module] = state
        self.facts = InferFacts(provider="own")
        self.rounds_run = 0

    def run(self, rounds: int = _OWN_ROUNDS) -> None:
        """Run the fixpoint to stability, bounded like the pyrefly arm's passes."""
        # Round 0 state: annotations and defaults.
        for state in self.states.values():
            for node in ast.walk(state.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    base = _param_defaults_facts(node)
                    slot = state.proven_params.setdefault(node.name, {})
                    for param, facts in base.items():
                        slot[param] = facts
        for current in range(rounds):
            self.rounds_run = current + 1
            # Returns from bodies.
            for state in self.states.values():
                typer = Typer(self.states, state)
                for node in ast.walk(state.tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    state.proven_returns[node.name] = self._def_return_of(state, node, typer)
            # Call sites with locally typed arguments, reduced to pins.
            calls: list[CallSiteFacts] = []
            for state in self.states.values():
                typer = Typer(self.states, state)
                self._module_globals(state, typer)
                calls.extend(self._call_sites(state, typer))
            pins = pins_from_calls(calls, self.signatures)
            converged = pins == self.facts.pins and current > 0
            self.facts.calls = calls
            self.facts.pins = pins
            if converged:
                break
            # Fold pins into the state: the callees' proven params. The
            # next round's returns and call sites read them.
            for (callee, param), pin in pins.items():
                head, _, name = callee.rpartition(".")
                member = self.states.get(head)
                if member is None:
                    continue
                facts = parse_pin_type(pin.kind)
                if facts is None:
                    continue
                member.proven_params.setdefault(name, {})[param] = facts

    def _def_return_of(
        self,
        state: ModuleState,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        typer: Typer,
    ) -> TypeFacts | None:
        """The return type a def's body proves under the current state."""
        if node.returns is not None:
            return None
        seed = dict(state.proven_params.get(node.name, {}))
        locals_ = bind_body_locals(typer, node, seed)
        returns = [stmt.value for stmt in ast.walk(node) if isinstance(stmt, ast.Return) and stmt.value is not None]
        if not returns:
            return None
        resolved: list[TypeFacts] = []
        for value in returns:
            facts = typer.type_of(value, locals_)
            if facts is None:
                return None
            resolved.append(_widen(facts))
        if not all(_same_shape(resolved[0], one) for one in resolved):
            return None
        if resolved[0].kind not in _SCALARS | _CONTAINERS:
            return None
        return resolved[0]

    def _module_globals(self, state: ModuleState, typer: Typer) -> None:
        """The module-level assignment tables, read under the current state."""
        for node in state.tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                facts = _annotation_facts(node.annotation)
                if facts is None and node.value is not None:
                    facts = typer.type_of(node.value, {})
                if facts is not None:
                    state.globals[node.target.id] = facts
            elif isinstance(node, ast.Assign):
                facts = typer.type_of(node.value, {})
                if facts is None:
                    facts = _display_facts(node.value)
                if facts is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            state.globals[target.id] = facts

    def _call_sites(self, state: ModuleState, typer: Typer) -> list[CallSiteFacts]:
        """The call sites with locally typed arguments (one locals walk per def)."""
        calls: list[CallSiteFacts] = []
        call_locals: dict[tuple[int, int], dict[str, TypeFacts]] = {}
        for node in ast.walk(state.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seed = dict(state.proven_params.get(node.name, {}))
            locals_ = bind_body_locals(typer, node, seed)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    call_locals[sub.lineno, sub.col_offset] = locals_
        for node in ast.walk(state.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            local = node.func.id
            if local in state.imports:
                callee = state.imports[local]
            elif local in state.proven_returns:
                callee = state.qualified(local)
            else:
                continue
            locals_ = call_locals.get((node.lineno, node.col_offset), {})
            params: dict[str, TypeFacts] = {}
            resolved = True
            for index, arg in enumerate(node.args):
                facts = typer.type_of(arg, locals_)
                if facts is None:
                    resolved = False
                    break
                params[f"#{index}"] = facts
            if resolved:
                for kw in node.keywords:
                    if kw.arg is None:
                        resolved = False
                        break
                    facts = typer.type_of(kw.value, locals_)
                    if facts is None:
                        resolved = False
                        break
                    params[kw.arg] = facts
            calls.append(
                CallSiteFacts(
                    module=state.module,
                    callee=callee,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    params=params if resolved else {},
                )
            )
        return calls

    def per_file_facts(self) -> dict[Path, BridgeFacts]:
        """The per-file facts the bridge hands to lowering.

        Keyed like `pinned_facts` by the resolved original path. The
        expression facts are read inside a body's own local scope with the
        proven (pinned) parameters seeded, the way the pyrefly arm's
        pinned re-check sees them. Def facts are keyed `line:col` by
        `BridgeFacts.def_at`; a fact an annotation already states is left
        out, so lowering never applies it twice.
        """
        out: dict[Path, BridgeFacts] = {}
        for state in self.states.values():
            typer = Typer(self.states, state)
            expressions: dict[str, TypeFacts] = {}
            defs: dict[str, DefFacts] = {}
            scope_locals: dict[tuple[int, int], dict[str, TypeFacts]] = {}
            for node in ast.walk(state.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                seed = dict(state.proven_params.get(node.name, {}))
                locals_ = bind_body_locals(typer, node, seed)
                for sub in ast.walk(node):
                    line = getattr(sub, "lineno", None)
                    col = getattr(sub, "col_offset", None)
                    if line is None or col is None:
                        continue
                    scope_locals[line, col] = locals_
            for node in ast.walk(state.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = dict(state.proven_params.get(node.name, {}))
                    plain, _defaults = _positional_args(node)
                    for arg in plain:
                        if arg.annotation is not None:
                            params.pop(arg.arg, None)
                    for arg in node.args.kwonlyargs:
                        if arg.annotation is not None:
                            params.pop(arg.arg, None)
                    ret = None if node.returns is not None else state.proven_returns.get(node.name)
                    defs[f"{node.lineno}:{node.col_offset}"] = DefFacts(name=node.name, params=params, return_type=ret)
                    continue
                if not isinstance(node, ast.expr):
                    continue
                locals_ = scope_locals.get((node.lineno, node.col_offset), {})
                facts = typer.type_of(node, locals_)
                if facts is not None:
                    expressions.setdefault(f"{node.lineno}:{node.col_offset}", facts)
            out[state.path.resolve()] = BridgeFacts(path=state.path, provider="own", expressions=expressions, defs=defs)
        return out


class OwnPinSource:
    """The pin-map surface a `CheckerBridge.call_site_facts` answers."""

    def __init__(self, facts: InferFacts) -> None:
        self._facts = facts

    def pins_for(self, callee: str) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for (callee_key, param), pin in self._facts.pins.items():
            if callee_key == callee or callee_key.endswith("." + callee):
                merged.setdefault(param, pin)
        return merged


class OwnResult:
    """What one own-arm run proves: the pins and the per-file bridge facts."""

    def __init__(self, engine: OwnEngine) -> None:
        self.engine = engine
        self.facts = engine.facts
        self.per_file = engine.per_file_facts()


def build_own_result(root: Path) -> OwnResult:
    """One own-arm run: run the engine, then read both fact surfaces.

    The pins and the per-file facts come from the same engine state — the
    fixpoint's folded proven params feed both, the way the pyrefly arm's
    pinned re-check re-checks with its pins applied.
    """
    engine = OwnEngine(root)
    engine.run()
    return OwnResult(engine)
