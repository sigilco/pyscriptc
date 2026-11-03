"""HIR-to-MIR lowering for the R4 leaf slice.

The leaf slice covers pure scalar arithmetic: int/float/bool constants,
binary arithmetic and comparison on scalars, unary minus and `not`, names,
assignments, and `return`. It is the straight-line and one-level-`if` shape
the byte-identity A/B runs against; everything outside the slice stays on
the direct path unchanged.

Three functions:

- `supported(fn)`: the slice test. A function is in the slice when every
  statement and expression is one the leaf handles; the module walker skips
  a function the slice does not cover, and the MIR lane falls back to the
  direct emitter for it. This keeps the strangler incremental: the slice
  grows, the fallback shrinks.
- `lower_function(fn)`: one IR function to a `MirFunction` built through
  `MirBuilder`, so construction invariants hold by construction.
- `lower_module(module)`: every in-slice function, as a `MirModule`. A
  module whose functions are all out of slice returns a module with an
  empty function list, which the emitter refuses.

Name-to-value mapping: every local (including parameters, which the IR
mirrors into `locals`) gets one value slot in `slots`, allocated in
`locals` order before the body walk, so slot ids are input-order stable.
Expression values are the operand ints of `mir.py` (a value id, or a
negative constant-pool reference).

The MIR lane emits `w` for ints and bools, `d` for floats; comparisons
produce `w` 0/1 values, the bool the branch op reads.

"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _ir_const_value
from pyscriptc.errors import IRError
from pyscriptc.ir import fits_i64
from pyscriptc.mir import (
    MIR_ADDD,
    MIR_ADDW,
    MIR_B,
    MIR_CEQD,
    MIR_CEQW,
    MIR_CGED,
    MIR_CGEW,
    MIR_CGTD,
    MIR_CGTW,
    MIR_CLED,
    MIR_CLEW,
    MIR_CLTD,
    MIR_CLTW,
    MIR_CNED,
    MIR_CNEW,
    MIR_COPY,
    MIR_D,
    MIR_MULD,
    MIR_MULW,
    MIR_NEGW,
    MIR_NOTB,
    MIR_SUBD,
    MIR_SUBW,
    MIR_W,
    MirBuilder,
    MirFunction,
    MirModule,
)

# The binary op to the MIR op, per operand kind. `//` and `%` on ints and
# `/`/`%`/`//`/`**` on floats go through runtime helpers; they are part of
# the slice through `HELPER_OPS` below.
_W_BINOPS = {
    "+": MIR_ADDW,
    "-": MIR_SUBW,
    "*": MIR_MULW,
}
_D_BINOPS = {
    "+": MIR_ADDD,
    "-": MIR_SUBD,
    "*": MIR_MULD,
}
_W_CMPOPS = {
    "<": MIR_CLTW,
    "<=": MIR_CLEW,
    ">": MIR_CGTW,
    ">=": MIR_CGEW,
    "==": MIR_CEQW,
    "!=": MIR_CNEW,
}
_D_CMPOPS = {
    "<": MIR_CLTD,
    "<=": MIR_CLED,
    ">": MIR_CGTD,
    ">=": MIR_CGED,
    "==": MIR_CEQD,
    "!=": MIR_CNED,
}

# The ps-helper-backed binary ops the slice lowers, per operand kind.
_W_HELPER_OPS = {
    "//": "floordiv",
    "%": "imod",
}
_D_HELPER_OPS = {
    "/": "fdiv",
    "%": "fmod",
    "//": "ffloordiv",
    "**": "fpow",
}

_EXPR_KINDS = frozenset({"const", "name", "binary", "compare", "unary"})
_STMT_KINDS = frozenset({"assign", "expr", "return", "pass", "if"})


def supported_expr(expr: dict[str, Any]) -> bool:
    """The slice test for one expression node, recursive over its children."""
    kind = expr["kind"]
    if kind not in _EXPR_KINDS:
        return False
    if kind == "const":
        if expr["type"]["kind"] not in ("int", "float", "bool"):
            return False
        if expr["type"]["kind"] == "int" and not fits_i64(int(expr["value"])):
            return False
        return True
    if kind == "name":
        return expr["type"]["kind"] in ("int", "float", "bool")
    if kind == "unary":
        if expr["type"]["kind"] not in ("int", "float", "bool"):
            return False
        if expr["op"] == "not":
            return expr["type"]["kind"] == "bool" and supported_expr(expr["operand"])
        if expr["op"] != "-":
            return False
        return expr["type"]["kind"] in ("int", "float") and supported_expr(expr["operand"])
    if kind == "binary":
        return _supported_binary(expr)
    if kind == "compare":
        return _supported_compare(expr)
    return False


def _supported_binary(expr: dict[str, Any]) -> bool:
    tkind = expr["type"]["kind"]
    op = expr["op"]
    if tkind == "int":
        if op not in _W_BINOPS and op not in _W_HELPER_OPS:
            return False
    elif tkind == "float":
        if op not in _D_BINOPS and op not in _D_HELPER_OPS:
            return False
    elif tkind == "bool" and op in ("&", "|", "^"):
        pass
    else:
        return False
    if expr["left"]["type"]["kind"] not in ("int", "float", "bool"):
        return False
    if expr["left"]["type"]["kind"] != expr["right"]["type"]["kind"]:
        return False
    return supported_expr(expr["left"]) and supported_expr(expr["right"])


def _supported_compare(expr: dict[str, Any]) -> bool:
    op = expr["op"]
    if op not in _W_CMPOPS:
        return False
    lkind = expr["left"]["type"]["kind"]
    rkind = expr["right"]["type"]["kind"]
    if lkind != rkind or lkind not in ("int", "float", "bool"):
        return False
    return supported_expr(expr["left"]) and supported_expr(expr["right"])


def supported_stmt(stmt: dict[str, Any]) -> bool:
    """The slice test for one statement, recursive into nested bodies."""
    kind = stmt["kind"]
    if kind not in _STMT_KINDS:
        return False
    if kind == "assign":
        return supported_expr(stmt["value"])
    if kind == "expr":
        return supported_expr(stmt["expr"])
    if kind == "return":
        return supported_expr(stmt["value"])
    if kind == "if":
        if not supported_expr(stmt["cond"]):
            return False
        return all(supported_stmt(s) for s in stmt["then"]) and all(supported_stmt(s) for s in stmt["else"])
    return True


def supported_fn(fn: dict[str, Any]) -> bool:
    """The slice test for one function: scalar params, scalar return, in-slice body."""
    if fn.get("env") is not None:
        return False
    if fn["returnType"]["kind"] not in ("int", "float", "bool"):
        return False
    for param in fn["params"]:
        if param["type"]["kind"] not in ("int", "float", "bool"):
            return False
    for local in fn["locals"]:
        if local["type"]["kind"] not in ("int", "float", "bool"):
            return False
    return all(supported_stmt(stmt) for stmt in fn["body"])


def _param_tag(kind: str) -> int:
    if kind == "float":
        return MIR_D
    if kind == "bool":
        return MIR_B
    return MIR_W


def _fn_result_tag(fn: dict[str, Any]) -> int:
    return _param_tag(fn["returnType"]["kind"])


class _FnLowerer:
    """One function's body walk. Slots and the builder live here."""

    def __init__(self, fn: dict[str, Any]) -> None:
        self.fn = fn
        self.builder = MirBuilder(fn["name"])
        self.slots: dict[str, int] = {}
        self.local_types: dict[str, str] = {}

    def run(self) -> MirFunction:
        builder = self.builder
        builder.set_result(_fn_result_tag(self.fn))
        # Parameters first, in IR order: their value ids seed the space the
        # body reads. The IR mirrors each parameter into `locals`, so the
        # locals walk skips the names the params already took.
        for param in self.fn["params"]:
            self.slots[param["name"]] = builder.add_param(_param_tag(param["type"]["kind"]))
        # Every name (parameters included) gets one slot value id, in
        # locals order: the id the emitter renders as an alloca, stores
        # write, and pre-store reads load from. Parameters keep their
        # parameter ids for the prologue store; the slot id is the memory.
        extra_locals = list(self.fn["locals"])
        self.slot_vids: dict[str, int] = {}
        for local in extra_locals:
            vid = builder.next_vid
            builder.next_vid += 1
            self.slot_vids[local["name"]] = vid
            self.local_types[local["name"]] = local["type"]["kind"]
        builder.start_block()
        # The prologue: every parameter stores into its slot value, the
        # direct path's `store i64 %a, ptr %tN` per parameter. Later reads
        # of the parameter's name read the slot, not the raw parameter.
        for param in self.fn["params"]:
            self.store(param["name"], self.slots[param["name"]])
        self.emit_body(self.fn["body"])
        return builder.finish()

    def kind_of(self, name: str) -> str:
        for param in self.fn["params"]:
            if param["name"] == name:
                return param["type"]["kind"]
        return self.local_types[name]

    def emit_body(self, body: list[dict[str, Any]]) -> None:
        for stmt in body:
            self.emit_stmt(stmt)

    def emit_stmt(self, stmt: dict[str, Any]) -> None:
        kind = stmt["kind"]
        if kind == "pass":
            return
        if kind == "assign":
            value = self.emit_expr(stmt["value"])
            self.store(stmt["target"], value)
            return
        if kind == "expr":
            self.emit_expr(stmt["expr"])
            return
        if kind == "return":
            value = self.emit_expr(stmt["value"])
            self.builder.emit_ret(value)
            return
        if kind == "if":
            self.emit_if(stmt)
            return
        raise IRError(f"the MIR leaf slice cannot lower statement {kind}")

    def emit_if(self, stmt: dict[str, Any]) -> None:
        """One-level if without block arguments on the join.

        The join block takes the merge of the two arms' effects through
        ordinary stores to the shared slots; no value flows through a
        block argument in this slice, so the branch binds nothing.
        """
        builder = self.builder
        cond = self.emit_expr(stmt["cond"])
        then_id = len(builder.fn.blocks)
        else_id = then_id + 1
        merge_id = then_id + 2
        builder.emit_br(cond, then_id, else_id, [], [])
        builder.start_block()
        self.emit_body(stmt["then"])
        then_dead = builder.terminated
        if not then_dead:
            builder.emit_jmp(merge_id)
        builder.start_block()
        self.emit_body(stmt["else"])
        else_dead = builder.terminated
        if not else_dead:
            builder.emit_jmp(merge_id)
        if then_dead and else_dead:
            return
        builder.start_block()
        self.emit_body([s for s in stmt.get("orelse", [])])

    def store(self, name: str, value: int) -> None:
        """A store to `name`: a `MIR_COPY` into the name's slot value.

        The slot value is a MIR value id (never the parameter's own id), so
        the emitter renders every definition of a name as one `store` and
        every read as one `load`, the same shape the direct walk produces.
        `arg2` of the copy carries the value's tag (`copy_result_type`
        reads it), so the verifier's type walk stays true.
        """
        vid = self.slot_vids.get(name)
        if vid is None:
            raise IRError(f"the MIR leaf slice has no slot for {name}")
        tag = _param_tag(self.kind_of(name))
        self.builder.emit(MIR_COPY, vid, value, tag)

    def load_slot(self, name: str) -> int:
        """The operand for a read of `name`: the name's slot value.

        Every read goes through the slot, the way the direct walk loads
        the alloca per access; the emitter renders the slot read as one
        `load`, so read counts and register order match the direct path
        byte for byte.
        """
        vid = self.slot_vids.get(name)
        if vid is None:
            raise IRError(f"the MIR leaf slice has no slot for {name}")
        return vid

    def emit_expr(self, expr: dict[str, Any]) -> int:
        kind = expr["kind"]
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            return self.load_slot(expr["id"])
        if kind == "binary":
            return self.emit_binary(expr)
        if kind == "compare":
            return self.emit_compare(expr)
        if kind == "unary":
            return self.emit_unary(expr)
        raise IRError(f"the MIR leaf slice cannot lower expression {kind}")

    def emit_const(self, expr: dict[str, Any]) -> int:
        tkind = expr["type"]["kind"]
        value = _ir_const_value(expr["type"], expr["value"])
        if tkind == "int":
            return self.builder.const_w(value)
        if tkind == "float":
            return self.builder.const_d(value)
        if tkind == "bool":
            return self.builder.const_b(1 if value else 0)
        raise IRError(f"the MIR leaf slice cannot lower a {tkind} constant")

    def emit_binary(self, expr: dict[str, Any]) -> int:
        left = self.emit_expr(expr["left"])
        right = self.emit_expr(expr["right"])
        tkind = expr["type"]["kind"]
        op = expr["op"]
        if tkind == "int":
            mir_op = _W_BINOPS.get(op)
            if mir_op is not None:
                return self.builder.emit_val(mir_op, left, right, MIR_W)
            return self.emit_helper(_W_HELPER_OPS[op], MIR_W, left, right)
        if tkind == "float":
            mir_op = _D_BINOPS.get(op)
            if mir_op is not None:
                return self.builder.emit_val(mir_op, left, right, MIR_D)
            return self.emit_helper(_D_HELPER_OPS[op], MIR_D, left, right)
        raise IRError(f"the MIR leaf slice cannot lower {op} on {tkind}")

    def emit_helper(self, helper: str, tag: int, left: int, right: int) -> int:
        """A runtime-helper binary op, carried as a `MIR_COPY` with the

        helper recorded out of band. The MIR op vocabulary has no call op
        yet (calls are a later slice), so the copy instruction's index maps
        to the helper name on the function; the emitter renders it as
        `call <tag> @ps.<name>` and needs the operands only. The copy's
        result type rides `arg2` (the `emit_copy` convention), so the
        verifier's type walk stays true to the value's tag.
        """
        vid = self.builder.emit_val(MIR_COPY, left, right, tag)
        index = len(self.builder.fn.ops) - 1
        # `helpers` carries (helper, tag): the copy's arg1/arg2 hold the
        # operands, so the result tag lives beside the helper name.
        self.builder.fn.helpers[index] = f"{helper} {tag}"
        return vid

    def emit_compare(self, expr: dict[str, Any]) -> int:
        left = self.emit_expr(expr["left"])
        right = self.emit_expr(expr["right"])
        kind = expr["left"]["type"]["kind"]
        table = _D_CMPOPS if kind == "float" else _W_CMPOPS
        mir_op = table[expr["op"]]
        return self.builder.emit_val(mir_op, left, right, MIR_B)

    def emit_unary(self, expr: dict[str, Any]) -> int:
        operand = self.emit_expr(expr["operand"])
        op = expr["op"]
        tkind = expr["type"]["kind"]
        if op == "not":
            return self.builder.emit_val(MIR_NOTB, operand, -1, MIR_B)
        if op == "-":
            if tkind == "int":
                return self.builder.emit_val(MIR_NEGW, operand, -1, MIR_W)


def lower_function(fn: dict[str, Any]) -> MirFunction:
    """Lower one in-slice IR function to MIR. `supported_fn` must hold."""
    return _FnLowerer(fn).run()


def lower_module(module: dict[str, Any]) -> MirModule:
    """Lower every in-slice function; out-of-slice functions are skipped.

    The skipped names come back on `module.skipped` so the emitter can
    refuse a module whose entry the slice cannot lower, instead of
    silently emitting a module without it.
    """
    functions: list[MirFunction] = []
    skipped: list[str] = []
    for fn in module["functions"]:
        if supported_fn(fn):
            functions.append(lower_function(fn))
        else:
            skipped.append(fn["name"])
    mir_module = MirModule(source_file=module["sourceFile"], functions=functions, entry=module.get("entry"))
    mir_module.skipped = skipped
    return mir_module
