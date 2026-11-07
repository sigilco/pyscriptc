"""Lower CPython's ast into the spike IR.

Three gates are lowered: a closed import table and calls, containers
(`list`, `dict`, `str`/`bytes` methods, `for`, exceptions), and annotated
classes. `for` is desugared to `while` so the iterator is consumed once and
a write to the loop target does not affect the next step. The codec wave
(`re`, `json`, `urllib.parse`, `base64`, `hashlib`, `struct`) lowers a
closed set of calls. Unknown syntax and queued stdlib modules stay fences.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any, NoReturn

from pyscriptc import _irnode as _ir
from pyscriptc.builtins import USER_EXC_BASE
from pyscriptc.coro import CoroMixin
from pyscriptc.errors import format_diagnostic, span_of
from pyscriptc.ffi_manifest import is_handle_type  # noqa: F401  # moved-verbatim import
from pyscriptc.ir import IR_VERSION, SUBSET, cpython_version, types_equal
from pyscriptc.lower._boxed_resolve import BoxedResolveMixin
from pyscriptc.lower._calls import CallsMixin
from pyscriptc.lower._classes import ClassesMixin
from pyscriptc.lower._closures import ClosuresMixin
from pyscriptc.lower._core import (
    _TRUTHY,
    Binding,
    Fence,
    _any_accepts,
    _attr,
    _compare,
    _const_none,
    _const_str,
    _declared_globals,
    _has_typevar,  # noqa: F401  # moved-verbatim import
    _maybe_type_kind,
    _node_kind,
    _node_type_kind,
    _show,
    _subst_type,
    _type_kind,
    _type_rt,
    module_needs_slot,
)
from pyscriptc.lower._coro_adapter import coro_module  # noqa: F401  # moved-verbatim import
from pyscriptc.lower._entry import EntryMixin
from pyscriptc.lower._expr import ExprMixin
from pyscriptc.lower._infer import TypeInferMixin
from pyscriptc.lower._misc_emit import MiscEmitMixin
from pyscriptc.lower._module_driver import ModuleDriverMixin
from pyscriptc.lower._stdlib import StdlibDispatchMixin
from pyscriptc.lower._stmt import (
    StmtMixin,
    _always_returns,
    _ast_exits,
    _is_docstring,
    _stmt_breaks_this_loop,
    _stmt_continues_this_loop,
)
from pyscriptc.mobile import MOBILE_EXCLUDED_SYMBOLS, is_mobile_target, mobile_fence_hint
from pyscriptc.stdlib_queue import (
    PACK_MODULES,
    canonical_module,
    dependency_lanes,
    ffi_modules,
    module_status,
    staged_resources,
)
from pyscriptc.stdmore import PTR_CLASSES, member_callable, module_constant
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict
from pyscriptc.tirserde import dict_to_span as dict_to_span_local


def _mk_span(span: dict[str, Any]) -> Any:
    """The Span record the dict-IR span spells."""

    return dict_to_span_local(span)


def _mk_type(typ: dict[str, Any]) -> Any:
    """The T record the dict-IR type spells."""

    return dict_to_type(typ)


def _call_node(callee: dict[str, Any], args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> Any:
    """The call node one dict-IR call spells."""

    return node_to_dict(
        _ir.call(dict_to_node(callee), [dict_to_node(arg) for arg in args], _mk_type(typ), _mk_span(span), "", "", -1)
    )


def _function_call(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> Any:
    """The call to a lowered function."""

    return _call_node({"kind": "function", "name": name}, args, typ, span)


def _builtin_call(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> Any:
    """The call to a builtin."""

    return _call_node({"kind": "builtin", "name": name}, args, typ, span)


def _abi_call_node(
    module: str, name: str, args: list[dict[str, Any]], typ: dict[str, Any], symbol: str, span: dict[str, Any]
) -> Any:
    """The module call node with the ABI symbol one `_abi_call` spells."""

    node = _ir.call(
        _ir.module_callee(module, name),
        [dict_to_node(arg) for arg in args],
        _mk_type(typ),
        _mk_span(span),
        symbol,
        "",
        -1,
    )
    return node_to_dict(node)


def _expr_node(expr: dict[str, Any], span: dict[str, Any]) -> Any:
    """The expression statement node."""

    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _unary_not_node(operand: dict[str, Any], span: dict[str, Any]) -> Any:
    """The `not operand` node with the bool type."""

    return node_to_dict(_ir.unary("not", dict_to_node(operand), _ir.t_leaf("bool"), _mk_span(span)))


def _closure_node(name: str, params: list[dict[str, Any]], ret: dict[str, Any], span: dict[str, Any]) -> Any:
    """The closure node with the empty environment."""

    return node_to_dict(
        _ir.closure(name, [], _ir.t_func([dict_to_type(p["type"]) for p in params], dict_to_type(ret)), _mk_span(span))
    )


def _maybe_node_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of an IR node, or None when the record carries none."""

    if "kind" not in node:
        return None
    return _node_kind(node)


def _record_kind(record: dict[str, Any]) -> str:
    """The kind tag of a record the typed layer cannot carry.

    A package export row (`func`, `const`, `class`, `module`) is not IR and
    has no typed record, so its tag reads off the plain dict in one place.
    """
    kind: str = record["kind"]
    return kind


