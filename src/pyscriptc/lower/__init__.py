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
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

from pyscriptc.builtins import USER_EXC_BASE
from pyscriptc.coro import CoroMixin
from pyscriptc.errors import format_diagnostic, span_of
from pyscriptc.ffi_manifest import is_handle_type
from pyscriptc.ir import IR_VERSION, SUBSET, cpython_version, types_equal
from pyscriptc.lower._calls import CallsMixin
from pyscriptc.lower._classes import ClassesMixin
from pyscriptc.lower._closures import ClosuresMixin
from pyscriptc.lower._core import (
    _CMP,
    _TRUTHY,
    Binding,
    Fence,
    _attr,
    _compare,
    _const_none,
    _declared_globals,
    _dotted,
    _has_typevar,
    _ir_kinds_of,
    _is_none_expr,
    _name,
    _peek_annotated_params,
    _peek_annotation,
    _show,
    _subst_type,
    _tuple_assign,
    module_needs_slot,
)
from pyscriptc.lower._expr import ExprMixin
from pyscriptc.lower._stdlib import StdlibDispatchMixin
from pyscriptc.lower._stdlib_shared import (
    _ERASED_MODULES,
    _module_assigned,
    _release_erased_import,
)
from pyscriptc.lower._stmt import (
    StmtMixin,
    _always_returns,
    _is_docstring,
    _is_main_guard,
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


class Lowerer(CoroMixin, StdlibDispatchMixin, ExprMixin, StmtMixin, CallsMixin, ClassesMixin, ClosuresMixin):
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
        # The function body being lowered, so `del name` can see a later load.
        self.fn_body: list[ast.stmt] = []
        # Names bound to frozenset(...). The value is still the set list. .add and
        # .remove on these names stay fenced. A plain set name is not in this set.
        self.frozen_names: set[str] = set()
        # `if x is not None:` narrowing on a tagged optional: inside the taken
        # branch a read of the name lowers to its `_val` field. name -> opt typ.
        self.opt_unwrap: dict[str, dict[str, Any]] = {}
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

    def _bridge_type(self, node: ast.expr) -> dict[str, Any] | None:
        """The IR type the checker inferred for an expression, or None.

        The bridge answers only when it proved a type the lowered subset can
        express; anything else returns None and the caller keeps its fence.
        """
        if self.bridge is None:
            return None
        inferred = self.bridge.inferred_type(node, self.classes)
        if inferred is None:
            return None
        return inferred

    def _bridge_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Any:
        """DefFacts the checker holds for one `def`, or None without a bridge."""
        if self.bridge is None:
            return None
        return self.bridge.def_facts(node)

    def _bridge_param_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, arg: ast.arg) -> dict[str, Any] | None:
        """The IR type the checker proved for an unannotated parameter, or None."""
        if self.bridge is None:
            return None
        return self.bridge.param_type(fn, arg.arg, self.classes)

    def _bridge_return_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The IR type the checker inferred for a missing return annotation."""
        if self.bridge is None:
            return None
        return self.bridge.return_type(fn, self.classes)

    _PEEK_UNSAFE = {"abi_call", "free", "consume", "yield_expr", "await_expr"}

    def _infer_type_of(self, node: ast.expr) -> dict[str, Any] | None:
        """The type lowering would give `node`, or None when it does not lower.

        The peek (issue #500) lowers the expression speculatively: the IR
        goes to a throwaway prelude and the temp counter is restored, so a
        non-trivial value lowers twice. Comprehension scaffolding is nested
        and restored like any other state; the read-only expressions return
        identical types on the real pass, which then emits the effects.
        Calls whose results are lowered outside the IR (abi thunks, handle
        frees and consumes) are refused: the peek must not run the runtime
        twice. A fence inside the peek is the refusal, not a diagnostic:
        inference never guesses, and the caller keeps its own message.
        """

        class _Peek:
            def __enter__(s) -> bool:
                if self.inf_peek or self.function is None:
                    return False
                s.saved_prelude = self.prelude
                s.saved_temp_n = self.temp_n
                s.saved_fences = self.caught
                s.saved_comp = (
                    self.comp_elt,
                    self.comp_key,
                    self.comp_kind,
                    self.comp_gens,
                    self.comp_ifs,
                    self.comp_dest,
                    self.comp_init,
                    self.comp_expect,
                    self.comp_ready,
                )
                s.saved_dead = self.dead_names
                s.saved_exc = self.exc_stack
                s.saved_broke = self.broke_flag
                s.saved_break = self.break_flag
                s.saved_cont = self.continue_flag
                self.prelude = []
                self.caught = []
                self.inf_peek = True
                self.inf_depth += 1
                return True

            def __exit__(s, *exc) -> None:
                if not hasattr(s, "saved_prelude"):
                    return
                self.inf_depth -= 1
                if self.inf_depth == 0:
                    self.inf_peek = False
                self.prelude = s.saved_prelude
                self.temp_n = s.saved_temp_n
                self.caught = s.saved_fences
                (
                    self.comp_elt,
                    self.comp_key,
                    self.comp_kind,
                    self.comp_gens,
                    self.comp_ifs,
                    self.comp_dest,
                    self.comp_init,
                    self.comp_expect,
                    self.comp_ready,
                ) = s.saved_comp
                self.dead_names = s.saved_dead
                self.exc_stack = s.saved_exc
                self.broke_flag = s.saved_broke
                self.break_flag = s.saved_break
                self.continue_flag = s.saved_cont

        if any(k in self._PEEK_UNSAFE for k in _ir_kinds_of(node)):
            return None
        with _Peek() as ok:
            if not ok:
                return None
            try:
                value = self._expr(node)
            except Fence:
                return None
            if self.caught:
                return None
            return value["type"]

    def _infer_type_known(self, node: ast.expr) -> dict[str, Any] | None:
        """The type of a call to a `def` this module already signed, or None.

        The peek cannot lower a user call speculatively (the callee's body
        may not have lowered yet), but the signature pass has already
        proven the return types of every def that signed. A direct call
        whose callee is one of those names returns the signature's return
        type without lowering anything: the real pass emits the call.
        """

        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return None
        sig = self.sigs.get(node.func.id)
        if sig is None or node.func.id in self.generic_fns:
            return None
        params, ret = sig
        if ret["kind"] == "none" or _has_typevar(ret):
            return None
        if any(k in self._PEEK_UNSAFE for k in _ir_kinds_of(node)):
            return None
        return ret

    def _tuple_fields_of(self, typ: dict[str, Any] | None) -> list[dict[str, Any]] | None:
        """The field types of a synthetic tuple class type, or None.

        `_tuple_type` spells the fields into the class name
        (`tuple_3_str_str_str`); this decodes the slugs back to the IR
        types. A type that is not a synthetic tuple, or a slug the subset
        cannot express, returns None so the caller refuses rather than
        approximate.
        """

        if not isinstance(typ, dict) or typ.get("kind") != "class":
            return None
        info = self.classes.get(typ["name"])
        if info is None or not info.get("tuple") or not info.get("synthetic"):
            return None
        return [field["type"] for field in info["fields"]]

    def _infer_signature(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The return type the function's own body proves, or None.

        The peek (issue #500) scans the body's `return` statements, lowers
        each returned expression under the peek, and keeps the type when
        every return agrees, at least one return exists, and every path
        returns. Only what the body proves is acceptable: a missing
        parameter annotation, a call that does not lower or a type the
        subset cannot express means the peek returns None and the caller
        keeps its fence. Defaults are irrelevant here on purpose: a typed
        default is a value at the call site, never the body's parameter
        type, so it does not flow into the inferred return. The PS1004
        fall-off-the-end fence is replayed by the real pass, which signs
        and lowers the body for real.
        """
        if fn.returns is not None or self.function is None:
            return None
        if self.inf_peek:
            # A signature being peeked proves no further def's return: the
            # nested peek would replay the same fences and recurse.
            return None
        if isinstance(fn, ast.AsyncFunctionDef):
            return None
        for node in ast.walk(fn):
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                return None
        # The body's own local assignments participate: a returned value
        # may name a local (`return mapping.get(k)`), and the local's type
        # is the same body-proven fact this peek derives. The scan is the
        # same top-level shape the real body lowers; a value that does not
        # lower leaves the name out.
        saved_locals = self.locals
        body_locals = dict(_peek_annotated_params(self, fn))
        # The annotated parameters are visible to the scan immediately: a
        # value expression may name one (`[0.0] * n` under `n: int`), and
        # the fact is the def's own annotation, not a guess.
        self.locals = {**saved_locals, **body_locals}
        saved_fn_body = self.fn_body
        self.fn_body = list(fn.body)
        try:
            for item in fn.body:
                if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                    name = item.targets[0].id
                    if name in body_locals:
                        continue
                    # An empty display says nothing on its own; the same
                    # accumulated-type pass the real assignment uses reads
                    # the appends and item writes in this body. The fn_body
                    # window above is what that pass scans.
                    typ = self._infer_accumulated_type(item) or self._infer_type_of(item.value)
                    if typ is not None and typ["kind"] != "none":
                        body_locals[name] = typ
                        self.locals[name] = typ
                elif (
                    isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Tuple)
                    and all(isinstance(elt, ast.Name) for elt in item.targets[0].elts)
                ):
                    # `stem, dot, ext = value.rpartition(".")`: a fixed-arity
                    # tuple unpack of a call the peek can prove. The synthetic
                    # tuple class name carries the field kinds
                    # (`tuple_3_str_str_str`), and each name takes its field
                    # type; a type the name does not decode leaves the whole
                    # unpack out (never a guess).
                    typ = self._infer_type_of(item.value)
                    fields = self._tuple_fields_of(typ)
                    if fields is not None and len(fields) == len(item.targets[0].elts):
                        for elt, field in zip(item.targets[0].elts, fields, strict=True):
                            body_locals[elt.id] = field
                            self.locals[elt.id] = field
        finally:
            self.fn_body = saved_fn_body
        self.locals = {**saved_locals, **body_locals}
        returns: list[dict[str, Any]] = []
        # The return scan lowers returned values through `_infer_type_of`,
        # whose own peek guard refuses a nested peek. The scan window sets
        # no flag here: the peek opens per returned expression.
        try:
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.Return):
                    returns.append(stmt)
            if any(item.value is None for item in returns):
                # A bare `return`: the body proves nothing beyond None, but
                # mixing bare and valued returns means the paths disagree.
                # Fall back to the zero-return rule below only when none
                # carries a value.
                if not all(item.value is None for item in returns):
                    return None
            if not returns or all(item.value is None for item in returns):
                # No return at all, or only bare ones: every path falls off
                # the end, so the body proves `None` (CPython semantics,
                # not a guess). A generator is refused above by the yield
                # scan. The PS1004 fence does not apply: nothing can fall
                # off because nothing expects a value.
                return {"kind": "none"}
            inferred = None
            for item in returns:
                assert item.value is not None
                typ = self._infer_type_of(item.value)
                if typ is None or typ["kind"] == "none":
                    return None
                if inferred is None:
                    inferred = typ
                elif not types_equal(inferred, typ):
                    return None
        finally:
            self.locals = saved_locals
        assert inferred is not None
        if _has_typevar(inferred):
            return None
        return inferred

    def _bindings_types(self) -> dict[str, dict[str, Any]]:
        """The import-bound modules as peekable types.

        The peek lowers module attribute calls (`os.path.splitext(x)`) and
        needs the module names in `locals` as opaque records the attribute
        path resolves. The binding table already proved each import lowers
        (a bad import fenced in `_live_statements`), so this trusts it.
        """

        out: dict[str, dict[str, Any]] = {}
        for name, binding in self.bindings.items():
            if binding.kind == "module":
                out[name] = {"kind": "module", "name": binding.module}
        return out

    def _pin_call_site_params(
        self, functions: list[ast.FunctionDef | ast.AsyncFunctionDef], fences: list[Fence]
    ) -> None:
        """Pin unannotated parameters from their call sites (issue #500).

        Runs after the first signature pass, before any body lowers. A
        parameter with no annotation and no default, whose every call site
        passes a value of one proven type, gets that type: the signature
        pass re-runs for the defs that changed, so their return inference
        sees the pinned parameter. Soundness: every call site must agree,
        a parameter any call passes an unprovable value to stays fenced,
        and nothing here reads a def whose signature already exists with
        all parameters annotated. Recursion through the pinned def itself
        is skipped (the call inside its own body is not a pinning site).
        """

        by_name = {fn.name: fn for fn in functions if isinstance(fn, ast.FunctionDef)}
        pending = [
            fn
            for fn in by_name.values()
            if not isinstance(fn, ast.AsyncFunctionDef)
            and any(
                arg.annotation is None and not self._has_default(fn, arg.arg)
                for arg in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            )
        ]
        if not pending:
            return
        # The first signature pass fenced the unannotated parameters this
        # pass is about to prove. A def the pin rescues has its stale
        # first-pass fence dropped: the rescued signature is the answer and
        # the earlier PS1001 was a state this pass replaces.
        for _round in range(3):
            changed = False
            for fn in pending:
                if fn.name in self.sigs:
                    continue
                unannotated = [
                    arg
                    for arg in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
                    if arg.annotation is None and not self._has_default(fn, arg.arg)
                ]
                if not unannotated:
                    continue
                pins = self._call_site_types(fn, unannotated, by_name)
                if not pins:
                    continue
                for arg, typ in pins.items():
                    arg.annotation = self._pin_annotation(typ)
                saved_prelude = self.prelude
                saved_caught = self.caught
                saved_temp_n = self.temp_n
                saved_function = self.function
                self.prelude = []
                self.caught = []
                self.temp_n = saved_temp_n
                self.function = fn.name
                changed = True
                try:
                    signed = self._signature(fn, None, "method")
                    if signed is None or self.caught:
                        continue
                except Fence:
                    continue
                finally:
                    self.prelude = saved_prelude
                    self.caught = saved_caught
                    self.temp_n = saved_temp_n
                    self.function = saved_function
                self.sigs[fn.name] = signed
                pinned_names = {arg.arg for arg in pins}
                fences[:] = [
                    item
                    for item in fences
                    if not (
                        item.code == "PS1001"
                        and item.function == fn.name
                        and item.message in {f"parameter {name} needs an annotation" for name in pinned_names}
                    )
                ]
            if not changed:
                break

    def _has_default(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
        """True when the named positional or keyword-only parameter has a default."""

        plain = fn.args.posonlyargs + fn.args.args
        missing = len(plain) - len(fn.args.defaults)
        for index, arg in enumerate(plain):
            if arg.arg == name:
                return index >= missing
        for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
            if arg.arg == name:
                return default is not None
        return False

    def _call_site_types(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        unannotated: list[ast.arg],
        by_name: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    ) -> dict[ast.arg, dict[str, Any]]:
        """The one type every call site proves for each unannotated parameter.

        A call site inside any function body (not the pinned def's own, to
        skip recursion) contributes the argument's peeked type. The first
        disagreement, or a call whose argument does not lower, refuses the
        parameter: no pin.
        """

        wanted = {arg.arg: arg for arg in unannotated}
        pins: dict[str, dict[str, Any]] = {}

        def _call_site_locals(caller: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            """Bind the caller body's own local names for the argument peeks.

            `get_category(ext)` passes a local the caller bound earlier; the
            pin pass runs before any body lowers, so the peek needs those
            names. The walk is recursive in source order (`if`/`for`/`while`
            bodies hold the binding the call site reads, like
            `file_names = sys.argv[1:]` under an `if len(sys.argv)`), and a
            name bound twice with different types leaves the table: the
            ambiguity refuses the pin rather than guessing. Loop targets
            resolve from their iterable when its type is known.
            """

            self.locals = self._bindings_types()

            def _bind(name: str, typ: dict[str, Any] | None) -> None:
                if typ is None or typ["kind"] == "none":
                    return
                known = self.locals.get(name)
                if known is not None and not types_equal(known, typ):
                    self.locals.pop(name, None)
                    return
                self.locals[name] = typ

            def _visit(body: list[ast.stmt]) -> None:
                for item in body:
                    if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                        if item.targets[0].id in self.bindings:
                            continue
                        _bind(item.targets[0].id, self._infer_type_of(item.value))
                    elif (
                        isinstance(item, ast.Assign)
                        and len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Tuple)
                        and all(isinstance(elt, ast.Name) for elt in item.targets[0].elts)
                    ):
                        # `_, ext = os.path.splitext(name)`: a fixed-arity
                        # tuple decodes like the return peek's unpack, each
                        # name taking its field's type. A list RHS decodes
                        # as its element type for every name: the real
                        # unpack length-checks at run time and a length
                        # mismatch is CPython's ValueError, so the binding
                        # is exactly as proven as the unpack itself.
                        value_type = self._infer_type_of(item.value)
                        fields = self._tuple_fields_of(value_type)
                        if (
                            fields is None
                            and isinstance(value_type, dict)
                            and value_type.get("kind") == "list"
                            and not value_type.get("set")
                        ):
                            fields = [value_type["elem"]] * len(item.targets[0].elts)
                        if fields is not None and len(fields) == len(item.targets[0].elts):
                            for elt, field in zip(item.targets[0].elts, fields, strict=True):
                                _bind(elt.id, field)
                    if isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Name):
                        left = self.locals.get(item.target.id)
                        if left is not None:
                            binop = ast.BinOp(
                                left=ast.Name(id=item.target.id, ctx=ast.Load()), op=item.op, right=item.value
                            )
                            ast.copy_location(binop, item)
                            _bind(item.target.id, self._infer_type_of(binop))
                    if isinstance(item, ast.For) and isinstance(item.target, ast.Name):
                        iter_typ = self._infer_type_of(item.iter)
                        if iter_typ is not None and iter_typ.get("kind") in {"list", "dict"}:
                            elem = iter_typ["elem"] if iter_typ["kind"] == "list" else iter_typ["key"]
                            _bind(item.target.id, elem)
                    if isinstance(item, ast.If):
                        _visit(item.body)
                        _visit(item.orelse)
                    if isinstance(item, (ast.For, ast.While)):
                        if isinstance(item, ast.For) and isinstance(item.target, ast.Name):
                            iter_typ = self._infer_type_of(item.iter)
                            if iter_typ is not None and iter_typ.get("kind") in {"list", "dict"}:
                                elem = iter_typ["elem"] if iter_typ["kind"] == "list" else iter_typ["key"]
                                _bind(item.target.id, elem)
                        _visit(item.body)
                        _visit(item.orelse)
                    if isinstance(item, ast.With):
                        # `with open(p) as f` binds the handle local like the
                        # real lowering does; a general context manager is
                        # refused by its own expression peek.
                        named = [
                            it.optional_vars
                            for it in item.items
                            if it.optional_vars is not None and isinstance(it.optional_vars, ast.Name)
                        ]
                        for it in item.items:
                            if isinstance(it.optional_vars, ast.Name):
                                opened = self._infer_type_of(it.context_expr)
                                if opened is not None and opened.get("kind") == "class":
                                    _bind(it.optional_vars.id, opened)
                        if len(item.items) == 1 or named:
                            _visit(item.body)
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue

            # Annotated parameters of the caller are facts the def states,
            # not guesses: an argument the call site passes as a bare
            # parameter (`format_array("t", [n])`) peeks through them. The
            # pins from the caller's own rescue ride along the same way.
            # They bind before the walk: a local's own value may read a
            # parameter (`f = open(fname)`), and the peek needs it then.
            for arg in caller.args.posonlyargs + caller.args.args + caller.args.kwonlyargs:
                if arg.annotation is None:
                    continue
                typ = _peek_annotation(arg.annotation)
                if typ.get("kind") not in {None, "__peek_unresolved__"} and arg.arg not in self.bindings:
                    self.locals[arg.arg] = typ
            _visit(caller.body)

        saved_pin_locals = self.locals
        for caller in by_name.values():
            _call_site_locals(caller)
            for node in ast.walk(caller):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id != fn.name:
                    continue
                if caller is fn:
                    continue
                for index, arg_node in enumerate(node.args):
                    plain = fn.args.posonlyargs + fn.args.args
                    if index >= len(plain) or plain[index].arg not in wanted:
                        continue
                    name = plain[index].arg
                    typ = self._infer_type_of(arg_node)
                    if typ is None or typ["kind"] == "none":
                        pins.pop(name, None)
                        wanted.pop(name, None)
                        continue
                    if name in pins and not types_equal(pins[name], typ):
                        pins.pop(name, None)
                        wanted.pop(name, None)
                        continue
                    if name in wanted:
                        pins[name] = typ
                for kw in node.keywords:
                    if kw.arg is None or kw.arg not in wanted:
                        continue
                    typ = self._infer_type_of(kw.value)
                    if typ is None or typ["kind"] == "none":
                        pins.pop(kw.arg, None)
                        wanted.pop(kw.arg, None)
                        continue
                    if kw.arg in pins and not types_equal(pins[kw.arg], typ):
                        pins.pop(kw.arg, None)
                        wanted.pop(kw.arg, None)
                        continue
                    if kw.arg in wanted:
                        pins[kw.arg] = typ
        self.locals = saved_pin_locals
        out: dict[ast.arg, dict[str, Any]] = {}
        for name, typ in pins.items():
            if name in wanted:
                out[wanted[name]] = typ
        return out

    def _pin_annotation(self, typ: dict[str, Any]) -> ast.AST:
        """The annotation spelling for a pinned parameter type, or a poison one.

        `_annotation` re-reads the annotation when the signature pass
        re-runs, so the spelling must round-trip: scalars spell as
        themselves, lists and dicts as generics, a class as its name. A
        type with no spelling here means the pin is refused (the caller
        never sees it: `_call_site_types` only produces the subset this
        helper reads).
        """

        kind = typ.get("kind")
        if kind in {"int", "float", "bool", "str", "bytes"}:
            return ast.Name(id=kind, ctx=ast.Load())
        if kind == "list" and typ.get("set"):
            return ast.Subscript(
                value=ast.Name(id="set", ctx=ast.Load()), slice=ast.Name(id="int", ctx=ast.Load()), ctx=ast.Load()
            )
        if kind == "list":
            elem = self._pin_annotation(typ["elem"])
            return ast.Subscript(value=ast.Name(id="list", ctx=ast.Load()), slice=elem, ctx=ast.Load())
        if kind == "dict":
            key = self._pin_annotation(typ["key"])
            value = self._pin_annotation(typ["value"])
            return ast.Subscript(
                value=ast.Name(id="dict", ctx=ast.Load()),
                slice=ast.Tuple(elts=[key, value], ctx=ast.Load()),
                ctx=ast.Load(),
            )
        if kind == "class":
            return ast.Name(id=typ["name"], ctx=ast.Load())
        return ast.Name(id="__pin_unsupported__", ctx=ast.Load())

    def _infer_fence(self, node: ast.AST, target: str) -> NoReturn:
        """The refusal fence, byte-identical to the pre-inference message.

        The rule and the hint stay where the reader looks them up: the
        `param-inference` / `local-inference` / `return-inference` families
        carry the refactor hint, and `pyscriptc explain PS1001` the rule.
        Ambiguity (a bare `None`, a lambda, an empty display, a call that
        does not lower) is a fence, never a guess.
        """
        self.fail(node, "PS1001", f"{target} needs an annotation before use")

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

    def module(self, tree: ast.Module) -> dict[str, Any]:
        tree = self._desugar(tree)
        fences: list[Fence] = []
        functions: list[ast.FunctionDef] = []
        class_nodes: list[ast.ClassDef] = []
        live = self._live_statements(list(tree.body), fences)
        self.global_slots = self._scan_global_slots(live)
        entry_stmts = self._split_entry(live, functions, class_nodes, fences)
        entry_fn = self._entry_function(entry_stmts, functions)
        entry_name = entry_fn[0].name if entry_fn else None
        self.entry_fn_name = entry_name
        functions.extend(entry_fn)
        self._install_synthetics(class_nodes, fences)
        imported = {name for name, info in self.classes.items() if info.get("imported")}
        imported.update(self.class_aliases)
        self.known_classes = {node.name for node in class_nodes}
        self.known_classes.update(name for name, info in self.classes.items() if info.get("synthetic"))
        self.known_classes.update(imported)
        for node in class_nodes:
            self.function = node.name
            self.caught = []
            try:
                self._collect_class(node)
            except Fence as exc:
                fences.append(exc)
            fences.extend(self.caught)
        for fn in functions:
            self.function = fn.name
            if fn.name in self.declared:
                fences.append(Fence("PS2002", f"{fn.name} is already bound", span_of(fn, self.filename), fn.name))
                continue
            try:
                kind = "static" if self._is_module_staticmethod(fn) else "method"
                self.sigs[fn.name] = self._coro_signature(fn) or self._signature(fn, None, kind)
            except Fence as exc:
                fences.append(exc)
            else:
                params, ret = self.sigs[fn.name]
                if _has_typevar(ret) or any(_has_typevar(param["type"]) for param in params):
                    self.generic_fns[fn.name] = fn
        self._pin_call_site_params(functions, fences)
        if self.deferred_module:
            self._bind_deferred_module(fences)
        if self.cataloguing:
            self.function = None
            return {
                "irVersion": IR_VERSION,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": self.filename,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": [fence.as_dict() for fence in fences],
            }
        lowered_classes: list[dict[str, Any]] = []
        for node in class_nodes:
            info = self.classes.get(node.name)
            if info is None or info.get("synthetic") or info.get("typevars"):
                continue
            self.function = node.name
            self.caught = []
            try:
                lowered = self._lower_class(node)
            except Fence as exc:
                fences.append(exc)
                fences.extend(self.caught)
                continue
            fences.extend(self.caught)
            if lowered is not None and not self.caught:
                lowered_classes.append(lowered)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        lowered: list[dict[str, Any]] = []
        for fn in functions:
            if fn.name not in self.sigs or fn.name in self.generic_fns:
                continue
            self.function = fn.name
            self.temp_n = 0
            self.prelude = []
            # boxed:begin
            self.island_modules = {}
            # boxed:end
            self.caught = []
            self.fn_globals = self._globals_for(fn.body)
            self.fn_nonlocals = set()
            self.captures = {}
            params, ret = self._coro_body_sig(fn.name)
            self.locals = {param["name"]: param["type"] for param in params}
            self.frozen_names = set()
            self.opt_unwrap = {}
            self.boxed_names = set()
            self.fn_body = list(fn.body)
            # Manifest handle ownership is per-function scope: a fresh
            # function owns nothing (issue #224). Its handle parameters are
            # borrows: the caller keeps ownership.
            self.handle_locals = {}
            self.consumed_handles = set()
            self.returned_handles = set()
            self.borrowed_handles = {
                name for name, typ in self.locals.items() if isinstance(typ, dict) and is_handle_type(typ)
            }
            # Function-local imports rebind module names: snapshot the
            # compile-time binding tables so they restore when the body ends.
            saved_bindings = dict(self.bindings)
            saved_aliases = dict(self.type_aliases)
            saved_declared = set(self.declared)
            saved_typevars = dict(self.typevars)
            try:
                body = self._scope_exit_frees(self._body(list(fn.body)), span_of(fn, self.filename))
                if self.caught:
                    fences.extend(self.caught)
                    # The entry is not a user function: its fences are already
                    # in the module list and block emission, so the partial
                    # body still carries the flip signal in the IR.
                    if entry_name is None or fn.name != entry_name:
                        continue
                if ret["kind"] != "none" and not _always_returns(body):
                    self.fail(fn, "PS1004", f"{fn.name} can fall off the end and return None")
                record = {
                    "name": self._ir_name(fn.name),
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
        self._drain_instances(lowered, lowered_classes, fences)
        lowered.extend(self.nested_fns)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        # The deferred classes (the argparse namespace) grew their fields
        # while the statements lowered, so they close here.
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}, deferred_only=True))
        self.function = None
        names = [item["name"] for item in lowered]
        module = {
            "irVersion": IR_VERSION,
            "subset": SUBSET,
            "cpython": cpython_version(),
            "sourceFile": self.filename,
            # A definition-only module keeps the old shape: entry is the first
            # function. Only a synthesized entry overrides that.
            "entry": entry_name if entry_name in names else lowered[0]["name"] if lowered else None,
            "functions": lowered,
            "classes": lowered_classes,
            "fences": [fence.as_dict() for fence in fences],
        }
        if self.global_inits:
            module["globals"] = self.global_inits
        return self._coro_finish(module)

    def _split_entry(
        self,
        live: list[ast.stmt],
        functions: list[ast.FunctionDef],
        class_nodes: list[ast.ClassDef],
        fences: list[Fence],
    ) -> list[ast.stmt]:
        """Walk the live statements: definitions stay where they are, script
        statements collect into the entry body, in source order.

        A module assignment tries the constant binding first. When the module
        also carries live script statements the failed binding rolls back and
        the assignment lowers inside the entry instead; a definition-only
        module keeps the constant fence it always raised. `pass` is inert
        (C3) and never makes a script.
        """
        entry_stmts: list[ast.stmt] = []
        failed_assigns: list[tuple[ast.stmt, int]] = []
        bare_decls: list[ast.AnnAssign] = []
        definitions = (ast.Import, ast.ImportFrom, ast.Pass, ast.FunctionDef, ast.ClassDef, ast.AnnAssign, ast.Assign)
        self._script_line = min(
            (stmt.lineno for stmt in live if not isinstance(stmt, definitions) and not _is_docstring(stmt)),
            default=None,
        )
        scriptish = self._script_line is not None
        divert = self._diverted_literals(live) if scriptish else set()
        for stmt in live:
            if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not self._accept_module_function(stmt, fences):
                    continue
                functions.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                class_nodes.append(stmt)
            elif isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id in divert for t in targets):
                    entry_stmts.append(stmt)
                    continue
                snapshot = (dict(self.constants), set(self.declared), len(fences))
                self.caught = []
                self._bind_module_assign(stmt)
                # A failed binding rolls back at once, so later assignments
                # bind against the pre-statement state. Its fences stay for
                # now; when the module turns out to be a script they are
                # retracted and the statement lowers inside the entry.
                if self.caught:
                    self.constants = snapshot[0]
                    self.declared = snapshot[1]
                    fences.extend(self.caught)
                    failed_assigns.append((stmt, snapshot[2]))
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is None:
                    # Bound nothing, but a script may still need the
                    # declaration: the name's real assignment lowers inside
                    # the entry and reads `locals` for its annotation.
                    bare_decls.append(stmt)
                self.caught = []
            elif _is_docstring(stmt):
                continue
            else:
                entry_stmts.append(stmt)
        if entry_stmts:
            # This module is a script: retract the fences the failed constant
            # bindings raised; those assignments lower inside the entry
            # instead, in source order. Bindings that did not fence stay
            # bound - a script may still carry true constants.
            retract = min((at for _, at in failed_assigns), default=len(fences))
            if retract < len(fences):
                del fences[retract:]
            entry_stmts.extend(stmt for stmt, _ in failed_assigns)
            # A bare declaration follows its name into the entry unless already bound.
            for stmt in bare_decls:
                if isinstance(stmt.target, ast.Name) and stmt.target.id not in self.declared:
                    entry_stmts.append(stmt)
            entry_stmts.sort(key=lambda stmt: stmt.lineno)
        return entry_stmts

    def _diverted_literals(self, live: list[ast.stmt]) -> set[str]:
        """Names bound to container literals whose entry mutates them by subscript.

        Module constants re-emit their literal on every read, so a script's
        `counts[k] = v` would write into a throwaway dict; the binding lowers
        inside the entry instead unless the name hides inside a def/class.
        """
        bound: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(stmt.value, (ast.Dict, ast.List, ast.Set)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                bound.update(t.id for t in targets if isinstance(t, ast.Name))
        if not bound:
            return set()
        hidden: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hidden.update(n.id for n in ast.walk(stmt) if isinstance(n, ast.Name) and n.id in bound)
        mutated: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in ast.walk(stmt):
                if (
                    isinstance(n, ast.Subscript)
                    and isinstance(n.value, ast.Name)
                    and n.value.id in bound
                    and isinstance(n.ctx, (ast.Store, ast.Del))
                ):
                    mutated.add(n.value.id)
        return mutated - hidden

    def _entry_function(self, stmts: list[ast.stmt], functions: list[ast.FunctionDef]) -> list[ast.FunctionDef]:
        """The synthesized entry for module-level statements.

        A definition-only module (imports, constants, functions, classes,
        docstrings) synthesizes nothing: the IR is exactly what it was before
        entry lowering existed. Scripts get one function named `main`, or
        `__entry__` when the module defines its own `main` - the user's name
        always wins - carrying the live statements in source order.

        Package units never synthesize one: a package is a library, its
        modules are namespaced, and `sigs` feeds the export table.

        A program's `__main__` module does synthesize one: it is the script
        the whole program runs, so its top-level statements are the entry.
        A program's other modules synthesize `__init__`: their body runs when
        the program first imports them, before the importing module's body.
        """
        if not stmts:
            return []
        if self.module_name and not self._is_program_main():
            if not self.module_init:
                return []
            taken = {fn.name for fn in functions} | self.declared
            name = "__init__" if "__init__" not in taken else "__init_entry__"
            return [self._make_entry(stmts, name)]
        if self.cataloguing:
            # The cataloguing pass only needs the signatures. The program's
            # entry is not an export, so it is not synthesized here.
            return []
        taken = {fn.name for fn in functions} | self.declared
        name = "main" if "main" not in taken else "__entry__"
        return [self._make_entry(stmts, name)]

    def _make_entry(self, stmts: list[ast.stmt], name: str) -> ast.FunctionDef:
        """A synthetic no-argument function carrying `stmts` in source order."""

        fn = ast.FunctionDef(
            name=name,
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=stmts,
            decorator_list=[],
            returns=ast.Constant(value=None),
            type_comment=None,
            type_params=[],
        )
        for stmt in stmts:
            ast.copy_location(fn, stmt)
        fn.lineno = stmts[0].lineno
        fn.col_offset = 0
        fn.end_lineno = stmts[-1].end_lineno
        fn.end_col_offset = 0
        return fn

    def _is_program_main(self) -> bool:
        """True for the `__main__` module of a program directory.

        Project mode: the named entry module plays the same role under its
        own dotted name.
        """

        return self.program and (self.module_name == "__main__" or self.entry_module)

    def _is_main_module(self) -> bool:
        """True when this module runs as `__main__` (a script or program entry)."""

        return not self.module_name or self._is_program_main()

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
                dropped = {param["name"] for param in params if param["type"]["kind"] == "none"}
                params = [param for param in params if param["name"] not in dropped]
                self.locals = {param["name"]: param["type"] for param in params}
                saved_consts = {name: self.constants.get(name) for name in dropped}
                for name in dropped:
                    self.constants[name] = {"kind": "const", "value": None, "type": {"kind": "none"}}
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
                    if ret["kind"] != "none" and not _always_returns(body):
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
        if item["kind"] == "const":
            self.constants[local] = item["value"]
            self.declared.add(local)
            return
        if item["kind"] == "func":
            self.pkg_funcs[local] = item
            self.bindings[local] = Binding("pyfunc", module, name, False, item["return"])
            self.declared.add(local)
            self._install_return_class(module, item["return"], stmt, fences)
            return
        if item["kind"] == "class":
            self._install_class_export(local, item, stmt, fences)
            return
        if item["kind"] == "module":
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
        if typ.get("kind") != "class" or typ["name"] in self.classes:
            return
        item = (self.package or {}).get(module, {}).get(typ["name"])
        if item is None or item["kind"] != "class":
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

    def _is_program_module(self, module: str | None) -> bool:
        """A program tree's own module: a sibling, or a package inside it.

        In a program the export table is keyed by plain names (`util`,
        `pkg.x`), so any exact key match, or a dotted name whose head is a
        program package, is the program's own module. `__main__` is the
        entry module and never importable.

        A dependency source root's package (issue #398) is the program's own
        module too: it is exported under its plain name, so `import certifi`
        binds the package the way a sibling module does.
        """

        if not self.program or not module or self.package is None:
            return False
        if module == "__main__":
            return True
        if module in self.package:
            return True
        head = module.split(".", 1)[0]
        if head in self.program_roots:
            return True
        return head in self.package and self.package[head] is not None

    def _pkg_item(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = (self.package or {}).get(module, {}).get(member)
        if item is None:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _pkg_export(self, node: ast.AST, module: str, member: str) -> dict[str, Any]:
        item = self._pkg_item(node, module, member)
        if item["kind"] not in {"func", "const"}:
            self.fail(node, "PS2001", f"{module}.{member} is not exported by this package")
        return item

    def _linked_call(self, node: ast.Call, span: dict[str, Any], item: dict[str, Any], what: str) -> dict[str, Any]:
        if item["kind"] != "func":
            self.fail(node, "PS2002", f"{what} is not a function")
        args = self._fill_args(node, item["params"], what)
        return {
            "kind": "call",
            "callee": {"kind": "function", "name": item["ir_name"]},
            "args": args,
            "type": item["return"],
            "span": span,
        }

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
                # `if x is None: <arm always exits>` narrows x to its tagged
                # value for the rest of this body, unless a later statement
                # rebinds the name.
                if (
                    isinstance(stmt, ast.If)
                    and len(lowered) == 1
                    and lowered[0]["kind"] == "if"
                    and isinstance(stmt.test, ast.Compare)
                    and len(stmt.test.ops) == 1
                    and isinstance(stmt.test.ops[0], ast.Is)
                ):
                    t_left, t_right = stmt.test.left, stmt.test.comparators[0]
                    probe = t_left if _is_none_expr(t_right) else t_right if _is_none_expr(t_left) else None
                    rest = statements[index + 1 :]
                    if (
                        isinstance(probe, ast.Name)
                        and probe.id in self.locals
                        and self.locals[probe.id].get("optional")
                        and probe.id not in self.fn_globals
                        and _always_returns(lowered[0]["then"])
                        and not any(
                            isinstance(inner, ast.Name)
                            and inner.id == probe.id
                            and isinstance(inner.ctx, (ast.Store, ast.Del))
                            for item in rest
                            for inner in ast.walk(item)
                        )
                    ):
                        self.opt_unwrap[probe.id] = self.locals[probe.id]
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

    def _setitem(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Subscript)
        if isinstance(target.slice, ast.Slice):
            # `xs[a:b] = ys` splices a list, growing or shrinking it; a step
            # stays fenced with the slice-read message.
            slc = target.slice
            container = self._expr(target.value)
            kind = container["type"]["kind"]
            if kind == "list" and container["type"].get("set"):
                self.fail(target, "PS2002", "a set has no item assignment")
            if kind != "list":
                self.fail(target, "PS2002", "slice assignment targets a list")
            if slc.step is not None:
                self.fail(target, "PS2002", "slices are not lowered")
            value = self._expr(stmt.value)
            if value["type"]["kind"] != "list" or not types_equal(value["type"]["elem"], container["type"]["elem"]):
                self.fail(stmt, "PS1003", "slice assignment takes a list of the same element type")
            start, has_start = self._slice_bound(slc.lower, span)
            stop, has_stop = self._slice_bound(slc.upper, span)
            flags = [
                {"kind": "const", "value": str(has_start), "type": {"kind": "int"}, "span": span},
                {"kind": "const", "value": str(has_stop), "type": {"kind": "int"}, "span": span},
            ]
            call = self._abi_call(
                "builtins",
                "list.setslice",
                [container, start, stop, *flags, value],
                {"kind": "none"},
                "ps_seq_splice",
                span,
            )
            return {"kind": "expr", "expr": call, "span": span}
        container = self._expr(target.value)
        index = self._expr(target.slice)
        kind = container["type"]["kind"]
        if kind == "list" and container["type"].get("set"):
            self.fail(target, "PS2002", "a set has no item assignment")
        if kind == "list":
            if index["type"]["kind"] != "int":
                self.fail(target, "PS1003", "a list index is int")
            value = self._expr(stmt.value, container["type"]["elem"])
            self._expect(stmt, value["type"], container["type"]["elem"], "list item")
        elif kind == "dict":
            self._expect(target, index["type"], container["type"]["key"], "dict key")
            value = self._expr(stmt.value, container["type"]["value"])
            self._expect(stmt, value["type"], container["type"]["value"], "dict value")
        else:
            self.fail(target, "PS2002", "item assignment targets a list or dict")
        return {"kind": "setitem", "container": container, "index": index, "value": value, "span": span}

    def _setattr(self, stmt: ast.Assign, span: dict[str, Any]) -> dict[str, Any]:
        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        obj = self._expr(target.value)
        if obj["type"]["kind"] != "class":
            self.fail(target, "PS2001", f"attribute assignment is not lowered ({_dotted(target)})")
        class_name = obj["type"]["name"]
        info = self.classes[class_name]
        if info.get("namedtuple"):
            self.fail(target, "PS2002", f"{class_name}.{target.attr} is read-only on a namedtuple")
        field = next((item for item in info["fields"] if item["name"] == target.attr), None)
        if field is None:
            self.fail(target, "PS2001", f"{class_name}.{target.attr} is not a field")
        value = self._expr(stmt.value, field["type"])
        self._expect(stmt, value["type"], field["type"], target.attr)
        return {"kind": "setattr", "object": obj, "name": target.attr, "value": value, "span": span}

    def _bool_cond(self, node: ast.expr) -> dict[str, Any]:
        if isinstance(node, ast.BoolOp):
            cond = self._boolop(node, span_of(node, self.filename), as_cond=True)
        else:
            cond = self._expr(node)
        kind = cond["type"]["kind"]
        if kind == "bool":
            return cond
        if kind in _TRUTHY or cond["type"].get("optional"):
            return self._truthy(cond, span_of(node, self.filename))
        self.fail(node, "PS2004", "condition must be a bool, not a truthy value")
        raise AssertionError("unreachable")

    def _bool_cond_of(self, node: ast.expr, want: dict[str, Any]) -> dict[str, Any]:
        """A condition whose expected type rides down (the `__getattr__` hook)."""
        if isinstance(node, ast.BoolOp):
            return self._bool_cond(node)
        cond = self._expr(node, want)
        kind = cond["type"]["kind"]
        if kind == "bool":
            return cond
        if kind in _TRUTHY or cond["type"].get("optional"):
            return self._truthy(cond, span_of(node, self.filename))
        self.fail(node, "PS2004", "condition must be a bool, not a truthy value")
        raise AssertionError("unreachable")

    def _expect(self, node: ast.AST, got: dict[str, Any], want: dict[str, Any], what: str) -> None:
        if not types_equal(got, want) and not self._accepts_protocol(want, got):
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

    def _set_display(self, node: ast.Set, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        if not node.elts:
            self.fail(node, "PS1001", "an empty set needs a set annotation")
        if any(isinstance(elt, ast.Starred) for elt in node.elts):
            self.fail(node, "PS2002", "set unpacking is not lowered")
        elem_expect = expect["elem"] if expect and expect.get("set") else None
        first = self._expr(node.elts[0], elem_expect)
        elem = first["type"]
        if elem["kind"] not in {"int", "str"}:
            self.fail(node, "PS2002", "set elements are int or str")
        typ = {"kind": "list", "elem": elem, "set": True}
        if expect and expect.get("set"):
            self._expect(node, typ, expect, "set")
        dest = self._temp("set")
        self.locals[dest] = typ
        self.prelude.append(_assign(dest, {"kind": "list", "elems": [], "type": typ, "span": span}, span))
        self.prelude.extend(self._set_insert_stmts(dest, typ, first, span))
        for elt in node.elts[1:]:
            value = self._expr(elt, elem)
            if not types_equal(value["type"], elem):
                self.fail(node, "PS1003", "set elements have one type")
            self.prelude.extend(self._set_insert_stmts(dest, typ, value, span))
        return _name(dest, typ, span)

    def _comp(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        span: dict[str, Any],
        expect: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # GeneratorExp lowers like ListComp: the elements materialize
        # eagerly into a list (the eager model documented for R24).
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            kind = "list"
        elif isinstance(node, ast.SetComp):
            kind = "set"
        else:
            kind = "dict"
        if any(gen.is_async for gen in node.generators):
            self.fail(node, "PS2002", "async comprehensions are not lowered")
        for gen in node.generators:
            if isinstance(gen.target, (ast.Tuple, ast.List)):
                for elt in gen.target.elts:
                    if not isinstance(elt, ast.Name):
                        self.fail(elt, "PS2002", "for unpacks flat names")
            elif not isinstance(gen.target, ast.Name):
                self.fail(node, "PS2002", "for targets a single name")
        dest = self._temp("comp")
        saved = (
            self.comp_elt,
            self.comp_key,
            self.comp_kind,
            self.comp_gens,
            self.comp_ifs,
            self.comp_dest,
            self.comp_init,
            self.comp_expect,
            self.comp_ready,
        )
        self.comp_elt = node.value if isinstance(node, ast.DictComp) else node.elt
        self.comp_key = node.key if isinstance(node, ast.DictComp) else None
        self.comp_kind = kind
        self.comp_gens = node.generators[1:]
        self.comp_ifs = node.generators[0].ifs
        self.comp_dest = dest
        self.comp_init = None
        self.comp_expect = expect
        self.comp_ready = False
        first = node.generators[0]
        loop = ast.For(target=first.target, iter=first.iter, body=[ast.Pass()], orelse=[], type_comment=None)
        ast.copy_location(loop, node)
        try:
            lowered = self._for(loop, span)
        finally:
            init = self.comp_init
            (
                self.comp_elt,
                self.comp_key,
                self.comp_kind,
                self.comp_gens,
                self.comp_ifs,
                self.comp_dest,
                self.comp_init,
                self.comp_expect,
                self.comp_ready,
            ) = saved
        if init is None:
            self.fail(node, "PS2002", f"{kind} comprehension is not lowered")
        self.prelude.append(init)
        self.prelude.extend(lowered)
        return _name(dest, self.locals[dest], span)

    def _store_constant(self, name: str, value: dict[str, Any], node: ast.AST) -> None:
        if name in self.bindings or name in self.declared or name in self.constants or name in self.type_aliases:
            self.fail(node, "PS2002", f"{name} is already bound")
        self.constants[name] = value
        self.declared.add(name)

    def _store_tuple_constant(self, name: str, node: ast.Tuple, stmt: ast.AST) -> None:
        if len(node.elts) < 2:
            self.fail(node, "PS2002", "tuples need at least two fields")
        elems: list[dict[str, Any]] = []
        for elt in node.elts:
            value = self._expr(elt)
            if value["kind"] != "const":
                self.fail(stmt, "PS2002", "a module assignment must be a constant")
            elems.append(value)
        self._tuple_type([elem["type"] for elem in elems], node)
        self._store_constant(name, {"kind": "tuple-const", "elems": elems}, stmt)

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
            self.fail(stmt, "PS2002", f"global {target} is not a module slot")
        value = self._expr(stmt.value, slot["type"])
        self._expect(stmt, value["type"], slot["type"], target)
        return [self._global_set(target, value, span)]

    def _slot_key(self, name: str) -> str:
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _global_get(self, name: str, span: dict[str, Any]) -> dict[str, Any]:
        slot = self.global_slots[name]
        typ = slot["type"]
        key = _text_ir(self._slot_key(name), "str", span)
        if typ["kind"] == "str":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_str", span)
        if typ["kind"] == "int":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_i64", span)
        if typ["kind"] == "class" and typ.get("name") == "Path":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_path", span)
        if typ["kind"] == "func":
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        if typ["kind"] == "class" and typ.get("name") in PTR_CLASSES:
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        if typ["kind"] in {"list", "dict"}:
            return self._abi_call("builtins", "global_get", [key], typ, "ps_global_get_ptr", span)
        self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        raise AssertionError("unreachable")

    def _global_set(self, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        typ = value["type"]
        key = _text_ir(self._slot_key(name), "str", span)
        if typ["kind"] == "str":
            symbol = "ps_global_set_str"
        elif typ["kind"] == "int":
            symbol = "ps_global_set_i64"
        elif typ["kind"] == "class" and typ.get("name") == "Path":
            symbol = "ps_global_set_path"
        elif typ["kind"] == "func":
            symbol = "ps_global_set_ptr"
        elif typ["kind"] == "class" and typ.get("name") in PTR_CLASSES:
            symbol = "ps_global_set_ptr"
        elif typ["kind"] in {"list", "dict"}:
            symbol = "ps_global_set_ptr"
        else:
            self.fail(ast.Pass(), "PS2002", f"global {name} of {_show(typ)} is not lowered")
        return {
            "kind": "expr",
            "expr": self._abi_call("builtins", "global_set", [key, value], {"kind": "none"}, symbol, span),
            "span": span,
        }

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
            value = {
                "kind": "closure",
                "name": self._ir_name(stmt.name),
                "env": [],
                "type": {"kind": "func", "params": [p["type"] for p in params], "return": ret},
                "span": span,
            }
            try:
                for dec in reversed(stmt.decorator_list):
                    value = self._apply_decorator(dec, value, span)
            except Fence as exc:
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
                if value["type"]["kind"] != "func" and not self.prelude and module_needs_slot(value):
                    # A container or parser at plain-assign scope keeps its
                    # identity through the slot (#512), so it does not fall
                    # through to the constant replay below.
                    self._bind_callable_slot(target, value, span, stmt)
                    return
            except Fence as exc:
                self.caught.append(exc)
            if value is None or self.caught or value["type"]["kind"] != "func" or self.prelude:
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

    def _is_none(self, node: ast.Compare, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        left, right = node.left, node.comparators[0]
        # A process slot's `is None` is the unset flag. Every other None literal
        # is the existing == / != compare against none.
        if _is_none_expr(right):
            name = self._slot_read_name(left)
            if name is not None:
                return self._slot_is_unset(name, span, negated)
            return self._compare_none(node, self._expr(left), negated, span, none_on_left=False)
        if _is_none_expr(left):
            name = self._slot_read_name(right)
            if name is not None:
                return self._slot_is_unset(name, span, negated)
            return self._compare_none(node, self._expr(right), negated, span, none_on_left=True)
        return self._identity(node, span, negated)

    def _slot_is_unset(self, name: str, span: dict[str, Any], negated: bool) -> dict[str, Any]:
        flag = self._global_is_set(name, span)
        if negated:
            return flag
        return {"kind": "unary", "op": "not", "operand": flag, "type": {"kind": "bool"}, "span": span}

    def _compare_none(
        self,
        node: ast.AST,
        value: dict[str, Any],
        negated: bool,
        span: dict[str, Any],
        *,
        none_on_left: bool,
    ) -> dict[str, Any]:
        kind = value["type"]["kind"]
        if kind == "class" and value["type"].get("optional"):
            tag = _attr(value, "_tag", {"kind": "bool"}, span)
            if negated:
                return tag
            return {"kind": "unary", "op": "not", "operand": tag, "type": {"kind": "bool"}, "span": span}
        if kind not in {"none", "int", "float", "bool", "str", "bytes"}:
            self.fail(node, "PS2004", f"{_show(value['type'])} cannot compare with None")
        none = _const_none(span)
        op = "!=" if negated else "=="
        left, right = (none, value) if none_on_left else (value, none)
        return _compare(op, left, right, span)

    def _slot_read_name(self, node: ast.expr) -> str | None:
        if not isinstance(node, ast.Name) or node.id in self.locals:
            return None
        slot = self.global_slots.get(node.id)
        if slot is None or not slot.get("active"):
            return None
        return node.id

    def _materialize_tuple(
        self, elems: list[dict[str, Any]], span: dict[str, Any], node: ast.AST | None = None
    ) -> dict[str, Any]:
        typ = self._tuple_type([elem["type"] for elem in elems], node or ast.Pass())
        temp = self._temp("tuple")
        self.locals[temp] = typ
        self.prelude.extend(_tuple_assign(temp, typ, elems, span))
        return _name(temp, typ, span)

    def _live_statements(
        self,
        statements: list[ast.stmt],
        fences: list[Fence],
        rebound: set[str] | None = None,
    ) -> list[ast.stmt]:
        # Names assigned by user code. A later `if TYPE_CHECKING` is the typing
        # constant only while that name is still the erased import.
        if rebound is None:
            rebound = set()
        live: list[ast.stmt] = []
        for stmt in statements:
            if isinstance(stmt, ast.Import):
                self._bind_import(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if isinstance(stmt, ast.ImportFrom):
                self._bind_from(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if _is_main_guard(stmt):
                if self._is_main_module():
                    # C3: the canonical guard's body is live script text. It joins
                    # the entry in source order; an `else` under a `__name__` test
                    # never runs as `__main__`, so it stays dropped.
                    live.extend(self._live_statements(list(stmt.body), fences, rebound))
                    continue
                # An imported module is not `__main__`: the guard body never
                # runs there, and the `else` branch does.
                live.extend(self._live_statements(list(stmt.orelse), fences, rebound))
                continue
            if _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.If):
                taken = self._static_branch(stmt, rebound)
                if taken is None:
                    live.append(stmt)
                    rebound.update(_module_assigned(stmt))
                    continue
                # The other branch is not lowered. Its imports and annotations stay unread.
                live.extend(self._live_statements(taken, fences, rebound))
                continue
            live.append(stmt)
            rebound.update(_module_assigned(stmt))
        return live

    def _static_branch(self, stmt: ast.If, rebound: set[str]) -> list[ast.stmt] | None:
        held = self._version_holds(stmt.test)
        if held is None:
            held = self._type_checking_value(stmt.test, rebound)
        if held is None:
            return None
        return list(stmt.body if held else stmt.orelse)

    def _type_checking_value(self, node: ast.expr, rebound: set[str]) -> bool | None:
        """typing.TYPE_CHECKING is False. `not TYPE_CHECKING` is True. Anything else is not this fold."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self._type_checking_value(node.operand, rebound)
            if inner is None:
                return None
            return not inner
        if isinstance(node, ast.Name):
            if node.id in rebound:
                return None
            if self.type_aliases.get(node.id) == "TYPE_CHECKING":
                return False
            return None
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id not in rebound
        ):
            binding = self.bindings.get(node.value.id)
            if binding is not None and binding.kind == "erased" and binding.module in _ERASED_MODULES:
                return False
        return None

    def _version_holds(self, test: ast.expr) -> bool | None:
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            return None
        op = _CMP.get(type(test.ops[0]))
        if op is None:
            return None
        left = self._version_operand(test.left)
        right = self._version_operand(test.comparators[0])
        if left == "version" and isinstance(right, tuple):
            return _cmp_version(op, right)
        if right == "version" and isinstance(left, tuple):
            flipped = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "==": "==", "!=": "!="}[op]
            return _cmp_version(flipped, left)
        return None

    def _version_operand(self, node: ast.expr) -> str | tuple[int, ...] | None:
        if self._is_version_info(node):
            return "version"
        if isinstance(node, ast.Tuple) and node.elts and all(_is_int_const(elt) for elt in node.elts):
            return tuple(int(elt.value) for elt in node.elts if isinstance(elt, ast.Constant))
        return None

    def _is_version_info(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Attribute) or node.attr != "version_info" or not isinstance(node.value, ast.Name):
            return False
        binding = self.bindings.get(node.value.id)
        return (
            binding is not None
            and binding.kind == "module"
            and binding.module == "sys"
            and not binding.rooted
            and not binding.queued
        )

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
            return {"kind": "str"}
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return {"kind": "int"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
            return {"kind": "str"}
        if self._is_mod(node, "importlib.resources", "as_file"):
            return {"kind": "class", "name": "Path"}
        # Checker bridge: the checker inferred the slot's type from the
        # initializer expression; the literal rules above stay the fast path
        # and everything else consults the map when a bridge is wired.
        return self._bridge_type(node)

    def _temp(self, hint: str) -> str:
        self.temp_n += 1
        return f".ps_{hint}_{self.temp_n}"


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


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _const_str_join(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level f-string pieces that are already str constants are one str constant."""

    if any(part.get("kind") != "const" or part.get("type", {}).get("kind") != "str" for part in parts):
        return None
    text = "".join(str(part["value"]) for part in parts)
    return {"kind": "const", "value": text, "type": {"kind": "str"}, "span": span}


def _concat_str(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    if not parts:
        return {"kind": "const", "value": "", "type": {"kind": "str"}, "span": span}
    acc = parts[0]
    for part in parts[1:]:
        acc = {"kind": "binary", "op": "+", "left": acc, "right": part, "type": {"kind": "str"}, "span": span}
    return acc


def _const_list_concat(left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """Two constant lists of one element type are one list. A runtime list stays fenced."""

    if left.get("kind") != "list" or right.get("kind") != "list":
        return None
    if left["type"]["kind"] != "list" or right["type"]["kind"] != "list":
        return None
    if left["type"].get("set") or right["type"].get("set"):
        return None
    if not types_equal(left["type"]["elem"], right["type"]["elem"]):
        return None
    return {
        "kind": "list",
        "elems": [*left["elems"], *right["elems"]],
        "type": {"kind": "list", "elem": left["type"]["elem"]},
        "span": span,
    }


def _static_int(expr: dict[str, Any]) -> int | None:
    if expr.get("kind") == "const" and expr.get("type", {}).get("kind") == "int":
        value = expr.get("value")
        if isinstance(value, str) and _int_text(value):
            return int(value)
    if expr.get("kind") == "unary" and expr.get("op") == "-" and isinstance(expr.get("operand"), dict):
        inner = _static_int(expr["operand"])
        if inner is not None:
            return -inner
    return None


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


def _text_ir(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span}


def _is_int_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _cmp_version(op: str, parts: tuple[int, ...]) -> bool:
    current = sys.version_info
    if op == ">=":
        return current >= parts
    if op == ">":
        return current > parts
    if op == "<=":
        return current <= parts
    if op == "<":
        return current < parts
    if op == "==":
        return current == parts
    if op == "!=":
        return current != parts
    return False


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None
