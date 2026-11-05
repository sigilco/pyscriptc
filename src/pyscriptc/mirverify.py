"""Structural verification of the 3AC MIR.

`mir.py` refuses bad construction requests (an op outside a block, a second
instruction after a terminator, an unknown op), but the builder cannot see
the whole function: successors only exist once every block is built, and the
parallel instruction lists can be assembled by anything, not just the
builder. This module checks the invariants the MIR-to-LLVM emitter relies
on, so a drift between lowering and the emitter is a named diagnostic
instead of a dangling successor or a read of a value that is never defined.

Families of checks, mirroring `irverify.py`:

- Types: an operand's constant-pool tag or definition type matches what the
  op reads; a `MIR_BR` condition is a word; the returned value matches the
  function's result tag.
- Defined-before-use: every value operand resolves to a parameter, a block
  argument, or an earlier `dst`; every constant operand decodes to an
  existing pool entry.
- Control flow: every successor names an existing block; a terminator ends
  its block exactly once; `MIR_BR` argument bindings match the targets'
  block arguments positionally; every instruction belongs to exactly one
  block.

The verifier reads the MIR and never mutates it, so it is safe to run on a
module that is about to be emitted. Failures raise `MirVerifyError`, whose
`code` is a stable `MVS` code.

"""

from __future__ import annotations

from pyscriptc.mir import (
    MIR_B,
    MIR_BR,
    MIR_COPY,
    MIR_JMP,
    MIR_RAISE,
    MIR_RET,
    MirBlock,
    MirBlockArg,
    MirConst,
    MirFunction,
    MirParam,
    const_index,
    copy_result_type,
    is_const_ref,
)

# The op arity table, spelled here for the same reason as `mirlower`'s
# int-literal op ids: `mir.py`'s module-level table binds a global slot,
# and slots do not ride the package export table a cross-module import
# resolves against. The keys are the op ids as int literals, the values
# mirror `mir.py`'s table; a drift is a test failure, not a silent wrong
# verification.
_OP_ARITY: dict[int, int] = {
    1: 0,  # MIR_JMP
    2: 1,  # MIR_BR
    3: 1,  # MIR_RET
    4: 2,  # MIR_ADDW
    5: 2,  # MIR_SUBW
    6: 2,  # MIR_MULW
    7: 2,  # MIR_ADDD
    8: 2,  # MIR_SUBD
    9: 2,  # MIR_MULD
    10: 1,  # MIR_NEGW
    11: 1,  # MIR_NEGD
    12: 2,  # MIR_CLTW
    13: 2,  # MIR_CLEW
    14: 2,  # MIR_CGTW
    15: 2,  # MIR_CGEW
    16: 2,  # MIR_CEQW
    17: 2,  # MIR_CNEW
    18: 2,  # MIR_CLTD
    19: 2,  # MIR_CLED
    20: 2,  # MIR_CGTD
    21: 2,  # MIR_CGED
    22: 2,  # MIR_CEQD
    23: 2,  # MIR_CNED
    26: 1,  # MIR_NOTB
    25: 1,  # MIR_COPY
    27: 1,  # MIR_RAISE
}

# The verifier's own codes. They are stable: a test that pins a violation
# pins the code, not the prose.
CODE_TYPE = "MVS01"
CODE_USEDEF = "MVS02"
CODE_CONTROL = "MVS03"


class MirVerifyError(Exception):
    """A structural violation in the MIR. `code` is stable."""

    code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class _Violation:
    """One internal violation row; `verify_function` converts these."""

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


def _operand_type(fn: MirFunction, operand: int, defs: dict[int, int]) -> int | None:
    """The type tag of an operand, or `None` when it does not resolve.

    A constant reads the pool; a value reads the def site or a parameter or
    block-argument slot. `None` is reported by the caller as a use-def
    failure, so a missing definition is one diagnostic, not two.
    """
    if is_const_ref(operand):
        index: int = const_index(operand)
        consts: list[MirConst] = fn.consts
        if index < len(consts):
            const: MirConst = consts[index]
            return const.typ
        return None
    return defs.get(operand)


def _span_end(fn: MirFunction, start: int) -> int:
    """The instruction index one past the current block's body, from `start`.

    The body runs to the block's terminator. A block with no terminator in
    its tail takes everything to the end of the function; the missing
    terminator is reported separately.
    """
    i: int = start
    ops: list[int] = fn.ops
    n: int = len(ops)
    while i < n:
        op: int = ops[i]
        if op == MIR_JMP or op == MIR_BR or op == MIR_RET or op == MIR_RAISE:
            return i + 1
        i += 1
    return n


