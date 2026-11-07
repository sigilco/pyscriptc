"""The MIR-or-direct adapter seam (B11).

One module owns the per-function lane verdict so the flip decision has a
single place to land: today `emit_llvm_mir` splices MIR-lowered bodies over
the direct emitter's text, and each function the slice rejects rides the
direct path unchanged. This module is where that hand-off is recorded: for
every IR function the plan says either MIR (the `MirFunction` the lane
lowered) or the direct path (a fallback record), so the telemetry the
design note (`.agents/docs/design/mir-adapter-telemetry.md`) specifies has
one producer and one call site.

The verdict itself is not re-decided here: `mirlower.lower_module` already
runs the slice predicates and the callee fixpoint, and its `skipped` list is
the authoritative out-of-lane set. The adapter maps each IR function to its
`MirFunction` or records the fallback with the reason the coverage script's
predicate chain reports (`out_of_slice_reason`, the same walk
`scripts/mir_coverage.py` uses; the script delegates here so the vocabulary
has one source). A function `supported_fn` accepts but the callee fixpoint
rejects records `fn:callee out of slice`, matching the coverage report.

Records carry exactly the note's three fields — `module` (the IR
`sourceFile`), `function` (the IR function name) and `reason` — and nothing
else. They surface through `last_plan()` for the CLI's `--debug` table and
`summary.json`, and through the `emit.mir` / `emit.mir.adapter` audit-log
events. All of it is a side channel: the emitted `.ll`, the IR and the TIR
hashes do not move.

Self-host spell: IR payloads are `Dict[str, Any]` and node reads go through
`in` checks and subscripts, the same shape `mirlower.py` lowers clean.
"""

from __future__ import annotations

from typing import Any, Dict

from pyscriptc.log import emit_event
from pyscriptc.mir import MirFunction, MirModule
from pyscriptc.mirlower import lower_module, supported_expr, supported_fn, supported_stmt, supported_type

# The reason walk's child tables, the same keys `scripts/mir_coverage.py`
# used before the walk moved here. Lists iterate and the frozenset is the
# membership table, the spellings `mirlower.py` lowers clean.
_EXPR_CHILDREN = ["operand", "left", "right", "value", "hay", "needle"]
_STMT_EXPRS = ["object", "value", "expr", "cond"]
_STMT_BODIES = ["then", "else", "body"]

_CALLEE_REASON = "fn:callee out of slice"


def _expr_label(expr: Dict[str, Any]) -> str:
    """The rejected expression's label: kind, plus op and type kind where the
    node carries them (`expr:binary & int`, `expr:call`, ...)."""
    kind: str = expr["kind"]
    tkind: str = "None"
    if "type" in expr:
        typ: Dict[str, Any] = expr["type"]
        if "kind" in typ:
            tkind = typ["kind"]
    if kind in ("binary", "compare", "unary") and "op" in expr:
        op: str = expr["op"]
        return f"expr:{kind} {op} {tkind}"
    if kind in ("const", "name"):
        return f"expr:{kind} {tkind}"
    return f"expr:{kind}"


def _expr_reason(expr: Dict[str, Any]) -> str:
    """The innermost rejected node under an expression `supported_expr` rejects."""
    for key in _EXPR_CHILDREN:
        if key not in expr:
            continue
        child: Dict[str, Any] = expr[key]
        if child is None or "kind" not in child:
            continue
        if not supported_expr(child):
            return _expr_reason(child)
    return _expr_label(expr)


def _stmt_reason(stmt: Dict[str, Any]) -> str:
    """The innermost rejected node under a statement `supported_stmt` rejects."""
    for key in _STMT_EXPRS:
        if key not in stmt:
            continue
        child: Dict[str, Any] = stmt[key]
        if child is None or "kind" not in child:
            continue
        none_typed = False
        if "type" in child:
            ctyp: Dict[str, Any] = child["type"]
            if "kind" in ctyp:
                ctype_kind: str = ctyp["kind"]
                if ctype_kind == "none":
                    none_typed = True
        if none_typed and key != "cond":
            ckind: str = child["kind"]
            if ckind not in ("const", "name"):
                return f"{_expr_label(child)} (none-typed)"
            continue
        if not supported_expr(child):
            return _expr_reason(child)
    for key in _STMT_BODIES:
        if key not in stmt:
            continue
        body: list[Dict[str, Any]] = stmt[key]
        for inner in body:
            if inner is not None and "kind" in inner and not supported_stmt(inner):
                return _stmt_reason(inner)
    skind: str = stmt["kind"]
    return f"stmt:{skind}"