class Lowerer(
    CoroMixin,
    StdlibDispatchMixin,
    ExprMixin,
    StmtMixin,
    CallsMixin,
    ClassesMixin,
    ClosuresMixin,
    TypeInferMixin,
    EntryMixin,
    BoxedResolveMixin,
    MiscEmitMixin,
    ModuleDriverMixin,
):
    def __init__(self, filename: str, source: str, target: str | None = None, config: Any = None) -> None:
        self.filename = filename
        self.source = source
        # The compile target triple, None on desktop compiles. Mobile targets
        # turn the surfaces whose runtime the archive excludes into fences.
        self.target = target
        self.config = config
        if self.config is None and filename != "<stdin>":
            try:
                from pyscriptc.config import load_config

                p = Path(filename)
                if p.exists() or (p.parent and p.parent.exists()):
                    self.config = load_config(p)
            except Exception:
                self.config = None
        if self.config is None and "# /// script" in source:
            try:
                from pyscriptc.config import _from_table, _project_table, read_pep723

                meta = read_pep723(source)
                if meta:
                    meta_table = _project_table(meta, Path(filename))
                    if meta_table is not None:
                        self.config = _from_table(meta_table, Path(filename), "pep723")
            except Exception:
                pass
        self.function: str | None = None
        self.sigs: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        self.locals: dict[str, dict[str, Any]] = {}
        self.bindings: dict[str, Binding] = {}
        self.classes: dict[str, dict[str, Any]] = {}
        self.declared: set[str] = set()
        self.known_classes: set[str] = set()
        self.synthetics: set[str] = set()
        self.temp_n = 0
        self.caught: list[Fence] = []
        self.prelude: list[dict[str, Any]] = []
        self.constants: dict[str, dict[str, Any]] = {}
        self.type_aliases: dict[str, str] = {}
        self.comp_elt: ast.expr | None = None
        self.comp_key: ast.expr | None = None
        self.comp_kind = "list"
        self.comp_gens: list[ast.comprehension] = []
        self.comp_ifs: list[ast.expr] = []
        self.comp_dest: str | None = None
        self.comp_init: dict[str, Any] | None = None
        self.comp_expect: dict[str, Any] | None = None
        self.comp_ready = False
        # `all`/`any` over a comprehension fuses the loop into a bool
        # accumulator instead of materializing the element list: the pair
        # is (result name, want_all). None means the comp builds its list.
        self.comp_pred: tuple[str, bool] | None = None
        # The inference peek (issue #500): while True, `_expr` is running
        # speculatively to learn a value's type; the emitted IR and the
        # temp counter are discarded, and any fence becomes the inference
        # refusal (`PS1001` with a hint), never the inner diagnostic.
        self.inf_peek = False
        self.inf_depth = 0
        # The expected type the caller of the peek wants for an empty
        # display: None inside the peek (ambiguity is a fence, never a
        # guess), the annotation's type on the real pass.
        self.peek_infer: dict[str, Any] | None = None
        self.break_flag: str | None = None
        self.continue_flag: str | None = None
        self.broke_flag = None
        # Active except handlers: bound name, or a bare-raise temp once made.
        self.exc_stack: list[str] = []
        # `except ... as name` deletes the name at handler end (CPython del).
        self.dead_names: set[str] = set()
        # Manifest handle ownership (issue #224): local name -> the handle
        # type it carries. A consumed name moves here-then-out: `_ffi_call`
        # removes the entry on consume so a later read is a fence, and the
        # function-end pass frees what is still owned.
        self.handle_locals: dict[str, dict[str, Any]] = {}
        # Names a consuming call killed. A later read of one fences with
        # PS4010 (use after consume).
        self.consumed_handles: set[str] = set()
        # Handle names this function returned. A returned handle belongs to
        # the caller, so the scope exit does not free it (issue #224).
        self.returned_handles: set[str] = set()
        # Names holding a borrowed handle: the function got it from its caller
        # (a handle-typed parameter, or an alias of one) and must hand it back
        # alive, so the scope exit never frees it (issue #224).
        self.borrowed_handles: set[str] = set()
        # boxed:begin
        # Island module name -> the hidden local that owns its handle
        # (issue #553). Reset per function scope, like `handle_locals`.
        self.island_modules: dict[str, str] = {}
        # boxed:begin
        # Type peeks outside a lowering scope (the boxed top-level probe,
        # the call-site pin pass, issues #650 and #643) set this while they
        # infer: an island call peeked there cannot import a module handle,
        # so `_island_module_handle` returns an isolated handle node instead
        # of binding one.
        self.peeking = False
        # boxed:end
        # The boxed per-definition fallback tier (issue #612): when true, a
        # definition whose body fences is planned for the island instead of
        # refusing the compile. Set by `--fallback=cpython` / `--island`
        # through `boxed_definitions`.
        from pyscriptc.boxed import definitions_tier

        self.fallback_definitions = definitions_tier()
        # Names this compile plans for the island, from the fence list.
        self.island_fns: set[str] = set()
        # Class definitions planned for the island (issue #628): the island
        # seed defines them in CPython and compiled callers reach the class
        # object through a handle.
        self.island_classes: set[str] = set()
        # Class name -> the hidden local that owns its island handle, reset
        # per function scope like `island_modules`.
        self.island_class_handles: dict[str, str] = {}
        # The per-definition probe state: the definition list to probe and
        # the names the probe fenced.
        self._boxed_functions: list[ast.FunctionDef] = []
        self._boxed_island: set[str] = set()
        self._boxed_demoted_by: dict[str, str] = {}
        # Refs #641: the cross-module island-refusal fences, parked so the
        # module's own island planning never drops them.
        self._boxed_sibling_fences: list[Fence] = []
        # Refs #641: the module-level island refusals (a cross-package import
        # of a definition the other module planned for the island). They
        # belong to no definition and survive the island planning, so the
        # package merge adds them to the unit's fence list.
        self._boxed_module_fences: list[Fence] = []
        # The island top level (issue #613): the entry body is probed like a
        # definition, and a fenced probe plans the whole module top level for
        # the island (Decision A). `entry_fn_name` then names the entry whose
        # fences drop with the top level.
        self._boxed_top_island = False
        # boxed:end
        self.module_name = ""
        self.is_package = False
        self.cataloguing = False
        # A program directory: sibling modules are imported by their plain
        # names and `__main__.py` is the entry point.
        self.program = False
        # A program module whose body runs at import time (every program
        # module except `__main__`): it synthesizes `<module>.__init__`.
        self.module_init = False
        # Project mode: the entry module keeps its dotted name but plays the
        # `__main__` role (its script body is the program's entry).
        self.entry_module = False
        # The program root package name, when the tree is a program. It is the
        # prefix for the program's own package-style names.
        self.program_root = ""
        # The top-level names of the dependency source roots (issue #398):
        # a program module may import them by their plain names, the way
        # `sys.path` resolves an unpacked wheel's packages.
        self.program_roots: set[str] = set()
        self.package: dict[str, dict[str, dict[str, Any]]] | None = None
        self.pkg_funcs: dict[str, dict[str, Any]] = {}
        self.class_aliases: dict[str, str] = {}
        self.global_slots: dict[str, dict[str, Any]] = {}
        self.fn_globals: set[str] = set()
        # `nonlocal` declarations for the function being lowered.
        self.fn_nonlocals: set[str] = set()
        # Env-bound names for the function being lowered (its captured outer
        # slots). Top-level functions have none.
        self.captures: dict[str, dict[str, Any]] = {}
        # Env-first nested functions collected while lowering a module.
        self.nested_fns: list[dict[str, Any]] = []
        self.nested_n = 0
        # `name = const` initializers for active global slots, emitted at the
        # top of main as ps_global_set_* calls.
        self.global_inits: list[dict[str, Any]] = []
        # Module assignments and decorated definitions whose value may be a
        # callable: they need every function signature, which does not exist
        # during `_split_entry`, so they bind after the sigs pass, in source
        # order.
        self.deferred_module: list[ast.stmt] = []
        # Re-entrancy guard: `_bind_module_assign` defers candidates unless a
        # deferred bind is replaying it.
        self._deferring = False
        # Names of the current function's locals boxed into heap cells because
        # a nested callable captures them by pointer.
        self.boxed_names: set[str] = set()
        # Line of the first true script statement; global inits before it may
        # hoist, later ones lower inside the entry to keep source order.
        self._script_line: int | None = None
        # Name of the synthesized entry function (`main`/`__entry__`), if any.
        self.entry_fn_name: str | None = None
        # `global` declarations per function body, cached by body-list identity:
        # the scan pass and the lowering pass would otherwise walk each function
        # tree twice for the same `ast.Global` nodes. Bodies are not mutated
        # after parsing, so identity is a stable key within one lower_file run.
        self._globals_cache: dict[int, frozenset[str]] = {}
        # `global X` names a function assigns from a function-local import
        # (`from pyscriptc import assess as _assess` then `assess = _assess`):
        # X resolves at module scope exactly like the top-level import would.
        # The record carries what the replayed import bound so the write can
        # prove it is the alias init and not some other store. Populated by
        # `_scan_global_aliases` before any body lowers.
        self.global_aliases: dict[str, dict[str, Any]] = {}
        # The function body being lowered, so `del name` can see a later load.
        self.fn_body: list[ast.stmt] = []
        # Names bound to frozenset(...). The value is still the set list. .add and
        # .remove on these names stay fenced. A plain set name is not in this set.
        self.frozen_names: set[str] = set()
        # `if x is not None:` narrowing on a tagged optional: inside the taken
        # branch a read of the name lowers to its `_val` field. name -> opt typ.
        self.opt_unwrap: dict[str, dict[str, Any]] = {}
        # Proven element types of Any-container reads (issue #781), keyed by
        # the read node's (line, col): the emitter loads the proven LLVM
        # shape out of the unresolved cell.
        self._any_proven: dict[tuple[int, int], dict[str, Any]] = {}
        # Full signatures (names, defaults, keyword-only flags) of nested defs,
        # keyed by the identity of the local's func type, so a call through
        # that local binds keywords like a call to a module-level def. A
        # rebound local carries a different type object and loses the entry.
        self.local_sigs: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        # User exception classes take codes after the builtin table (1–8).
        self.exc_n = USER_EXC_BASE
        # TypeVar erasure (tier-2): name -> bound type or None when unbound.
        # Annotations on an unbound TypeVar produce {"kind": "typevar"}
        # placeholders that only ever live in signatures/field tables;
        # call sites monomorphize them into concrete instantiations.
        self.typevars: dict[str, dict[str, Any] | None] = {}
        # Active substitution while lowering one instantiation.
        self.tv_subst: dict[str, dict[str, Any]] = {}
        # Functions whose signature carries placeholders, name -> def.
        self.generic_fns: dict[str, ast.FunctionDef] = {}
        # (fn, ((typevar, type_key), ...)) -> emitted name, plus the queue
        # of instantiations still to lower (fn, emitted name, mapping).
        self.inst_fns: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
        self.inst_queue: list[tuple[str, str, dict[str, dict[str, Any]]]] = []
        # (class, (type_key, ...)) -> emitted class name, plus queued
        # (base ClassDef, emitted name, mapping) class instantiations.
        self.class_insts: dict[tuple[str, tuple[str, ...]], str] = {}
        self.class_queue: list[tuple[ast.ClassDef, str, dict[str, dict[str, Any]]]] = []
        # The substituted (params, ret) while an instantiation lowers.
        self.inst_sig: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
        # Optional checker bridge (issue #87): a `CheckerBridge` that answers
        # "what type did the checker infer here" with the checker's own
        # inferred-type map. None means no bridge: lowering keeps every
        # inference fence exactly as before.
        self.bridge: Any = None

    def fail(self, node: ast.AST, code: str, message: str) -> NoReturn:
        raise Fence(code, message, span_of(node, self.filename), self.function)

    def mobile_guard(self, node: ast.AST, surface: str, key: str) -> None:
        """Fence a surface a mobile target cannot carry.

        `surface` is the call as the user wrote it (`subprocess.run`), `key`
        the excluded runtime it needs (`subprocess`, `osfd`). On a mobile
        triple the archive leaves that runtime file out, so lowering it would
        link an undefined symbol. The fence keeps the same code the desktop
        build gives and adds the mobile reason.
        """
        if not is_mobile_target(self.target):
            return
        hint = mobile_fence_hint(key)
        if hint is None:
            return
        self.fail(node, "PS2001", f"{surface} is not lowered on a mobile target: {hint}")

    def mobile_symbol_guard(self, node: ast.AST, symbol: str) -> None:
        """Fence a lowered call whose runtime symbol the mobile archive drops.

        The symbol table is the whole truth here: every runtime file the
        mobile archive leaves out defines a known set of symbols, so a call
        that names one of them cannot link, whatever the surface is called.
        """
        if not is_mobile_target(self.target):
            return
        surface = MOBILE_EXCLUDED_SYMBOLS.get(symbol)
        if surface is None:
            return
        hint = mobile_fence_hint(surface)
        assert hint is not None
        self.fail(node, "PS2001", f"{symbol} is not lowered on a mobile target: {hint}")

    def _desugar(self, tree: ast.Module) -> ast.Module:
        """Pre-lowering syntax pass. Runs once, before any statement is
        lowered (called from `module`).

        A desugar pass receives the parsed module tree and returns the tree
        to lower: a replacement `ast.Module` whose statements the lowering
        below accepts. It must not lower anything itself, touch IR state
        (`self.bindings`, `self.prelude`, `self.sigs`, ...), or collect
        fences; those belong to the lowering passes that run after it.

        Today lowering happens inline (`for` becomes `while` inside
        `_for`/`_for_range`, not before it), so this hook is the identity.
        E1 replaces the body with the first real AST-to-AST pass.
        """
        return tree

    def _ir_name(self, name: str) -> str:
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _drain_instances(
        self,
        lowered: list[dict[str, Any]],
        lowered_classes: list[dict[str, Any]],
        fences: list[Fence],
    ) -> None:
        """Lower every queued TypeVar instantiation to a concrete copy.

        Call sites discover specializations while ordinary bodies lower;
        each queued entry is lowered here with `self.tv_subst` holding its
        TypeVar mapping so annotations inside the copy resolve concretely.
        New specializations found inside a copy just extend the queues.
        """
        while self.inst_queue or self.class_queue:
            while self.class_queue:
                node, emit_name, mapping = self.class_queue.pop(0)
                self.function = emit_name
                self.caught = []
                self.tv_subst = mapping
                try:
                    record = self._lower_class(node, emit_name)
                    if record is not None and not self.caught:
                        lowered_classes.append(record)
                    fences.extend(self.caught)
                except Fence as exc:
                    fences.append(exc)
                    fences.extend(self.caught)
                finally:
                    self.tv_subst = {}
            while self.inst_queue:
                fn_name, emit_name, mapping = self.inst_queue.pop(0)
                fn = self.generic_fns[fn_name]
                self.function = emit_name
                self.temp_n = 0
                self.prelude = []
                # boxed:begin
                self.island_modules = {}
                self.island_class_handles = {}
                # boxed:end
                self.caught = []
                self.fn_globals = self._globals_for(fn.body)
                self.fn_nonlocals = set()
                self.captures = {}
                params, ret = self.sigs[fn_name]
                params = [{**param, "type": _subst_type(param["type"], mapping)} for param in params]
                ret = _subst_type(ret, mapping)
                # A param whose TypeVar resolved to none takes no runtime
                # slot (void is not a value); it drops from the signature
                # and binds as the `None` constant for the body.
                dropped = {param["name"] for param in params if _type_kind(param["type"]) == "none"}
                params = [param for param in params if param["name"] not in dropped]
                self.locals = {param["name"]: param["type"] for param in params}
                saved_consts = {name: self.constants.get(name) for name in dropped}
                for name in dropped:
                    self.constants[name] = {"kind": "const", "value": None, "type": _type_rt({"kind": "none"})}
                self.frozen_names = set()
                self.opt_unwrap = {}
                self.boxed_names = set()
                self.fn_body = list(fn.body)
                self.tv_subst = mapping
                self.inst_sig = (params, ret)
                saved_bindings = dict(self.bindings)
                saved_aliases = dict(self.type_aliases)
                saved_declared = set(self.declared)
                saved_typevars = dict(self.typevars)
                try:
                    body = self._body(list(fn.body))
                    if self.caught:
                        fences.extend(self.caught)
                        continue
                    if _type_kind(ret) != "none" and not _always_returns(body):
                        self.fail(fn, "PS1004", f"{fn.name} can fall off the end and return None")
                    record = {
                        "name": self._ir_name(emit_name),
                        "params": params,
                        "returnType": ret,
                        "locals": [{"name": name, "type": typ} for name, typ in self.locals.items()],
                        "body": body,
                        "span": span_of(fn, self.filename),
                    }
                    if self.boxed_names:
                        record["boxed"] = sorted(self.boxed_names)
                    lowered.append(record)
                except Fence as exc:
                    fences.append(exc)
                finally:
                    self.bindings = saved_bindings
                    self.type_aliases = saved_aliases
                    self.declared = saved_declared
                    self.typevars = saved_typevars
                    self.tv_subst = {}
                    self.inst_sig = None
                    for name, prev in saved_consts.items():
                        if prev is None:
                            self.constants.pop(name, None)
                        else:
                            self.constants[name] = prev

    def _bind_star(self, module: str, stmt: ast.AST, fences: list[Fence]) -> None:
        """Bind the names `from module import *` brings in.

        CPython binds the module's `__all__` when it has one and every
        public name otherwise. The export table records the module's
        `__all__` under the `__all__` key, so the star binds the same set.
        A name the table does not carry is skipped: the star import only
        brings in what the module exports.
        """

        package = self.package or {}
        exported = package.get(module)
        if exported is None:
            fences.append(
                Fence(
                    "PS2002",
                    f"{module} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        declared = exported.get("__all__")
        names = declared.get("names") if isinstance(declared, dict) else None
        for name in sorted(exported):
            if name == "__all__":
                continue
            if names is not None:
                if name not in names:
                    continue
            elif name.startswith("_"):
                continue
            self._bind_one_export(module, name, name, stmt, fences)

    def _bind_one_export(self, module: str, name: str, local: str, stmt: ast.AST, fences: list[Fence]) -> None:
        package = self.package or {}
        item = package.get(module, {}).get(name)
        if item is None:
            fences.append(
                Fence(
                    "PS2002",
                    f"{module}.{name} is not exported by this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        if local in self.bindings or local in self.constants or local in self.declared or local in self.sigs:
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        if _record_kind(item) == "const":
            self.constants[local] = item["value"]
            self.declared.add(local)
            return
        if _record_kind(item) == "func":
            # boxed:begin
            # Refs #641: the defining module planned the function for the
            # island, so the merged module emits no body for it. The runtime
            # keeps one island namespace per binary, so a call across a
            # package boundary into another module's island floor has no
            # seam to cross: the import refuses here, at module level, where
            # the fence survives the module's own island planning.
            if item.get("island"):
                fence = Fence(
                    "PS2002",
                    f"{module}.{name} runs on the island floor of its own module, and importing it "
                    "crosses a package boundary into it. Compile the tree as a program "
                    "(--program --fallback=cpython) so the whole program runs island-side, "
                    "or make the definition lower natively.",
                    span_of(stmt, self.filename),
                    None,
                )
                fences.append(fence)
                self._boxed_module_fences.append(fence)
                return
            # boxed:end
            self.pkg_funcs[local] = item
            self.bindings[local] = Binding("pyfunc", module, name, False, item["return"])
            self.declared.add(local)
            self._install_return_class(module, item["return"], stmt, fences)
            return
        if _record_kind(item) == "class":
            self._install_class_export(local, item, stmt, fences)
            return
        if _record_kind(item) == "module":
            # A module re-exported by the package (`from . import core` in
            # `pkg/__init__.py`, issue #405): the name binds the submodule.
            self._bind_pkg_module(local, str(item["module"]), stmt, fences)
            return
        fences.append(
            Fence("PS2002", f"{module}.{name} is not a function or constant", span_of(stmt, self.filename), None)
        )

    def _install_return_class(self, module: str, typ: dict[str, Any], stmt: ast.AST, fences: list[Fence]) -> None:
        """Install a function export's class result alongside the function.

        A generator function's outward type is its frame class (`gen.<name>`,
        with `__iter__`/`__next__`), so consuming the function needs the
        class shape even when the user never names the class. The defining
        module still emits it; the consumer sees it imported, like any other
        class export.
        """
        if _maybe_type_kind(typ) != "class" or typ["name"] in self.classes:
            return
        item = (self.package or {}).get(module, {}).get(typ["name"])
        if item is None or _record_kind(item) != "class":
            return
        self._install_class_export(typ["name"], item, stmt, fences)

    def _package_root(self) -> str | None:
        if self.package is None or not self.module_name:
            return None
        return self.module_name.split(".", 1)[0]

    def _is_own_package(self, module: str | None) -> bool:
        root = self._package_root()
        if root is None or not module:
            return False
        return module == root or module.startswith(root + ".")

    def _pkg_item(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = (self.package or {}).get(module, {}).get(member)
        if item is None:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _pkg_export(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = self._pkg_item(node, module, member)
        if _record_kind(item) not in {"func", "const"}:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _linked_call(self, node: ast.Call, span: dict[str, Any], item: dict[str, Any], what: str) -> dict[str, Any]:
        if _record_kind(item) != "func":
            self.fail(node, "PS2002", f"{what} is not a function")
        # boxed:begin
        # Refs #641: the defining module planned the callee for the island,
        # so the merged module emits no body for it. The runtime keeps one
        # island namespace per binary, so a call across a package boundary
        # into another module's island floor has no seam to cross: a named
        # refusal instead of a crash in the emitter.
        #
        # The refusal is a module-level fence (it belongs to no
        # definition), so it survives the module's own island planning.
        if item.get("island"):
            fence = Fence(
                "PS2002",
                f"{what} runs on the island floor of its own module, and this call crosses a "
                "package boundary into it. Compile the tree as a program "
                "(--program --fallback=cpython) so the whole program runs island-side, "
                "or make the definition lower natively.",
                span,
                None,
            )
            self._boxed_module_fences.append(fence)
            raise fence
        # boxed:end
        args = self._fill_args(node, item["params"], what)
        return _function_call(item["ir_name"], args, item["return"], span)

    def _body(self, statements: list[ast.stmt]) -> list[dict[str, Any]]:
        body: list[dict[str, Any]] = []
        saved_unwrap = dict(self.opt_unwrap)
        try:
            for index, stmt in enumerate(statements):
                if _is_docstring(stmt):
                    continue
                try:
                    lowered = self._stmt(stmt)
                except Fence as exc:
                    self.caught.append(exc)
                    continue
                body.extend(lowered)
                # `if x is None: <arm always exits>` (or any test that proves
                # x set when the other arm is the only one left) narrows x to
                # its tagged value for the rest of this body, unless a later
                # statement rebinds the name.
                if isinstance(stmt, ast.If):
                    when_true, when_false = self._narrow_names(stmt.test)
                    then_exits = _ast_exits(stmt.body) or (
                        len(lowered) == 1 and _node_kind(lowered[0]) == "if" and _always_returns(lowered[0]["then"])
                    )
                    else_exits = bool(stmt.orelse) and _ast_exits(stmt.orelse)
                    proven = when_false if then_exits and not else_exits else when_true if else_exits else set()
                    rest = statements[index + 1 :]
                    for name in proven:
                        if not any(
                            isinstance(inner, ast.Name)
                            and inner.id == name
                            and isinstance(inner.ctx, (ast.Store, ast.Del))
                            for item in rest
                            for inner in ast.walk(item)
                        ):
                            self.opt_unwrap[name] = self.locals[name]
                elif isinstance(stmt, ast.Assert):
                    # `assert x is not None` proves x set for the rest of
                    # this body, the same rule the if-exit arm applies.
                    proven, _ = self._narrow_names(stmt.test)
                    rest = statements[index + 1 :]
                    for name in proven:
                        if not any(
                            isinstance(inner, ast.Name)
                            and inner.id == name
                            and isinstance(inner.ctx, (ast.Store, ast.Del))
                            for item in rest
                            for inner in ast.walk(item)
                        ):
                            self.opt_unwrap[name] = self.locals[name]
                skips = (self.break_flag is not None and _stmt_breaks_this_loop(stmt)) or (
                    self.continue_flag is not None and _stmt_continues_this_loop(stmt)
                )
                if skips:
                    rest = self._body(statements[index + 1 :])
                    if rest:
                        body.append(self._skip_rest(rest, span_of(stmt, self.filename)))
                    return body
            return body
        finally:
            self.opt_unwrap = saved_unwrap

    def _bool_cond(self, node: ast.expr) -> dict[str, Any]:
        if isinstance(node, ast.BoolOp):
            cond = self._boolop(node, span_of(node, self.filename), as_cond=True)
        else:
            cond = self._expr(node)
        kind = _node_type_kind(cond)
        if kind == "bool":
            return cond
        if kind in _TRUTHY or cond["type"].get("optional"):
            return self._truthy(cond, span_of(node, self.filename))
        self.fail(node, "PS2004", "condition must be a bool, not a truthy value")
        raise AssertionError("unreachable")

    def _expect(self, node: ast.AST, got: dict[str, Any], want: dict[str, Any], what: str) -> None:
        if not types_equal(got, want) and not _any_accepts(want, got) and not self._accepts_protocol(want, got):
            self.fail(node, "PS1003", f"{what} is {_show(got)}, annotation says {_show(want)}")

    def _use_status(self, module: str) -> str:
        if canonical_module(module) in PACK_MODULES:
            return "lowered"
        status = module_status(module)
        if status == "lowered":
            return "lowered"
        if status == "queued":
            return "queued"
        parts = module.split(".")
        for index in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:index])
            parent_status = module_status(parent)
            if parent_status == "queued":
                return "queued"
            if parent_status == "lowered":
                return "missing"
        return "missing"

    def _finish_resolve(self, dotted: str, module: str, member: str, status: str) -> tuple[str, str, str, str]:
        # Aliased spellings (os.path -> posixpath) resolve against the
        # canonical module table; `dotted` keeps the user's spelling for
        # fence messages.
        module = canonical_module(module)
        if status != "missing":
            return dotted, module, member, status
        parts = module.split(".")
        best = 0
        for index in range(1, len(parts) + 1):
            parent = ".".join(parts[:index])
            if module_status(parent) == "lowered":
                best = index
        if best == 0:
            return dotted, module, member, status
        parent = canonical_module(".".join(parts[:best]))
        rest = parts[best:]
        if member:
            rest.append(member)
        joined = ".".join(rest)
        if not joined:
            return dotted, parent, "", "value"
        if member_callable(parent, joined) or module_constant(parent, joined) is not None:
            return dotted, parent, joined, "lowered"
        return dotted, parent, joined, "missing"

    def _store_constant(self, name: str, value: dict[str, Any], node: ast.AST) -> None:
        if name in self.bindings or name in self.declared or name in self.constants or name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is already bound")
        self.constants[name] = value
        self.declared.add(name)

    def _bind_threading_global(self, name: str, value: dict[str, Any], typ: dict[str, Any], node: ast.AST) -> None:
        """A module-level record with identity binds a global slot.

        The value is a runtime pointer: a stored constant would re-emit the
        constructor at every read, so the record's identity would not hold.
        Threading and queue records need it, and so does any list or dict
        (#512): the elements live on the heap, and every read must be the
        same object. The slot keeps one pointer for the life of the process.
        """
        slot = self.global_slots.get(name)
        if slot is not None:
            if slot.get("type") is not None and not types_equal(slot["type"], typ):
                self.fail(node, "PS2002", f"global {name} is already bound")
            slot["type"] = typ
        else:
            self.global_slots[name] = {"type": typ, "active": False}
        self._activate_slot(name, node)
        init = self._global_set(name, value, span_of(node, self.filename))
        self.global_inits.append(init["expr"])

    def _activate_slot(self, name: str, node: ast.AST) -> None:
        slot = self.global_slots[name]
        if slot.get("conflict") or slot.get("type") is None:
            self.fail(node, "PS1001", f"{name} needs a type")
        if name in self.bindings or name in self.declared or name in self.constants or name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is already bound")
        slot["active"] = True
        self.declared.add(name)

    def _assign_global(self, stmt: ast.Assign, target: str, span: dict[str, Any]) -> list[dict[str, Any]]:
        slot = self.global_slots.get(target)
        if slot is None or not slot.get("active"):
            if self._global_alias_write(stmt, target):
                return [{"kind": "pass", "span": span}]
            self.fail(stmt, "PS2002", f"global {target} is not a module slot")
        value = self._expr(stmt.value, slot["type"])
        self._expect(stmt, value["type"], slot["type"], target)
        return [self._global_set(target, value, span)]

    def _global_alias_write(self, stmt: ast.Assign, target: str) -> bool:
        """`global X` + `X = <name>` where `<name>` is the import the scan aliased.

        The write needs no runtime code: X already resolves through the
        module-scope binding the alias installed, so the statement lowers to
        a pass. Any other store to the name keeps the module-slot fence.
        """
        rec = self.global_aliases.get(target)
        if rec is None or not isinstance(stmt.value, ast.Name) or stmt.value.id == target:
            return False
        rhs = stmt.value.id
        if rec["kind"] == "binding":
            want = rec["binding"]
            found = self.bindings.get(rhs)
            return (
                found is not None
                and found.kind == want.kind
                and found.module == want.module
                and found.attr == want.attr
            )
        if rec["kind"] == "class":
            return self.class_aliases.get(rhs) == rec["class"]
        return self.constants.get(rhs) is rec["const"]

    def _slot_key(self, name: str) -> str:
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _global_get(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        slot = self.global_slots[name]
        typ = slot["type"]
        key = _text_ir(self._slot_key(name), "str", span)
        # boxed:begin
        # The island top level (issue #613): a global the island bound is a
        # read of the once-unboxed shadow slot, under the island-prefixed key.
        if slot.get("island"):
            key = _text_ir(self._slot_island_key(name), "str", span)
        # boxed:end
        if _type_kind(typ) == "str":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_str", span)
        if _type_kind(typ) == "int":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_i64", span)
        if _type_kind(typ) == "class" and typ.get("name") == "Path":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_path", span)
        if _type_kind(typ) == "func":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        if _type_kind(typ) == "class" and typ.get("name") in PTR_CLASSES:
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        if _type_kind(typ) in {"list", "dict"}:
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        raise AssertionError("unreachable")

    def _global_set(self, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        typ = value["type"]
        # boxed:begin
        # The island top level (issue #613, Decision E): a global the island
        # owns is unbox-once into the native shadow slot. A native write
        # would either vanish (the island never re-reads it) or surprise the
        # island, so it fences with the named diagnostic.
        slot = self.global_slots.get(name)
        if slot is not None and slot.get("island"):
            self.fail(
                ast.Pass(),
                "PS2002",
                f"{name} is owned by the island top level and cannot be written from compiled code: "
                "write it in a function that runs on the island, or annotate the global so it lowers native",
            )
        # boxed:end
        key = _text_ir(self._slot_key(name), "str", span)
        if _type_kind(typ) == "str":
            symbol = "ps_global_set_str"
        elif _type_kind(typ) == "int":
            symbol = "ps_global_set_i64"
        elif _type_kind(typ) == "class" and typ.get("name") == "Path":
            symbol = "ps_global_set_path"
        elif _type_kind(typ) == "func":
            symbol = "ps_global_set_ptr"
        elif _type_kind(typ) == "class" and typ.get("name") in PTR_CLASSES:
            symbol = "ps_global_set_ptr"
        elif _type_kind(typ) in {"list", "dict"}:
            symbol = "ps_global_set_ptr"
        else:
            self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        return _expr_node(
            self._abi_call("builtins", "global_set", [key, value], {"kind": "none"}, symbol, span),
            span,
        )

    def _global_is_set(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        key = _text_ir(self._slot_key(name), "str", span)
        return self._abi_call("builtins", "global_is_set", [key], {"kind": "bool"}, "ps_global_is_set", span)

    def _bind_deferred_module(self, fences: list[Fence]) -> None:
        """Module assigns and decorated defs whose value may be a callable,
        bound after the signature pass. A callable value becomes an active
        global slot (`h = f` reads it via `ps_global_get_ptr`); anything else
        replays the constant-binding path so its fences stay identical."""
        saved = (self.locals, self.captures, self.prelude, self.function)
        self.locals = {}
        self.captures = {}
        self.prelude = []
        self.function = "__module__"
        try:
            for stmt in self.deferred_module:
                try:
                    self._bind_callable_global(stmt, fences)
                except Fence as exc:
                    fences.append(exc)
                fences.extend(self.caught)
                self.caught = []
        finally:
            self.locals, self.captures, self.prelude, self.function = saved
            self._deferring = False

    def _bind_callable_global(self, stmt: ast.stmt, fences: list[Fence]) -> None:
        span = span_of(stmt, self.filename)
        self.caught = []
        value: dict[str, Any] | None = None
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            target = stmt.name
            sig = self.sigs.get(stmt.name)
            if sig is None:
                return  # the signature pass already recorded its fence
            params, ret = sig
            value = _closure_node(
                self._ir_name(stmt.name),
                params,
                ret,
                span,
            )
            try:
                for dec in reversed(stmt.decorator_list):
                    value = self._apply_decorator(dec, value, span)
            except Fence as exc:
                # boxed:begin
                # Issue #676: a decorator fence from the deferred pass used
                # to carry no function owner, so it surfaced as a top-level
                # refusal even under the fallback tier. Name the decorated
                # function: the planner drops the fence with the definition,
                # which the island seed plans for the decorator.
                if self.fallback_definitions:
                    exc.function = stmt.name
                # boxed:end
                self.caught.append(exc)
            if self.caught:
                fences.extend(self.caught)
                self.caught = []
                return
        else:
            target = stmt.target.id if isinstance(stmt, ast.AnnAssign) else stmt.targets[0].id
            try:
                if isinstance(stmt, ast.AnnAssign):
                    expect = self._annotation(stmt.annotation)
                    value = self._expr(stmt.value, expect)
                    self._expect(stmt, value["type"], expect, target)
                else:
                    value = self._expr(stmt.value)
                if _node_type_kind(value) != "func" and not self.prelude and module_needs_slot(value):
                    # A container or parser at plain-assign scope keeps its
                    # identity through the slot (#512), so it does not fall
                    # through to the constant replay below.
                    self._bind_callable_slot(target, value, span, stmt)
                    return
            except Fence as exc:
                self.caught.append(exc)
            if value is None or self.caught or _node_type_kind(value) != "func" or self.prelude:
                # Not a lowered callable (or one needing prelude temps, which
                # have no home at module scope): replay the ordinary binding
                # path so constants still bind and fences stay identical.
                self.caught = []
                self._deferring = True
                try:
                    self._bind_module_assign(stmt)
                finally:
                    self._deferring = False
                fences.extend(self.caught)
                self.caught = []
                return
        self._bind_callable_slot(target, value, span, stmt)

    def _bind_callable_slot(self, name: str, value: dict[str, Any], span: dict[str, Any], node: ast.AST) -> None:
        slot = self.global_slots.get(name)
        if slot is not None:
            typ = slot.get("type")
            if typ is not None and not types_equal(typ, value["type"]):
                self.fail(node, "PS2002", f"global {name} is already bound")
            slot["type"] = value["type"]
            if not slot.get("active"):
                self._activate_slot(name, node)
        else:
            self.global_slots[name] = {"type": value["type"], "active": False}
            self._activate_slot(name, node)
        init = self._global_set(name, value, span)
        self.global_inits.append(init["expr"])

    def _slot_is_unset(self, name: str, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        flag = self._global_is_set(name, span)
        if negated:
            return flag
        return _unary_not_node(flag, span)

    def _compare_none(
        self,
        node: ast.AST,
        value: dict[str, Any],
        negated: bool,
        span: dict[str, Any],
        *,
        none_on_left: bool,
    ) -> dict[str, Any]:
        kind = _node_type_kind(value)
        if kind == "class" and value["type"].get("optional"):
            tag = _attr(value, "_tag", {"kind": "bool"}, span)
            if negated:
                return tag
            return _unary_not_node(tag, span)
        if kind not in {"none", "int", "float", "bool", "str", "bytes"}:
            self.fail(node, "PS2004", f"{_show(value['type'])} cannot compare with None")
        none = _const_none(span)
        op = "!=" if negated else "=="
        left, right = (none, value) if none_on_left else (value, none)
        return _compare(op, left, right, span)

    def _globals_for(self, body: list[ast.stmt]) -> frozenset[str]:
        """`global`-declared names for one function body, walked once.

        `_scan_global_slots` and the per-function lowering pass see the same
        body objects, so the result is cached by body-list identity. Bodies
        are immutable after parsing, so identity is a stable key within one
        `lower_file` run; the cache lives on the `Lowerer` and dies with it.
        """
        key = id(body)
        cached = self._globals_cache.get(key)
        if cached is None:
            cached = frozenset(_declared_globals(list(body)))
            self._globals_cache[key] = cached
        return cached

    def _scan_global_slots(self, statements: list[ast.stmt]) -> dict[str, dict[str, Any]]:
        slots: dict[str, dict[str, Any]] = {}
        for stmt in statements:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            declared: set[str] = set()
            assigns: list[ast.Assign] = []
            for node in ast.walk(stmt):
                if isinstance(node, ast.Global):
                    declared.update(node.names)
                elif isinstance(node, ast.Assign):
                    assigns.append(node)
            for name in declared:
                slots.setdefault(name, {"type": None, "active": False})
            # Prime the lowering-time cache: `declared` equals
            # `_declared_globals(list(stmt.body))` because a `global` statement
            # can only appear in a body, and this walk covers the same body.
            self._globals_cache[id(stmt.body)] = frozenset(declared)
            for node in assigns:
                if len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or target.id not in declared:
                    continue
                typ = self._infer_slot_type(node.value)
                if typ is None:
                    continue
                existing = slots[target.id]["type"]
                if existing is not None and not types_equal(existing, typ):
                    slots[target.id]["conflict"] = True
                else:
                    slots[target.id]["type"] = typ
        return slots

    def _infer_slot_type(self, node: ast.expr) -> dict[str, Any] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _type_rt({"kind": "str"})
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return _type_rt({"kind": "int"})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
            return _type_rt({"kind": "str"})
        if self._is_mod(node, "importlib.resources", "as_file"):
            return _type_rt({"kind": "class", "name": "Path"})
        # Checker bridge: the checker inferred the slot's type from the
        # initializer expression; the literal rules above stay the fast path
        # and everything else consults the map when a bridge is wired.
        return self._bridge_type(node)

    def _scan_global_aliases(self, functions: list[ast.FunctionDef]) -> None:
        """Promote `global X` + `X = <imported name>` writes to module bindings.

        The lazy-loader idiom (`def _load(): global assess; from pyscriptc
        import assess as _assess; assess = _assess`) writes a module global
        from inside a function. For the subset the write is a no-op: X takes
        exactly the binding the function-local import produced, so every
        reader resolves it like a top-level import of the same name. The
        scan replays each body's import statements through the real bind
        pass on a scratch state (restored afterwards), so the alias carries
        the binding the import would really have made. A name promotes only
        when every store to it in every body is that alias shape resolving
        to the same target; anything else keeps today's fences.
        """
        records: dict[str, dict[str, Any]] = {}
        bad: set[str] = set()

        def store_names(target: ast.expr) -> list[str]:
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Tuple, ast.List)):
                return [name for elt in target.elts for name in store_names(elt)]
            if isinstance(target, ast.Starred):
                return store_names(target.value)
            return []

        saved = (
            dict(self.bindings),
            dict(self.type_aliases),
            dict(self.constants),
            set(self.declared),
            dict(self.typevars),
            dict(self.classes),
            dict(self.class_aliases),
            set(self.known_classes),
            dict(self.pkg_funcs),
            set(self.synthetics),
        )
        try:
            for fn in functions:
                declared = self._globals_for(fn.body)
                if not declared:
                    continue
                stores: dict[str, list[ast.expr | None]] = {name: [] for name in declared}
                for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
                    if isinstance(node, ast.Import):
                        try:
                            self._bind_import(node, [])
                        except Fence:
                            pass
                    elif isinstance(node, ast.ImportFrom):
                        try:
                            self._bind_from(node, [])
                        except Fence:
                            pass
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            for name in store_names(target):
                                if name in stores:
                                    stores[name].append(node.value)
                    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                        for name in store_names(node.target):
                            if name in stores:
                                stores[name].append(None)
                for name, values in stores.items():
                    if name in bad or not values:
                        continue
                    found: dict[str, Any] | None = None
                    ok = True
                    for value in values:
                        rec = self._alias_record(value)
                        if rec is None or (found is not None and rec["key"] != found["key"]):
                            ok = False
                            break
                        found = rec
                    if not ok or found is None:
                        bad.add(name)
                        records.pop(name, None)
                    else:
                        records[name] = found
        finally:
            (
                self.bindings,
                self.type_aliases,
                self.constants,
                self.declared,
                self.typevars,
                self.classes,
                self.class_aliases,
                self.known_classes,
                self.pkg_funcs,
                self.synthetics,
            ) = (
                saved[0],
                saved[1],
                saved[2],
                saved[3],
                saved[4],
                saved[5],
                saved[6],
                saved[7],
                saved[8],
                saved[9],
            )
        for name, rec in records.items():
            if name in bad:
                continue
            self.global_aliases[name] = rec
            if rec["kind"] == "binding":
                want = rec["binding"]
                self.bindings[name] = Binding(want.kind, want.module, want.attr, want.queued, want.type, want.rooted)
                if want.kind == "pyfunc" and rec.get("item") is not None:
                    self.pkg_funcs[name] = rec["item"]
                self.declared.add(name)
            elif rec["kind"] == "class":
                self.class_aliases[name] = rec["class"]
                self.known_classes.add(name)
                self.declared.add(name)
            else:
                self.constants[name] = rec["const"]
                self.declared.add(name)

    def _alias_record(self, value: ast.expr | None) -> dict[str, Any] | None:
        """The alias a `global X = <name>` store would install, else None."""
        if not isinstance(value, ast.Name):
            return None
        rhs = value.id
        found = self.bindings.get(rhs)
        if found is not None:
            return {
                "kind": "binding",
                "key": ("binding", found.kind, found.module, found.attr),
                "binding": found,
                "item": self.pkg_funcs.get(rhs),
            }
        if rhs in self.class_aliases:
            return {"kind": "class", "key": ("class", self.class_aliases[rhs]), "class": self.class_aliases[rhs]}
        if rhs in self.constants:
            return {"kind": "const", "key": ("const", id(self.constants[rhs])), "const": self.constants[rhs]}
        return None


def lower_source(
    source: str,
    filename: str = "<stdin>",
    bridge: Any = None,
    timings: dict[str, float] | None = None,
    ffi: list[Any] | None = None,
    target: str | None = None,
    dep_lane_report: dict[str, dict[str, str]] | None = None,
    resource_root: str = "",
    config: Any = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    tree = ast.parse(source, filename=filename)
    if timings is not None:
        timings["parse"] = round((time.perf_counter() - started) * 1000.0, 3)
    with dependency_lanes(dep_lane_report or {}), staged_resources(resource_root):
        if ffi:
            # The FFI table is a module-level registry the caller may have
            # installed already; only replace it when this call brings its
            # own manifests.
            with ffi_modules(ffi):
                lowerer = Lowerer(filename, source, target=target, config=config)
                lowerer.bridge = bridge
                return lowerer.module(tree)
        lowerer = Lowerer(filename, source, target=target, config=config)
        lowerer.bridge = bridge
        return lowerer.module(tree)


def lower_file(
    path: str,
    bridge: Any = None,
    timings: dict[str, float] | None = None,
    ffi: list[Any] | None = None,
    target: str | None = None,
    dep_roots: list[Path] | None = None,
    dep_lane_report: dict[str, dict[str, str]] | None = None,
    resource_root: str = "",
    config: Any = None,
) -> tuple[dict[str, Any], str]:
    """Lower one script file, with the dependency roots as extra modules.

    ``dep_roots`` (issue #398) lower into the same module the script does:
    their plain names join the shared export table, so `import certifi`
    binds the way a sibling module would. The script stays the entry module
    and the dependency modules never synthesize entries.
    """

    if config is None:
        try:
            from pyscriptc.config import load_config

            config = load_config(Path(path))
        except Exception:
            config = None

    text = open(path, encoding="utf-8").read()
    if not dep_roots:
        return lower_source(
            text, path, bridge, timings, ffi, target, dep_lane_report, resource_root, config=config
        ), text

    from pyscriptc.package import (
        _catalogued_note,
        _discover_dep_roots,
        _lower_units,
        _prune_unreached,
        _pruned_note,
    )

    script = Path(path)
    dep_units, dep_fences = _discover_dep_roots(dep_roots)
    if dep_fences:
        return (
            {
                "irVersion": IR_VERSION,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": path,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": dep_fences,
            },
            text,
        )
    kept, catalogued, pruned = _prune_unreached(
        [("__main__", script, False)], dep_units, program=True, entry="__main__"
    )
    merged, _notes = _lower_units(
        [("__main__", script, False), *kept],
        script,
        program=True,
        entry="__main__",
        bridge=bridge,
        ffi=ffi,
        target=target,
        dep_names={name for name, _p, _ip in kept},
        dep_lane_report=dep_lane_report,
        catalog_units=catalogued,
        resource_root=resource_root,
        config=config,
    )
    merged["sourceFile"] = str(script)
    notes = [
        *[_pruned_note(name, dep_path) for name, dep_path, _is_package in pruned],
        *[_catalogued_note(name, dep_path) for name, dep_path, _is_package in catalogued],
    ]
    if notes:
        merged["_notes"] = notes
    return merged, text


def render_fences(module: dict[str, Any], source: str) -> str:
    import re

    raw_fences = module.get("fences") or []
    if not raw_fences:
        return ""

    # Deduplicate fences while preserving lowering order.
    seen: set[tuple[Any, ...]] = set()
    fences: list[dict[str, Any]] = []
    for f in raw_fences:
        span = f.get("span") or {}
        key = (f.get("code"), span.get("file"), span.get("line"), span.get("col"), f.get("message"))
        if key in seen:
            continue
        seen.add(key)
        fences.append(f)

    def _source_for(f: dict[str, Any]) -> str:
        path = f.get("span", {}).get("file")
        if path and path != module.get("sourceFile"):
            try:
                return open(path, encoding="utf-8").read()
            except OSError:
                return source
        return source

    if len(fences) <= 1:
        fence = fences[0] if fences else raw_fences[0]
        return format_diagnostic(fence, _source_for(fence))

    tree_cache: dict[str | None, Any] = {}

    def _get_tree(file_path: str | None) -> Any:
        if file_path not in tree_cache:
            src = _source_for({"span": {"file": file_path}})
            try:
                tree_cache[file_path] = ast.parse(src)
            except Exception:
                tree_cache[file_path] = None
        return tree_cache[file_path]

    def _assign_targets(tree: Any, line: int) -> set[str]:
        if tree is None:
            return set()
        targets: set[str] = set()
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) == line:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        for sub in ast.walk(t):
                            if isinstance(sub, ast.Name):
                                targets.add(sub.id)
                elif isinstance(node, ast.AnnAssign):
                    for sub in ast.walk(node.target):
                        if isinstance(sub, ast.Name):
                            targets.add(sub.id)
        return targets

    def _assign_rhs_names(tree: Any, line: int) -> set[str]:
        if tree is None:
            return set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) == line:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
                    for sub in ast.walk(node.value):
                        if isinstance(sub, ast.Name):
                            names.add(sub.id)
        return names

    def _def_at(tree: Any, line: int) -> str | None:
        if tree is None:
            return None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.lineno == line:
                    return node.name
        return None

    def _stopped_names(fence: dict[str, Any]) -> set[str]:
        msg = fence.get("message", "")
        line = fence.get("span", {}).get("line", 0)
        file_path = fence.get("span", {}).get("file")
        tree = _get_tree(file_path)

        names: set[str] = set()
        m = re.search(r"^([a-zA-Z_]\w*)\s+needs an annotation before use", msg)
        if m:
            names.add(m.group(1))
        m = re.search(r"^parameter\s+([a-zA-Z_]\w*)\s+needs an annotation", msg)
        if m:
            names.add(m.group(1))
        m = re.search(r"^([a-zA-Z_]\w*)\s+needs an initial value", msg)
        if m:
            names.add(m.group(1))

        names.update(_assign_targets(tree, line))
        def_name = _def_at(tree, line)
        if def_name:
            names.add(def_name)
        return names

    from pyscriptc.coverage import family_of

    def _resolves_to(fence: dict[str, Any]) -> set[str]:
        msg = fence.get("message", "")
        code = fence.get("code", "")
        fam = family_of(code, msg)

        res: set[str] = set()
        m = re.search(r"^([a-zA-Z_]\w*)\s+is not a local or parameter", msg)
        if m:
            res.add(m.group(1))
        m = re.search(r"^call to ([a-zA-Z_]\w*)\s+is not lowered", msg)
        if m:
            res.add(m.group(1))
        m = re.search(r"^this class ([a-zA-Z_]\w*)\s+is not lowered", msg)
        if m:
            res.add(m.group(1))
        m = re.search(r"^([a-zA-Z_]\w*)\s+is not exported", msg)
        if m:
            res.add(m.group(1))
        m = re.search(r"cannot re-export unlowered ([a-zA-Z_]\w*)", msg)
        if m:
            res.add(m.group(1))

        if fam in ("export-cascade", "class-cascade"):
            words = set(re.findall(r"\b[a-zA-Z_]\w*\b", msg))
            res.update(words)

        line = fence.get("span", {}).get("line", 0)
        file_path = fence.get("span", {}).get("file")
        tree = _get_tree(file_path)
        res.update(_assign_rhs_names(tree, line))
        return res

    root_fences: list[list[Any]] = []

    for fence in fences:
        target_names = _resolves_to(fence)
        matched_root = None

        if target_names:
            for item in root_fences:
                _rf, _cons, s_names = item
                if target_names & s_names:
                    matched_root = item
                    break

        if matched_root is not None:
            matched_root[1].append(fence)
            line = fence.get("span", {}).get("line", 0)
            file_path = fence.get("span", {}).get("file")
            tree = _get_tree(file_path)
            matched_root[2].update(_assign_targets(tree, line))
            matched_root[2].update(_stopped_names(fence))
        else:
            s_names = _stopped_names(fence)
            root_fences.append([fence, [], s_names])

    parts: list[str] = []
    printed_roots = root_fences[:3]
    for r_fence, consequences, _ in printed_roots:
        text = _source_for(r_fence)
        parts.append(format_diagnostic(r_fence, text))
        count = len(consequences)
        if count == 1:
            parts.append("(and 1 more fence that follows from it)")
        elif count > 1:
            parts.append(f"(and {count} more fences that follow from it)")

    if len(root_fences) > 3:
        rem = sum(1 + len(cons) for _, cons, _ in root_fences[3:])
        if rem == 1:
            parts.append("(and 1 more fence)")
        elif rem > 1:
            parts.append(f"(and {rem} more fences)")

    return "\n".join(parts)


# Issue #587: the deepest AST nesting the recursive lowering passes handle.
# Measured against the interpreter's own recursion limit: a chain around 250
# levels already grazes it, so the fence fires well before the crash while
# leaving every realistic program native. CPython 3.14's parser only bounds
# parenthesis nesting, not attribute or operator chains, so a source that
# parses can still be too deep to lower.
MAX_LOWER_DEPTH = 200


def _ast_depth(tree: ast.AST) -> int:
    """The maximum AST depth (edges from the root), iteratively.

    Iterative on purpose: `ast.walk`-style recursion on a pathological tree
    is exactly what this guard exists to fence.
    """
    deepest = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > deepest:
            deepest = depth
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return deepest


def _deepest_node(tree: ast.AST) -> ast.AST:
    """The first node at the maximum depth: the fence span points there."""
    best: ast.AST = tree
    best_depth = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > best_depth:
            best, best_depth = node, depth
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return best


def _const_str_join(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level f-string pieces that are already str constants are one str constant."""

    if any(_maybe_node_kind(part) != "const" or _maybe_type_kind(part.get("type", {})) != "str" for part in parts):
        return None
    text = "".join(str(part["value"]) for part in parts)
    return _const_str(text, span)


def _concat_str(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    if not parts:
        return _const_str("", span)
    acc = parts[0]
    for part in parts[1:]:
        acc = node_to_dict(_ir.binary("+", dict_to_node(acc), dict_to_node(part), _ir.t_leaf("str"), _mk_span(span)))
    return acc


def _const_list_concat(left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """Two constant lists of one element type are one list. A runtime list stays fenced."""

    if _maybe_node_kind(left) != "list" or _maybe_node_kind(right) != "list":
        return None
    if _type_kind(left["type"]) != "list" or _type_kind(right["type"]) != "list":
        return None
    if left["type"].get("set") or right["type"].get("set"):
        return None
    if not types_equal(left["type"]["elem"], right["type"]["elem"]):
        return None
    return node_to_dict(
        _ir.list_lit(
            [dict_to_node(elem) for elem in [*left["elems"], *right["elems"]]],
            _ir.t_list(dict_to_type(left["type"]["elem"])),
            _mk_span(span),
        )
    )


def _static_int(expr: dict[str, Any]) -> int | None:
    if _maybe_node_kind(expr) == "const" and _maybe_type_kind(expr.get("type", {})) == "int":
        value = expr.get("value")
        if isinstance(value, str) and _int_text(value):
            return int(value)
    if _maybe_node_kind(expr) == "unary" and expr.get("op") == "-" and isinstance(expr.get("operand"), dict):
        inner = _static_int(expr["operand"])
        if inner is not None:
            return -inner
    return None


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


def _text_ir(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    ptag = {"str": _ir.P_STR, "int": _ir.P_INT, "float": _ir.P_FLOAT, "bytes": _ir.P_BYTES}[kind]
    return node_to_dict(_ir.const_node(ptag, value, _ir.t_leaf(kind), _mk_span(span)))


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None