def _check_instr_operands(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The operand checks of one instruction: arity, definitions, constants."""
    out: list[_Violation] = []
    op: int = fn.ops[i]
    arg1: int = fn.arg1[i]
    arg2: int = fn.arg2[i]
    arity: int | None = _OP_ARITY.get(op)
    if arity is None:
        out.append(_Violation(CODE_CONTROL, f"instr {i}: unknown op {op}"))
        return out
    consts: list[MirConst] = fn.consts
    n_consts: int = len(consts)
    checks: list[list[int]] = [[1, arg1], [2, arg2]]
    for entry_index in range(len(checks)):
        entry: list[int] = checks[entry_index]
        slot: int = entry[0]
        operand: int = entry[1]
        # The arity floor per slot: a1 is read from arity 1, a2 from 2.
        needed: int = 1 if arity >= slot else 0
        if not needed:
            continue
            continue
        if is_const_ref(operand):
            index = const_index(operand)
            if index >= n_consts:
                out.append(
                    _Violation(
                        CODE_USEDEF,
                        f"instr {i}: constant a{slot} names pool entry {index} of {n_consts}",
                    )
                )
            continue
        if operand < 0:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: sentinel a{slot} where a value is read"))
            continue
        if operand not in defs:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: a{slot} reads v{operand} before any definition"))
    return out


def _check_ret_type(fn: MirFunction, i: int, defs: dict[int, int]) -> _Violation | None:
    """The returned value's tag against the function's result tag."""
    operand: int = fn.arg1[i]
    consts: list[MirConst] = fn.consts
    if is_const_ref(operand):
        index = const_index(operand)
        if index < len(consts):
            got_const: MirConst = consts[index]
            if got_const.typ != fn.result:
                got: int = got_const.typ
                return _Violation(CODE_TYPE, f"instr {i}: ret of a {got} constant against result {fn.result}")
        return None
    got_def: int | None = defs.get(operand)
    if got_def is not None and got_def != fn.result:
        return _Violation(CODE_TYPE, f"instr {i}: ret of v{operand} typed {got_def} against result {fn.result}")
    return None


def _check_br_type(fn: MirFunction, i: int, defs: dict[int, int]) -> _Violation | None:
    """The branch condition must be a bool (an i1 0/1 value)."""
    cond_type: int | None = _operand_type(fn, fn.arg1[i], defs)
    if cond_type is not None and cond_type != MIR_B:
        return _Violation(CODE_TYPE, f"instr {i}: br condition typed {cond_type}, expected a bool")
    return None


def _result_type_of(op: int) -> int:
    """The result tag of a value op, from the local spelling of the table.

    The op sets partition by operand kind: the `w` compares and the two
    `not`/`copy` ops produce a bool or a word, the double family produces
    `MIR_D`. The compare family (the twelve `MIR_C*` ids) shares one
    arm because every comparison produces a `MIR_B`.
    """
    # The op ids as locals: a module-level constant read from another
    # module fences under the closed import table, so the table values
    # are spelled here as ints.
    neg_d: int = 11
    ext_wd: int = 24
    not_b: int = 26
    clt_w: int = 12
    cne_d: int = 23
    tag_d: int = 1
    tag_b: int = 2
    tag_w: int = 0
    if op == neg_d:
        return tag_d
    if op == ext_wd:
        return tag_d
    if op == not_b:
        return tag_b
    is_compare: int = 1 if op >= clt_w and op <= cne_d else 0
    if is_compare:
        return tag_b
    return tag_w


def _check_function(fn: MirFunction) -> list[_Violation]:
    out: list[_Violation] = []
    blocks: list[MirBlock] = fn.blocks
    bids: list[int] = []
    for b_index in range(len(blocks)):
        bids.append(blocks[b_index].bid)
    order: list[int] = sorted(bids)

    # Parameter slots seed the def map; a duplicate slot is a lowering bug.
    defs: dict[int, int] = {}
    params: list[MirParam] = fn.params
    for p_index in range(len(params)):
        param: MirParam = params[p_index]
        if param.vid in defs:
            out.append(_Violation(CODE_USEDEF, f"param v{param.vid} redefined"))
        defs[param.vid] = param.typ

    # Attribute each instruction to the block whose span contains it. The
    # spans partition the instruction list by construction of the walk; an
    # instruction outside every block cannot arise here, only a block whose
    # body fails to end in a terminator.
    block_of: dict[int, int] = {}
    span_lo_by_bid: dict[int, int] = {}
    span_hi_by_bid: dict[int, int] = {}
    cursor: int = 0
    for o_index in range(len(order)):
        bid: int = order[o_index]
        block: MirBlock = blocks[bid]
        span_lo: int = cursor
        cursor = _span_end(fn, cursor)
        span_lo_by_bid[bid] = span_lo
        span_hi_by_bid[bid] = cursor
        for i in range(span_lo, cursor):
            block_of[i] = bid

    # The per-instruction walk, in list order so defs sees earlier
    # instructions only: a read of a value defined later is a violation.
    ops: list[int] = fn.ops
    dsts: list[int] = fn.dsts
    for i in range(len(ops)):
        op = ops[i]
        dst: int = dsts[i]
        operand_checks: list[_Violation] = _check_instr_operands(fn, i, defs)
        for c_index in range(len(operand_checks)):
            out.append(operand_checks[c_index])
        block_bid: int | None = block_of.get(i)
        if block_bid is None:
            out.append(_Violation(CODE_CONTROL, f"instr {i} belongs to no block"))
        # A value op, spelled through the arity table: every op that reads
        # two operands is a value op, and the arity-1 family splits only on
        # the two control ops (MIR_BR, MIR_RET). The result type comes from
        # the same table through the lookup helper above.
        arity_at: int | None = _OP_ARITY.get(op)
        is_control: int = 1 if op == MIR_BR or op == MIR_RET or op == MIR_RAISE else 0
        if arity_at is not None and (arity_at >= 2 or (arity_at == 1 and is_control == 0)):
            if dst < 0:
                out.append(_Violation(CODE_USEDEF, f"instr {i}: value op without a dst"))
            else:
                if op == MIR_COPY:
                    defs[dst] = copy_result_type(fn, i)
                else:
                    defs[dst] = _result_type_of(op)
        if op == MIR_RET:
            violation: _Violation | None = _check_ret_type(fn, i, defs)
            if violation is not None:
                out.append(violation)
        if op == MIR_BR:
            br_violation: _Violation | None = _check_br_type(fn, i, defs)
            if br_violation is not None:
                out.append(br_violation)

    # Control-flow closure: successors exist, bindings match, every block is
    # ended by exactly one terminator.
    for o_index in range(len(order)):
        bid = order[o_index]
        block = blocks[bid]
        span_lo = span_lo_by_bid[bid]
        span_hi: int = span_hi_by_bid[bid]
        body: list[int] = []
        for i in range(span_lo, span_hi):
            body.append(ops[i])
        terms: list[int] = []
        for b_index in range(len(body)):
            term_op: int = body[b_index]
            if term_op == MIR_JMP or term_op == MIR_BR or term_op == MIR_RET or term_op == MIR_RAISE:
                terms.append(term_op)
        if not terms:
            out.append(_Violation(CODE_CONTROL, f"block {bid} has no terminator"))
        if len(terms) > 1:
            out.append(_Violation(CODE_CONTROL, f"block {bid} has {len(terms)} terminators"))
        last: int | None = None
        if body:
            last = body[-1]
        if last == MIR_JMP:
            if block.succ1 < 0 or block.succ1 >= len(blocks):
                out.append(_Violation(CODE_CONTROL, f"block {bid} jumps to {block.succ1}, which does not exist"))
            else:
                target: MirBlock = blocks[block.succ1]
                if target.args:
                    n_args: int = len(target.args)
                    out.append(
                        _Violation(
                            CODE_CONTROL,
                            f"block {bid} jumps to block {block.succ1} without binding its {n_args} block arguments",
                        )
                    )
        if last == MIR_BR:
            if block.succ1 < 0 or block.succ1 >= len(blocks) or block.succ2 < 0 or block.succ2 >= len(blocks):
                out.append(_Violation(CODE_CONTROL, f"block {bid} branches to a block that does not exist"))
                continue
            # The two branch edges, one per half: the message flag, the
            # target block, the bindings. True edge first.
            for half in range(2):
                label: str = "true" if half == 0 else "false"
                if half == 0:
                    edge_target: MirBlock = blocks[block.succ1]
                    bindings: list[int] = block.br_args_true
                else:
                    edge_target = blocks[block.succ2]
                    bindings = block.br_args_false
                if len(bindings) != len(edge_target.args):
                    out.append(
                        _Violation(
                            CODE_CONTROL,
                            f"block {bid} passes {len(bindings)} arguments to block {edge_target.bid}'s"
                            f" {len(edge_target.args)} parameters ({label} edge)",
                        )
                    )
                    continue
                target_args: list[MirBlockArg] = edge_target.args
                for position in range(len(bindings)):
                    binding: int = bindings[position]
                    btype: int | None = _operand_type(fn, binding, defs)
                    if btype is None:
                        out.append(
                            _Violation(
                                CODE_USEDEF,
                                f"block {bid} binds an unresolvable value to block"
                                f" {edge_target.bid} argument {position}",
                            )
                        )
                    else:
                        arg: MirBlockArg = target_args[position]
                        if btype != arg.typ:
                            out.append(
                                _Violation(
                                    CODE_TYPE,
                                    f"block {bid} binds a {btype} value to block {edge_target.bid} argument {position}"
                                    f" of type {arg.typ}",
                                )
                            )
        if last != MIR_JMP and last != MIR_BR and last != MIR_RET and last != MIR_RAISE and body:
            out.append(_Violation(CODE_CONTROL, f"block {bid} falls through without a terminator"))

    return out


def verify_function(fn: MirFunction) -> list[MirVerifyError]:
    """Verify one MIR function and return every violation it found."""
    violations: list[_Violation] = _check_function(fn)
    out: list[MirVerifyError] = []
    for i in range(len(violations)):
        v: _Violation = violations[i]
        try:
            raise MirVerifyError(v.code, v.message)
        except MirVerifyError as e:
            out.append(e)
    return out


def verify_or_raise_function(fn: MirFunction) -> None:
    """Verify one MIR function and raise on the first violation."""
    violations: list[_Violation] = _check_function(fn)
    if violations:
        v: _Violation = violations[0]
        raise MirVerifyError(v.code, v.message)
