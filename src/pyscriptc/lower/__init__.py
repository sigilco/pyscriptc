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
    _accepts_any_elem,
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
    _ast_exits,
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
        # The function body being lowered, so `del name` can see a later load.
        self.fn_body: list[ast.stmt] = []
        # Names bound to frozenset(...). The value is still the set list. .add and
        # .remove on these names stay fenced. A plain set name is not in this set.
        self.frozen_names: set[str] = set()
        # `if x is not None:` narrowing on a tagged optional: inside the taken
        # branch a read of the name lowers to its `_val` field. name -> opt typ.
        self.opt_unwrap: dict[str, dict[str, Any]] = {}
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
                # boxed:begin
                # A peek cannot bind an island module handle local (issues
                # #650, #643): the island calls it walks return an isolated
                # handle node instead of touching the scope's prelude.
                s.saved_peeking = self.peeking
                self.peeking = True
                # boxed:end
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
                # boxed:begin
                self.peeking = s.saved_peeking
                # boxed:end

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
        # boxed:begin
        # The boxed tier (issue #612): a call to an island function is an
        # opaque handle, never a typed return; the peek must not give the
        # call site the signature's return type, because the body never
        # lowers and the trampoline marshals by annotation only.
        if node.func.id in self.island_fns:
            return None
        # boxed:end
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

    def _pin_namespace_locals(
        self, functions: list[ast.FunctionDef | ast.AsyncFunctionDef]
    ) -> list[dict[str, Any]] | None:
        """The Namespace fields the module's `add_argument` calls register.

        The pin pass runs before any body lowers, so a namespace attribute
        argument (`batch_rename(args.work_dir, ...)`) has no field types yet:
        the real `add_argument` lowering registers them later. This reads the
        same closed keyword set from the AST, without lowering anything, so
        the peek can type the attribute. Anything the real handler would
        fence (an unknown keyword, a non-constant name) returns None and the
        pin refuses the parameter, exactly as today.

        The scan covers every function in the module, not one caller: an
        argparse parser is module state, and a script may build it in one
        function (`get_parser`) and read the namespace in another (`main`).
        """

        from pyscriptc.restlower._argparse import peek_namespace_fields

        body: list[ast.stmt] = []
        for fn in functions:
            body.extend(fn.body)
        fields = peek_namespace_fields(self, body)
        if fields is None:
            return None
        info = self.classes.get("Namespace")
        if info is None:
            return None
        # The peek's Namespace layout is the registered one. The real
        # `add_argument` lowering overwrites it with the same records, so
        # the two agree; the peek never runs the real handler.
        info["fields"] = fields
        info["own_fields"] = fields
        return fields

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

            def _ns_type() -> dict[str, Any] | None:
                """The Namespace type, when the caller registered arguments.

                `args = parser.parse_args()` lowers late; the pin walk binds
                the local so an attribute argument (`args.work_dir`) peeks
                through the field table the add_argument scan built.
                """

                if self.classes.get("Namespace", {}).get("fields"):
                    return {"kind": "class", "name": "Namespace"}
                return None

            def _assign_type(value: ast.expr) -> dict[str, Any] | None:
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "parse_args"
                    and (typ := _ns_type()) is not None
                ):
                    return typ
                return self._infer_type_of(value)

            def _visit(body: list[ast.stmt]) -> None:
                for item in body:
                    if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                        if item.targets[0].id in self.bindings:
                            continue
                        _bind(item.targets[0].id, _assign_type(item.value))
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
        self._pin_namespace_locals(list(by_name.values()))
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

    # boxed:begin
    def _boxed_resolve(self, fences: list[Fence]) -> None:
        """The boxed planner, before any body lowers (issue #612).

        Runs the fence-free probe per definition so the plane map is known
        before call sites lower: a fenced definition's fences carry
        `_boxed_probe` and leave no trace, and its name lands in
        `island_fns`, which routes the calls to it through the island
        trampoline. Definitions the probe clears lower native with their
        real fences intact (a later fence in the body still refuses).
        """
        if not self.fallback_definitions:
            return
        saved_caught = self.caught
        try:
            for fn in self._boxed_functions:
                if fn.name in self.declared or fn.name in self.generic_fns:
                    continue
                # boxed:begin
                # Issue #676: the deferred decorator pass already planned a
                # name the island seed owns; its decorator fences dropped
                # with the definition, so it never lowers and no probe runs.
                if fn.name in self._boxed_island:
                    continue
                # boxed:end
                probe_fences: list[Fence] = []
                saved_function = self.function
                self.function = fn.name
                try:
                    self._boxed_probe(fn, probe_fences)
                finally:
                    self.function = saved_function
                if probe_fences:
                    self._boxed_island.add(fn.name)
        finally:
            self.caught = saved_caught
        self.island_fns = set(self._boxed_island)
        self._boxed_demoted_by = {}

        # Issue #677: annotated `int` is i64 (divergence 1), so a definition
        # that accumulates an unbounded int natively aborts on overflow where
        # the island would print CPython's big int. A conservative static
        # check finds the growth shape and routes the whole definition to the
        # island instead: an unannotated definition rides the island exactly
        # like a fenced one, so this closes the gap without changing the
        # typed-subset contract. Runs after the reset above so the demotion
        # reasons survive the #620 cascade below.
        for fn in self._boxed_functions:
            if fn.name in self.island_fns or fn.name not in self.sigs or fn.name in self.generic_fns:
                continue
            growth = _bigint_growth(fn)
            if growth is not None:
                self._boxed_island.add(fn.name)
                self._boxed_demoted_by[fn.name] = f"possible i64 overflow at {growth}"
        self.island_fns = set(self._boxed_island)
        boxed_fns_by_name = {fn.name: fn for fn in self._boxed_functions}

        # Issue #620: Cascade island definitions to native siblings.
        # If an island definition calls a native sibling, demote that sibling
        # transitively to a fixpoint.
        # Issue #674: a native caller passing a mutable container (list,
        # set, dict) to an island callee loses in-place mutation: the
        # trampoline marshals a fresh island copy and nothing copies the
        # cells back. Demote the caller to the island so the call is an
        # island-to-island call on the same object. One fixpoint: demoting
        # a caller (#674) can make its own native callees need the #620
        # demotion and the other way around.
        changed = True
        while changed:
            changed = False
            for fn in self._boxed_functions:
                if fn.name not in self.island_fns:
                    continue
                # Issue #675: a native sibling read as a value (passed as a
                # callable, reduced over, stored) is no more reachable
                # native-side than one called directly (issue #620): the
                # name does not resolve in the island namespace either way.
                # Demote any bare name load, not only a call.
                loaded = set()
                for node in ast.walk(fn):
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                        loaded.add(node.id)
                    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        # `mod.helper` names `mod`, not `helper`.
                        loaded.add(node.value.id)
                for callee in loaded:
                    if callee in boxed_fns_by_name and callee not in self.island_fns:
                        if callee in self.generic_fns:
                            continue
                        self.island_fns.add(callee)
                        self._boxed_island.add(callee)
                        if callee not in self._boxed_demoted_by:
                            self._boxed_demoted_by[callee] = fn.name
                        changed = True
            for fn in self._boxed_functions:
                if fn.name in self.island_fns:
                    continue
                if fn.name in self.generic_fns or fn.name in self.declared:
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                        continue
                    callee = node.func.id
                    if callee not in self.island_fns or callee not in boxed_fns_by_name:
                        continue
                    if self._island_call_passes_mutable(callee):
                        self.island_fns.add(fn.name)
                        self._boxed_island.add(fn.name)
                        self._boxed_demoted_by.setdefault(
                            fn.name,
                            f"island function {callee} takes a mutable container argument "
                            "(a copy-in copy-out seam would lose in-place mutation, issue #674)",
                        )
                        changed = True
                        break

        # Issue #628: cascade the island class to the compiled functions
        # that name it. The class object and its instances exist only
        # island-side, so a function that uses one runs island-side too
        # (the #620 rule, with the class as the island definition).
        if self.island_classes:
            for fn in self._boxed_functions:
                if fn.name in self.island_fns:
                    continue
                used = sorted(
                    node.id for node in ast.walk(fn) if isinstance(node, ast.Name) and node.id in self.island_classes
                )
                if used:
                    self.island_fns.add(fn.name)
                    self._boxed_island.add(fn.name)
                    self._boxed_demoted_by.setdefault(fn.name, f"island class {used[0]}")

        self._boxed_sibling_fences: list[Fence] = []
        # Keep the PS2002 diagnostic only where demotion is impossible and say why.
        for fn in self._boxed_functions:
            if fn.name not in self.island_fns:
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    callee = node.func.id
                    if callee in self.sigs and callee not in self.island_fns:
                        if callee in self.generic_fns:
                            reason = (
                                f"generic compiled function {callee} cannot be demoted to the island; "
                                "the island-to-compiled seam is the `--emit binding` surface"
                            )
                        elif callee not in boxed_fns_by_name:
                            reason = (
                                f"{callee} is not a module-level definition and cannot be demoted to the island; "
                                "the island-to-compiled seam is the `--emit binding` surface"
                            )
                        else:
                            reason = (
                                "the island-to-compiled seam is the `--emit binding` surface; "
                                f"annotate {callee} so it lowers native-to-native, "
                                "or drop its annotations so it joins the island"
                            )
                        msg = f"{fn.name} runs on the island and cannot call the compiled function {callee}: {reason}"
                        fences.append(Fence("PS2002", msg, span_of(node, self.filename), fn.name))
                        self._boxed_sibling_fences.append(fences[-1])

    def _island_call_passes_mutable(self, callee: str) -> bool:
        """Whether the island callee has a mutable container parameter
        (issue #674).

        The callee's proven signature is the fact: an unprovable signature
        fenced the callee itself, so `sigs` answers for every parameter that
        marshals at all. A list (a set lowers as a list with the set flag),
        a dict or an optional wrapper of either mutates in place, so the
        copy-in seam would lose the mutation. Scalars, str and bytes are
        immutable and stay on the copy-in trampoline. Call-site binding is
        deliberately ignored: the callee's contract is that the parameter
        can be mutated, and any caller that binds it has the same hazard.
        """
        try:
            params, _ret = self.sigs[callee]
        except KeyError:
            return True
        for param in params:
            kind = param["type"]["kind"]
            elem = param["type"].get("elem") or {}
            if kind in {"list", "dict"} or param["type"].get("set"):
                return True
            if param["type"].get("optional") and elem.get("kind") in {"list", "dict"}:
                return True
        return False

    def _boxed_island_class(self, node: ast.ClassDef) -> None:
        """Plan one class definition for the island (issue #628).

        The class leaves every native table, so a compiled call site resolves
        it through the island handle path (or demotes, see `_boxed_resolve`),
        and the class lowering pass skips it: CPython defines it island-side.
        """
        self.island_classes.add(node.name)
        self.classes.pop(node.name, None)
        self.declared.discard(node.name)
        self.known_classes.discard(node.name)
        self.class_aliases.pop(node.name, None)

    def _boxed_replan(self, fences: list[Fence]) -> None:
        """Re-run the function planner after a class joins the island late.

        A class that only fences while its method bodies lower becomes
        island after `_boxed_resolve` already ran. `island_fns` recomputes
        from the accumulated `_boxed_island` set (so a demotion is never
        undone), and the sibling refusal fences the previous pass appended
        are dropped before the new pass appends its own.
        """
        if not self.fallback_definitions:
            return
        stale = set(map(id, self._boxed_sibling_fences))
        fences[:] = [fence for fence in fences if id(fence) not in stale]
        self._boxed_resolve(fences)
        # The entry's probe ran before this class joined the island, so it
        # may have cleared uses the island class now expresses differently.
        # A fence there is the whole top level fencing (Decision A): the
        # module body runs island-side, exactly as if the probe had run after
        # the class decision.
        entry_name = self.entry_fn_name
        if self._boxed_top_island or entry_name is None:
            return
        entry = next((fn for fn in self._boxed_functions if fn.name == entry_name), None)
        if entry is None:
            return
        probe: list[Fence] = []
        saved_function = self.function
        self.function = entry.name
        try:
            self._boxed_probe(entry, probe)
        finally:
            self.function = saved_function
        if probe:
            self._boxed_top_island = True
            self._boxed_island.add(entry.name)
            self.island_fns.add(entry.name)

    def _boxed_probe(self, fn: ast.FunctionDef, sink: list[Fence]) -> None:
        """Lower `fn`'s body into `sink`, discarding the IR.

        The scope setup mirrors the real body pass (issue #612): the
        signature's parameters become the locals, the per-function state
        starts fresh, and everything restores after. Without it the probe
        would fence on the function's own parameters (`url` is not a local)
        and misplan every function that reads one.
        """

        self.caught = sink
        try:
            saved: dict[str, Any] = {
                "locals": self.locals,
                "prelude": self.prelude,
                "temp": self.temp_n,
                "modules": self.island_modules,
                "class_handles": self.island_class_handles,
                "globals": self.fn_globals,
                "nonlocals": self.fn_nonlocals,
                "captures": self.captures,
                "frozen": self.frozen_names,
                "unwrap": self.opt_unwrap,
                "boxed": self.boxed_names,
                "fn_body": self.fn_body,
                "handle_locals": self.handle_locals,
                "consumed": self.consumed_handles,
                "returned": self.returned_handles,
                "borrowed": self.borrowed_handles,
            }
            try:
                self.temp_n = 0
                self.prelude = []
                self.island_modules = {}
                self.island_class_handles = {}
                self.fn_globals = self._globals_for(fn.body)
                self.fn_nonlocals = set()
                self.captures = {}
                params, ret = self._coro_body_sig(fn.name)
                self.locals = {param["name"]: param["type"] for param in params}
                self.frozen_names = set()
                self.opt_unwrap = {}
                self.boxed_names = set()
                self.fn_body = list(fn.body)
                self.handle_locals = {}
                self.consumed_handles = set()
                self.returned_handles = set()
                self.borrowed_handles = {
                    name for name, typ in self.locals.items() if isinstance(typ, dict) and is_handle_type(typ)
                }
                body = self._body(list(fn.body))
                if not self.caught:
                    body = self._scope_exit_frees(body, span_of(fn, self.filename))
                    if ret["kind"] != "none" and not _always_returns(body):
                        sink.append(
                            Fence(
                                "PS1004",
                                f"{fn.name} can fall off the end and return None",
                                span_of(fn, self.filename),
                                fn.name,
                            )
                        )
            except Fence as exc:
                sink.append(exc)
            finally:
                self.locals = saved["locals"]
                self.prelude = saved["prelude"]
                self.temp_n = saved["temp"]
                self.island_modules = saved["modules"]
                self.island_class_handles = saved["class_handles"]
                self.fn_globals = saved["globals"]
                self.fn_nonlocals = saved["nonlocals"]
                self.captures = saved["captures"]
                self.frozen_names = saved["frozen"]
                self.opt_unwrap = saved["unwrap"]
                self.boxed_names = saved["boxed"]
                self.fn_body = saved["fn_body"]
                self.handle_locals = saved["handle_locals"]
                self.consumed_handles = saved["consumed"]
                self.returned_handles = saved["returned"]
                self.borrowed_handles = saved["borrowed"]
        finally:
            self.caught = []

    def _boxed_island_source(
        self,
        tree: ast.Module,
        island: set[str],
        top_level_island: bool,
        globals_out: dict[str, str],
    ) -> str:
        """The source text the island seed execs (issue #612, #613).

        With a native top level (Stage 2) the seed must not re-execute
        side-effecting top-level statements (the native init already ran
        them; a second run doubles output). It carries the statements an
        island function needs to resolve its names: every top-level `import`
        (module bindings, no side effects beyond the import itself, which
        CPython caches), module assignments whose value is a literal or a
        deterministic initializer, and the source segment of every island
        definition.

        With the island top level (Stage 3, Decision A) the seed is the
        module's own source text: the embedded interpreter execs it as the
        `__main__` body exactly once (`ps_island_top_seed` sets `__name__`),
        so the script's main guard fires island-side and `raise
        SystemExit(...)` delivers the process status (Decision E). The
        annotated scalar globals the top level binds are recorded in
        `globals_out` so the emitter unboxes them once at init into native
        readers (Decision E).
        """

        if not top_level_island:
            return self._boxed_stage2_source(tree, island)
        # The annotated scalar globals the top level binds join the native
        # slot table from their module-level AnnAssign, so a typed native
        # reader unboxes the island value once at init. An annotated global
        # normally binds as a constant (no slot); here the annotation gives
        # the slot its type instead. An unannotated top-level global never
        # got a slot (it fenced): it stays island-only.
        for stmt in tree.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            name = stmt.target.id
            if name in self.constants:
                # The split bound it as a module constant (annotation plus a
                # simple value). The island top level owns the binding, so
                # the constant replay leaves and the slot table takes the
                # name: a typed native reader unboxes the island value
                # through the shadow slot instead of folding the literal.
                del self.constants[name]
                self.declared.discard(name)
            if name in self.declared:
                continue
            slot = self.global_slots.get(name)
            if slot is None:
                try:
                    typ = self._annotation(stmt.annotation)
                except Fence:
                    continue
                if not self._boxed_unboxable_global(typ):
                    continue
                self.global_slots[name] = {"type": typ, "active": False}
                slot = self.global_slots[name]
            elif slot.get("island"):
                continue
            else:
                typ = slot.get("type")
                if slot.get("active") or not self._boxed_unboxable_global(typ):
                    continue
            try:
                self._activate_slot(name, stmt)
            except Fence:
                self.global_slots.pop(name, None)
                self.declared.discard(name)
                continue
            slot["island"] = True
            kind = typ["kind"]
            if kind == "list":
                pad = self._boxed_island_pad_code(typ["elem"])
                globals_out[name] = f"5:{pad}"
            else:
                globals_out[name] = str({"int": 1, "str": 3, "bool": 4, "bytes": 6}[kind])
        for slot_name, slot in self.global_slots.items():
            if not slot.get("active") or slot.get("island"):
                continue
            typ = slot.get("type")
            if not self._boxed_unboxable_global(typ):
                continue
            slot["island"] = True
            kind = typ["kind"]
            if kind == "list":
                pad = self._boxed_island_pad_code(typ["elem"])
                globals_out[slot_name] = f"5:{pad}"
            else:
                globals_out[slot_name] = str({"int": 1, "str": 3, "bool": 4, "bytes": 6}[kind])
        return self.source

    def _boxed_island_pad_code(self, typ: dict[str, Any]) -> int:
        """The `pad` element code `_island_pad` emits, mirrored at the lowerer.

        The trampoline stores an unboxed list cell per element; the pad names
        the element kind the same way the emitter's `_ISLAND_ARG_CODES` does.
        """

        from pyscriptc.emit.llvm._abi import AbiMixin

        return AbiMixin._ISLAND_ARG_CODES[typ["kind"]]

    def _boxed_stage2_source(self, tree: ast.Module, island: set[str]) -> str:
        """The Stage 2 seed: imports, literal module values, island defs."""
        segments: list[str] = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                segments.append(ast.get_source_segment(self.source, stmt) or "")
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and self._boxed_is_literal_module_value(stmt):
                segment = ast.get_source_segment(self.source, stmt)
                if segment:
                    segments.append(segment)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in island:
                segment = self._boxed_definition_source(node)
                if segment:
                    segments.append(segment)
            # Issue #628: an island class definition joins the seed too, in
            # source order beside the island functions that instantiate it.
            elif isinstance(node, ast.ClassDef) and node.name in self.island_classes:
                segment = self._boxed_definition_source(node)
                if segment:
                    segments.append(segment)
        return "\n\n\n".join(segment for segment in segments if segment)

    def _boxed_definition_source(self, node: ast.AST) -> str:
        """One definition's source text, decorators included.

        `ast.get_source_segment` starts a decorated definition at its `def`
        or `class` line, so the decorator lines come from the decorator
        expressions themselves. An island class without its decorator would
        silently lose it (a `@dataclass(frozen=True)` class would lose its
        generated `__init__`, issue #628).
        """
        segment = ast.get_source_segment(self.source, node)
        if not segment:
            return ""
        imprint = []
        for decorator in getattr(node, "decorator_list", []) or []:
            text = ast.get_source_segment(self.source, decorator)
            if text:
                imprint.append(f"@{text}")
        if not imprint:
            return segment
        return "\n".join([*imprint, segment])

    def _boxed_is_literal_module_value(self, stmt: ast.Assign | ast.AnnAssign) -> bool:
        """True when the value is safe to re-execute island-side.

        A literal (a constant or a display of constants) is always safe.
        An initializer built from deterministic calls (`Path.home() / x`)
        also re-executes to the same value, and the dogfood scripts read
        such globals (`SESSION_DIR`, `ROOT`) from island functions; the
        re-execution divergence is documented (plan Decision E: globals
        are one-directional at this seam, and Stage 3 replaces this rule
        with an island-owned top level). An import-bound name resolves
        through the seeded imports, so attribute and call chains over it
        lower island-side too.
        """
        value = getattr(stmt, "value", None)
        if value is None:
            return False
        for node in ast.walk(value):
            if isinstance(node, (ast.expr_context, ast.operator, ast.boolop, ast.unaryop, ast.cmpop)):
                continue
            if isinstance(node, ast.Constant):
                continue
            if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                continue
            if isinstance(node, ast.UnaryOp):
                continue
            if isinstance(
                node,
                (
                    ast.Name,
                    ast.Attribute,
                    ast.Call,
                    ast.BinOp,
                    ast.BoolOp,
                    ast.JoinedStr,
                    ast.FormattedValue,
                    ast.IfExp,
                ),
            ):
                continue
            return False
        return True

    def _boxed_unboxable_global(self, typ: Any) -> bool:
        """True when an island global's annotation is a marshalable scalar.

        Decision E (issue #613): a native reader unboxes the island value
        once after the seed runs; the accepted shapes are the ones the
        native global-slot runtime can hold and the native readers can read
        back - bool, int, str, bytes, and lists of scalars. Float has no
        native slot getter today, so it stays island-side (a native reader
        of it fences with the named diagnostic); `Path` and other handles
        stay island-side the same way.
        """

        if not isinstance(typ, dict):
            return False
        if typ.get("island") or typ.get("optional"):
            return False
        kind = typ.get("kind")
        if kind in {"bool", "int", "str", "bytes"}:
            return True
        if kind == "list":
            elem = typ.get("elem") or {}
            return not elem.get("optional") and elem.get("kind") in {"bool", "int", "float", "str", "bytes"}
        return False

    # boxed:end

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
        # Issue #587: a deeply nested AST overflows the lowerer's recursive
        # descent with an uncaught RecursionError. CPython's parser still
        # accepts these programs (its own nesting guard only counts
        # parentheses), so the guard lives here: past the depth the
        # recursive passes can handle, the module fences with the fit
        # diagnostic instead of crashing the compiler.
        if (depth := _ast_depth(tree)) > MAX_LOWER_DEPTH:
            fence = Fence(
                "PS3002",
                f"nesting is deeper than the lowerer handles: {depth} AST levels (over {MAX_LOWER_DEPTH})",
                span_of(_deepest_node(tree), self.filename),
                None,
            )
            return {
                "irVersion": IR_VERSION,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": self.filename,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": [fence.as_dict()],
            }
        tree = self._desugar(tree)
        fences: list[Fence] = []
        functions: list[ast.FunctionDef] = []
        class_nodes: list[ast.ClassDef] = []
        live = self._live_statements(list(tree.body), fences)
        self.global_slots = self._scan_global_slots(live)
        entry_stmts = self._split_entry(live, functions, class_nodes, fences)
        # boxed:end
        entry_fn = self._entry_function(entry_stmts, functions)
        entry_name = entry_fn[0].name if entry_fn else None
        self.entry_fn_name = entry_name
        functions.extend(entry_fn)
        # boxed:end
        self._install_synthetics(class_nodes, fences)
        imported = {name for name, info in self.classes.items() if info.get("imported")}
        imported.update(self.class_aliases)
        self.known_classes = {node.name for node in class_nodes}
        self.known_classes.update(name for name, info in self.classes.items() if info.get("synthetic"))
        self.known_classes.update(imported)
        for node in class_nodes:
            self.function = node.name
            self.caught = []
            # boxed:begin
            # Issue #628: a class whose definition fences (a decorator, class
            # keywords, a class-body statement) is planned for the island, and
            # its fences drop with it: the island seed defines the class in
            # CPython, exactly like an island function.
            if self.fallback_definitions:
                class_fences: list[Fence] = []
                try:
                    self._collect_class(node)
                except Fence as exc:
                    class_fences.append(exc)
                class_fences.extend(self.caught)
                if class_fences:
                    self._boxed_island_class(node)
                else:
                    fences.extend(class_fences)
                continue
            # boxed:end
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
                # boxed:begin
                # The signature itself fenced (an unannotated parameter or
                # return, issue #612): under the fallback tier this plans
                # the definition for the island, and the planner drops the
                # fence with the definition.
                if self.fallback_definitions:
                    self._boxed_island.add(fn.name)
                # boxed:end
            else:
                params, ret = self.sigs[fn.name]
                if _has_typevar(ret) or any(_has_typevar(param["type"]) for param in params):
                    self.generic_fns[fn.name] = fn
        self._pin_call_site_params(functions, fences)
        if self.deferred_module:
            self._bind_deferred_module(fences)
        # boxed:begin
        # The boxed plane decision happens here, before any body lowers, so
        # every call site sees the same `island_fns` (issue #612). Names the
        # deferred decorator pass planned early (issue #676) join the probe
        # set, so `_boxed_resolve` sees the same island set the planner
        # would have produced from a body fence.
        self._boxed_functions = list(functions)
        self._boxed_resolve(fences)
        # The island top level (issue #613, Decision A): the synthesized
        # entry is the module body, so it is probed like any definition.
        # A fenced probe means module-level statements fence native - the
        # whole top level runs island-side and the entry's fences drop with
        # it (the entry never lowers native: the body pass skips it).
        # Module-level import fences (PS2001/PS2002, issue #627) route the
        # module top level to the island floor as well. A deferred module
        # assignment fence ("a module assignment must be a constant", Refs
        # #641) carries the `__module__` sentinel instead of None, and
        # routes the top level the same way: the seed execs the real
        # statement, so the value is CPython's own.
        if self.fallback_definitions:
            probe_fences: list[Fence] = []
            if entry_fn:
                saved_function = self.function
                self.function = entry_name
                try:
                    self._boxed_probe(entry_fn[0], probe_fences)
                finally:
                    self.function = saved_function
            has_import_fence = any(
                fence.function in (None, "__module__") and fence.code in ("PS2001", "PS2002") for fence in fences
            )
            self._boxed_top_island = bool(probe_fences) or has_import_fence
            if self._boxed_top_island:
                if probe_fences:
                    fences.extend(probe_fences)
                if entry_name:
                    self._boxed_island.add(entry_name)
        # boxed:end
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
            # boxed:begin
            # Issue #628: a class that only fences while its method bodies
            # lower joins the island too. The fence and the class's fences
            # drop, and the function planes are re-planned so a compiled
            # caller of the now-island class demotes with it.
            class_mark = len(fences)
            nested_mark = len(self.nested_fns)
            # boxed:end
            try:
                lowered = self._lower_class(node)
            except Fence as exc:
                fences.append(exc)
                fences.extend(self.caught)
                # boxed:begin
                if self.fallback_definitions:
                    self._boxed_island_class(node)
                    del fences[class_mark:]
                    self.nested_fns = self.nested_fns[:nested_mark]
                    self._boxed_replan(fences)
                # boxed:end
                continue
            fences.extend(self.caught)
            # boxed:begin
            if self.fallback_definitions and self.caught:
                self._boxed_island_class(node)
                del fences[class_mark:]
                self.nested_fns = self.nested_fns[:nested_mark]
                self._boxed_replan(fences)
                continue
            # boxed:end
            if lowered is not None and not self.caught:
                lowered_classes.append(lowered)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        lowered: list[dict[str, Any]] = []
        for fn in functions:
            if fn.name not in self.sigs or fn.name in self.generic_fns:
                continue
            # boxed:begin
            # A definition planned for the island never lowers (issue
            # #612): its probe already fenced, the fences dropped, and the
            # island runs its real body. The entry joins this list when the
            # top level is island (issue #613).
            if self.fallback_definitions and fn.name in self.island_fns:
                continue
            # boxed:end
            self.function = fn.name
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
        # boxed:begin
        # The boxed per-definition planner (issue #612): the fence list of a
        # definition planned for the island leaves the module fence list and
        # the definition leaves the lowered list (its body never lowered).
        # Stage 3 (issue #613): a fenced entry probe plans the whole top
        # level for the island (Decision A is all-or-nothing in both
        # directions), and the annotated scalar globals the top level binds
        # unbox once into native readers.
        fallback_map: dict[str, Any] | None = None
        if self.fallback_definitions:
            from pyscriptc import boxed as _boxed

            island = set(self.island_fns)
            # The island top level (issue #613): the entry name is the
            # island's own entry (the seed runs the whole module body), so
            # it stays out of the native lowered list and its fences drop
            # with the top level. A native top level keeps the Stage 2
            # shape: the entry lowers native, an island function is a
            # trampoline call from it.
            if self._boxed_top_island:
                if entry_name:
                    island.add(entry_name)
                    lowered = [record for record in lowered if record["name"] != entry_name]
                fences = [
                    fence
                    for fence in fences
                    if (fence.function != entry_name if entry_name is not None else True)
                    and not (fence.function in (None, "__module__") and fence.code in ("PS2001", "PS2002"))
                ]
            else:
                island.discard(entry_name)
            self.island_fns = island
            lowered = [record for record in lowered if record["name"] not in island]
            siblings = set(map(id, self._boxed_sibling_fences))
            fences = [fence for fence in fences if fence.function not in island or id(fence) in siblings]
            # The island seed is the whole module body island-side (issue
            # #613, the original source, `__name__ = "__main__"`) or the
            # Stage 2 shape (only the pieces island functions need resolve
            # their names).
            top_level_island = self._boxed_top_island
            all_names = [record["name"] for record in lowered] + [fn.name for fn in functions]
            fallback_map = _boxed.plane_map(
                [fence.as_dict() for fence in fences],
                [{"name": n} for n in dict.fromkeys(all_names)],
                entry_name,
                top_level_island,
                demoted_by=self._boxed_demoted_by,
                # Issue #628: the class planes the collect and lowering passes
                # decided, so the summary reports what the binary does.
                classes=[{"name": node.name, "island": node.name in self.island_classes} for node in class_nodes],
            )
            for name in island:
                if name == entry_name or (name.startswith("__") and name.endswith("__")):
                    continue
                fallback_map["functions"][name] = _boxed.ISLAND
            for name in self.island_classes:
                fallback_map["classes"][name] = _boxed.ISLAND
            island_globals: dict[str, str] = {}
            module_plane = ""
            # An island class needs the seed even when no function rides it:
            # a native class method reaches the class through its handle.
            if island or top_level_island or self.island_classes:
                module_plane = self._boxed_island_source(tree, island, top_level_island, island_globals)
        # boxed:end
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
        # boxed:begin
        if fallback_map is not None:
            module["fallback"] = fallback_map
            if module_plane:
                module["islandSource"] = module_plane
            if island_globals:
                module["islandGlobals"] = island_globals
        # boxed:end
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
        """Names bound at module scope whose entry reassigns or mutates them.

        Module constants re-emit their literal on every read, so a script's
        `counts[k] = v` would write into a throwaway dict; the binding lowers
        inside the entry instead unless the name hides inside a def/class.
        Similarly, any module variable reassigned in the script must lower
        inside the entry so its updates take effect.
        """
        bound: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
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
            if self._script_line is not None and stmt.lineno >= self._script_line:
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Name) and n.id in bound and isinstance(n.ctx, (ast.Store, ast.Del)):
                        mutated.add(n.id)
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
                # `if x is None: <arm always exits>` (or any test that proves
                # x set when the other arm is the only one left) narrows x to
                # its tagged value for the rest of this body, unless a later
                # statement rebinds the name.
                if isinstance(stmt, ast.If):
                    when_true, when_false = self._narrow_names(stmt.test)
                    then_exits = _ast_exits(stmt.body) or (
                        len(lowered) == 1 and lowered[0]["kind"] == "if" and _always_returns(lowered[0]["then"])
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
        # boxed:begin
        # An island handle is opaque, and the seam carries values one way:
        # a compiled attribute write would have to carry the value back into
        # the island, so it fences instead of reaching a native field table.
        if obj["type"].get("island"):
            self.fail(
                target,
                "PS2001",
                f"{class_name} is an island handle: mutate it on the island, or read the result back",
            )
        # boxed:end
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
            if _accepts_any_elem(want, got):
                # A concrete container satisfies a dict[K, Any] / list[Any]
                # annotation (issue #814): the annotation does not pin the
                # element type, and the value carries one.
                return
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
            value = self._module_fold(elt)
            if value is None:
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

    # boxed:begin
    def _slot_island_key(self, name: str) -> str:
        """The shadow slot key of an island-owned global (issue #613)."""

        return f"__island__{self._slot_key(name)}"

    # boxed:end

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


# boxed:begin
def _bigint_growth(fn: ast.FunctionDef) -> str | None:
    """The growing operation of an unbounded int accumulation in `fn`, or None.

    Issue #677: annotated `int` lowers as i64 (divergence 1), so native
    arithmetic aborts on overflow where CPython grows a big int. This check
    is deliberately conservative: any in-place update of the same name by
    a multiplying operator (`*=`, `<<=`, `**=`) or an assign of
    `name <op> anything` back to `name` (the loop-carry shape
    `r = r * i` / `r <<= 1`) flags the definition. A single `name ** k`
    assign with a literal exponent of at most 62 and no loop in the body
    can never leave i64, so it is excluded. Add and subtract are not
    growth: two i64 adds of in-range values stay in range unless the
    operands were already huge, while `*`/`<<`/`**` double the bit length
    every step and outgrow i64 from small constants. The false-positive
    cost is one demotion to the island; the false-negative cost is an
    abort, so the check leans wide.
    """
    has_loop = any(isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(fn))
    for node in ast.walk(fn):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, (ast.Mult, ast.LShift, ast.Pow)):
            if isinstance(node.target, ast.Name):
                return f"'{node.target.id} {type(node.op).__name__}='"
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, (ast.Mult, ast.LShift, ast.Pow))
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value.left, ast.Name)
            and node.value.left.id == node.targets[0].id
            and not (isinstance(node.value.op, ast.Pow) and not has_loop and _bounded_pow(node.value.right))
        ):
            return f"'{node.targets[0].id} {type(node.value.op).__name__}='"
    return None


def _bounded_pow(node: ast.expr) -> bool:
    """True for `**` with a literal exponent small enough to stay in i64."""

    return isinstance(node, ast.Constant) and isinstance(node.value, int) and 0 <= node.value <= 62


# boxed:end


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