def out_of_slice_reason(fn: Dict[str, Any]) -> str | None:
    """Why `supported_fn` rejects `fn`, or None when it is in the slice.

    The first failing check wins, in `supported_fn`'s own order: closure
    env, return type, params, locals, then the first rejected statement.
    """
    if supported_fn(fn):
        return None
    if "env" in fn:
        env: str | None = fn["env"]
        if env is not None:
            return "fn:closure env"
    rt: Dict[str, Any] = fn["returnType"]
    rtk: str = rt["kind"]
    if rtk != "none" and not supported_type(rt):
        return f"return:{rtk}"
    for key, label, allow_none in (("params", "param", False), ("locals", "local", True)):
        entries: list[Dict[str, Any]] = fn[key]
        for entry in entries:
            typ: Dict[str, Any] = entry["type"]
            kind: str = typ["kind"]
            if kind == "none":
                if allow_none:
                    continue
                return f"{label}:{kind}"
            if not supported_type(typ):
                return f"{label}:{kind}"
    body: list[Dict[str, Any]] = fn["body"]
    for stmt in body:
        if not supported_stmt(stmt):
            return _stmt_reason(stmt)
    return "fn:unclassified"


def route_function(fn: Dict[str, Any], lowered: Dict[str, MirFunction]) -> MirFunction | None:
    """One IR function's lane verdict: its `MirFunction` on the MIR lane,
    None when the function rides the direct path unchanged.

    The verdict is membership in the set `lower_module` admitted — the
    slice predicates plus the callee fixpoint — not `supported_fn` alone,
    so a caller of an out-of-lane callee stays on the direct path with
    them.
    """
    name: str = fn["name"]
    return lowered.get(name)


def _fallback_record(fn: Dict[str, Any], source_file: str) -> Dict[str, Any]:
    """The three-field record for one function leaving the MIR lane."""
    name: str = fn["name"]
    reason = out_of_slice_reason(fn)
    record_reason: str = _CALLEE_REASON
    if reason is not None:
        record_reason = reason
    record: Dict[str, Any] = {"module": source_file, "function": name, "reason": record_reason}
    return record


def plan_module(module: Dict[str, Any]) -> Dict[str, Any]:
    """Lower `module` through the MIR lane and record every fallback.

    This is the adapter's one entry point: `emit_llvm_mir` calls it once
    per compile. The returned payload carries the `MirModule` the emitter
    splices (`plan["mir"]`), the fallback records sorted by function name
    (`plan["fallbacks"]`), the in-lane count (`plan["lowered"]`) and the
    module's `sourceFile` (`plan["source_file"]`); the CLI's debug surface
    reads the same payload through `last_plan()`.
    """
    mir: MirModule = lower_module(module)
    by_name: Dict[str, MirFunction] = {}
    for mir_fn in mir.functions:
        by_name[mir_fn.name] = mir_fn
    source_file: str = module["sourceFile"]
    fns: list[Dict[str, Any]] = module["functions"]
    names: list[str] = []
    record_by_name: Dict[str, Any] = {}
    for ir_fn in fns:
        if route_function(ir_fn, by_name) is not None:
            continue
        record = _fallback_record(ir_fn, source_file)
        fname: str = record["function"]
        names.append(fname)
        record_by_name[fname] = record
    # One module per plan, so module-then-function order is function order.
    # `sorted(key=)` does not lower on record elements; sorting the names
    # and looking each record back up keeps the same order fence-free.
    fallbacks: list[Dict[str, Any]] = []
    for name in sorted(names):
        rec: Dict[str, Any] = record_by_name[name]
        fallbacks.append(rec)
    lowered: int = len(mir.functions)
    plan: Dict[str, Any] = {}
    plan["mir"] = mir
    plan["fallbacks"] = fallbacks
    plan["lowered"] = lowered
    plan["source_file"] = source_file
    _LAST_PLAN["plan"] = plan
    _log_plan(lowered, fallbacks)
    return plan


def _log_plan(lowered: int, fallbacks: list[Dict[str, Any]]) -> None:
    """The audit-log surface: one INFO summary per compile, one DEBUG
    record per adapter function (the local log may hold the source path).

    The payloads go through `emit_event` positionally: keyword arguments
    and mixed-type dict literals are outside the self-host subset, so the
    summary dict is built by subscript stores into a `Dict[str, Any]`.
    """
    reasons: Dict[str, int] = {}
    for record in fallbacks:
        reason: str = record["reason"]
        if reason in reasons:
            reasons[reason] = reasons[reason] + 1
        else:
            reasons[reason] = 1
    payload: Dict[str, Any] = {}
    payload["lowered"] = lowered
    payload["adapter"] = len(fallbacks)
    payload["reasons"] = reasons
    emit_event("info", "emit.mir", payload)
    for record in fallbacks:
        emit_event("debug", "emit.mir.adapter", record)


# The most recent plan, for the CLI's debug surface: a dict cell so the
# write stays a plain subscript store (no `global` declaration).
_LAST_PLAN: Dict[str, Any] = {}


def last_plan() -> Dict[str, Any] | None:
    """The plan the most recent `plan_module` call recorded, or None."""
    if "plan" not in _LAST_PLAN:
        return None
    plan: Dict[str, Any] = _LAST_PLAN["plan"]
    return plan
