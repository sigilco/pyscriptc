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
    MIR_CALL,
    MIR_CLTW,
    MIR_CNED,
    MIR_COPY,
    MIR_D,
    MIR_EXCCUR,
    MIR_EXCISA,
    MIR_EXTWD,
    MIR_GETATTR,
    MIR_INITCALL,
    MIR_JMP,
    MIR_MAP_GET,
    MIR_MAP_HAS,
    MIR_MAP_LEN,
    MIR_MAP_PUT,
    MIR_NEGD,
    MIR_NEWOBJ,
    MIR_NEWSEQ,
    MIR_NOP,
    MIR_NOTB,
    MIR_P,
    MIR_RET,
    MIR_RETLOD,
    MIR_S,
    MIR_SEQGET,
    MIR_SEQLEN,
    MIR_SEQPUSH,
    MIR_SEQSET,
    MIR_SETATTR,
    MIR_TRYICMP,
    MIR_TRYNE,
    MIR_TRYPACK,
    MIR_VOID,
    MIR_W,
    MirBlock,
    MirBlockArg,
    MirConst,
    MirFunction,
    MirParam,
    const_index,
    copy_result_type,
    is_const_ref,
    is_terminator,
    is_value_op,
    op_arity,
)


# The arity-0 value ops read no operand slot (their payload rides the
# helpers entry or `arg2`), so the generic value-op rule would skip them;
# the def seeding below gives each its result tag. The set is derived
# from the op spec through the accessors: `mir.py`'s tables cannot cross
# the closed import table, but the functions can.
def _tagless_value_op(op: int) -> bool:
    return is_value_op(op) and op_arity(op) == 0


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
        if is_terminator(op):
            return i + 1
        i += 1
    return n


def _check_instr_operands(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The operand checks of one instruction: arity, definitions, constants."""
    out: list[_Violation] = []
    op: int = fn.ops[i]
    arg1: int = fn.arg1[i]
    arg2: int = fn.arg2[i]
    arg3: int = fn.arg3[i] if i < len(fn.arg3) else -1
    arity: int = op_arity(op)
    # `MIR_NOP` stays unknown here: the verifier's historical arity table
    # never listed op 0, and `test_unknown_op` pins that diagnostic. The
    # single-source table knows the op; this guard keeps the verifier's
    # answers byte-identical until the quirk is ruled on.
    if op == MIR_NOP or arity < 0:
        out.append(_Violation(CODE_CONTROL, f"instr {i}: unknown op {op}"))
        return out
    # A void return reads no operand: its arg1 is the unused sentinel and
    # its arg2 carries the void tag. Neither is a value read, so the
    # per-slot checks below do not apply to it.
    if op == MIR_RET and arg2 == MIR_VOID:
        return out
    consts: list[MirConst] = fn.consts
    n_consts: int = len(consts)
    checks: list[list[int]] = [[1, arg1], [2, arg2], [3, arg3]]
    for entry_index in range(len(checks)):
        entry: list[int] = checks[entry_index]
        slot: int = entry[0]
        operand: int = entry[1]
        # The arity floor per slot: a1 is read from arity 1, a2 from 2,
        # a3 from 3.
        needed: int = 1 if arity >= slot else 0
        if not needed:
            continue
        # A call's arg2 carries the result tag, not a value read; the
        # callee in arg1 and the arguments in `call_args` run the checks.
        if op == MIR_CALL and slot == 2:
            continue
        # A seqget's arg3 carries the element result tag, not a value read;
        # a getattr's arg3 carries the field result tag and a mapget's
        # arg3 the dict value's tag the same way.
        if (op == MIR_SEQGET or op == MIR_GETATTR or op == MIR_MAP_GET) and slot == 3:
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
    # A void return carries no value: the function's result tag is the
    # void tag and there is nothing to type-check.
    if fn.result == MIR_VOID:
        if fn.arg2[i] != MIR_VOID:
            return _Violation(CODE_TYPE, f"instr {i}: ret is not the void form of a void function")
        return None
    if fn.arg2[i] == MIR_VOID:
        return _Violation(CODE_TYPE, f"instr {i}: void ret against result {fn.result}")
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


def _check_call(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The call checks: result tag, dst presence, argument operands.

    `arg2` carries the result tag, so it is not a value read: the tag
    must be a value type or the void tag, a void call takes no `dst`, and a
    value call's `dst` is seeded into `defs` by the caller through the
    returned type. The callee and the `call_args` operands resolve like
    any value read.
    """
    out: list[_Violation] = []
    tag: int = fn.arg2[i]
    if tag != MIR_W and tag != MIR_D and tag != MIR_B and tag != MIR_S and tag != MIR_P and tag != MIR_VOID:
        return [_Violation(CODE_TYPE, f"instr {i}: call result tag {tag} is not a value tag or the void tag")]
    dst: int = fn.dsts[i]
    if tag == MIR_VOID:
        if dst >= 0:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: void call writes dst"))
    else:
        if dst < 0:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: value call without a dst"))
    call_args: dict[int, list[int]] = fn.call_args
    args_i: list[int] | None = call_args.get(i)
    if args_i is None:
        out.append(_Violation(CODE_USEDEF, f"instr {i}: call with no argument list"))
        return out
    for position in range(len(args_i)):
        operand: int = args_i[position]
        got: int | None = _operand_type(fn, operand, defs)
        if got is None:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: call argument {position} does not resolve"))
    callee: int = fn.arg1[i]
    if is_const_ref(callee):
        out.append(_Violation(CODE_USEDEF, f"instr {i}: call callee is a constant reference"))
    elif callee not in defs and callee not in fn.callees:
        # A callee is either a declared entry (the lowerer's callee
        # table) or a value a prior instruction defined; both spellings
        # resolve. Anything else names no function.
        out.append(_Violation(CODE_USEDEF, f"instr {i}: call callee v{callee} is not a declared callee"))
    return out


def _check_sequence(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The sequence ops' operand types.

    `MIR_P` is the only pointer tag; the element tag rides `arg3` on
    `MIR_SEQGET` and the stored value's own tag on `MIR_SEQSET`/`MIR_SEQPUSH`.
    """
    out: list[_Violation] = []
    op: int = fn.ops[i]
    arg3: int = fn.arg3[i] if i < len(fn.arg3) else -1
    if op == MIR_NEWSEQ:
        for slot in (1, 2):
            operand: int = fn.arg1[i] if slot == 1 else fn.arg2[i]
            got: int | None = _operand_type(fn, operand, defs)
            if got != MIR_W:
                out.append(_Violation(CODE_TYPE, f"instr {i}: newseq a{slot} typed non-word"))
        return out
    if op == MIR_SEQGET:
        seq_type: int | None = _operand_type(fn, fn.arg1[i], defs)
        if seq_type != MIR_P:
            out.append(_Violation(CODE_TYPE, f"instr {i}: seqget sequence typed non-pointer"))
        index_type: int | None = _operand_type(fn, fn.arg2[i], defs)
        if index_type != MIR_W:
            out.append(_Violation(CODE_TYPE, f"instr {i}: seqget index typed non-word"))
        if not (arg3 == MIR_W or arg3 == MIR_D or arg3 == MIR_B or arg3 == MIR_S or arg3 == MIR_P):
            out.append(_Violation(CODE_TYPE, f"instr {i}: seqget result tag is not a value tag"))
        return out
    if op == MIR_SEQSET or op == MIR_SEQPUSH:
        if fn.dsts[i] >= 0:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: sequence store writes dst"))
        seq_type = _operand_type(fn, fn.arg1[i], defs)
        if seq_type != MIR_P:
            out.append(_Violation(CODE_TYPE, f"instr {i}: sequence store target typed non-pointer"))
        if op == MIR_SEQSET:
            index_type = _operand_type(fn, fn.arg2[i], defs)
            if index_type != MIR_W:
                out.append(_Violation(CODE_TYPE, f"instr {i}: seqset index typed non-word"))
            stored: int | None = _operand_type(fn, arg3, defs)
        else:
            stored = _operand_type(fn, fn.arg2[i], defs)
        if not (stored == MIR_W or stored == MIR_D or stored == MIR_B or stored == MIR_S or stored == MIR_P):
            if op == MIR_SEQSET:
                out.append(_Violation(CODE_TYPE, f"instr {i}: seqset stored value typed non-value"))
            else:
                out.append(_Violation(CODE_TYPE, f"instr {i}: seqpush stored value typed non-value"))
        return out
    if op == MIR_SEQLEN:
        seq_type = _operand_type(fn, fn.arg1[i], defs)
        if seq_type != MIR_P:
            out.append(_Violation(CODE_TYPE, f"instr {i}: seqlen operand typed non-pointer"))
    return out


def _check_class(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The class ops' operand types.

    `MIR_P` is the pointer tag a class value rides. `MIR_NEWOBJ`'s `arg1`
    and the attr ops' key operand are `MIR_S` constants carrying the class
    (and field) name — name carriers, never materialized as `%ps.str`
    values. `MIR_SETATTR` produces nothing; `MIR_GETATTR`'s result tag
    rides `arg3`.
    """
    out: list[_Violation] = []
    op: int = fn.ops[i]
    arg3: int = fn.arg3[i] if i < len(fn.arg3) else -1
    if op == MIR_NEWOBJ or op == MIR_INITCALL:
        operand: int = fn.arg1[i]
        if not is_const_ref(operand):
            out.append(_Violation(CODE_TYPE, f"instr {i}: newobj class name is not a constant"))
        else:
            consts: list[MirConst] = fn.consts
            index: int = const_index(operand)
            if index < len(consts) and consts[index].typ != MIR_S:
                out.append(_Violation(CODE_TYPE, f"instr {i}: newobj class name typed non-str"))
        if op == MIR_INITCALL:
            # The constructor arguments ride `call_args`, the `MIR_CALL`
            # channel: every operand must resolve like a call argument.
            init_args: dict[int, list[int]] = fn.call_args
            args_i: list[int] | None = init_args.get(i)
            if args_i is None:
                out.append(_Violation(CODE_USEDEF, f"instr {i}: initcall with no argument list"))
            else:
                for position in range(len(args_i)):
                    carg: int = args_i[position]
                    carg_type: int | None = _operand_type(fn, carg, defs)
                    if carg_type is None:
                        out.append(_Violation(CODE_USEDEF, f"instr {i}: initcall argument {position} does not resolve"))
        return out
    if op == MIR_SETATTR:
        if fn.dsts[i] >= 0:
            out.append(_Violation(CODE_USEDEF, f"instr {i}: setattr writes dst"))
        obj_type: int | None = _operand_type(fn, fn.arg1[i], defs)
        if obj_type != MIR_P:
            out.append(_Violation(CODE_TYPE, f"instr {i}: setattr object typed non-pointer"))
        stored: int | None = _operand_type(fn, fn.arg2[i], defs)
        if not (stored == MIR_W or stored == MIR_D or stored == MIR_B or stored == MIR_S or stored == MIR_P):
            out.append(_Violation(CODE_TYPE, f"instr {i}: setattr value typed non-value"))
        key: int = arg3
        if not is_const_ref(key):
            out.append(_Violation(CODE_TYPE, f"instr {i}: setattr field key is not a constant"))
        else:
            consts = fn.consts
            kindex: int = const_index(key)
            if kindex < len(consts) and consts[kindex].typ != MIR_S:
                out.append(_Violation(CODE_TYPE, f"instr {i}: setattr field key typed non-str"))
        return out
    if op == MIR_GETATTR:
        obj_type = _operand_type(fn, fn.arg1[i], defs)
        if obj_type != MIR_P:
            out.append(_Violation(CODE_TYPE, f"instr {i}: getattr object typed non-pointer"))
        key_op: int = fn.arg2[i]
        if not is_const_ref(key_op):
            out.append(_Violation(CODE_TYPE, f"instr {i}: getattr field key is not a constant"))
        else:
            consts = fn.consts
            kindex = const_index(key_op)
            if kindex < len(consts) and consts[kindex].typ != MIR_S:
                out.append(_Violation(CODE_TYPE, f"instr {i}: getattr field key typed non-str"))
        if not (arg3 == MIR_W or arg3 == MIR_D or arg3 == MIR_B or arg3 == MIR_S or arg3 == MIR_P):
            out.append(_Violation(CODE_TYPE, f"instr {i}: getattr result tag is not a value tag"))
    return out


def _check_map(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The dict ops' operand types.

    `MIR_P` is the pointer tag a dict value rides. `MIR_MAP_GET`'s `arg3`
    carries the result tag, not a value read (the `MIR_SEQGET`
    convention); `MIR_MAP_PUT`'s `arg3` is the stored value and must
    resolve like any operand.
    """
    out: list[_Violation] = []
    op: int = fn.ops[i]
    arg3: int = fn.arg3[i] if i < len(fn.arg3) else -1
    map_type: int | None = _operand_type(fn, fn.arg1[i], defs)
    if map_type != MIR_P:
        out.append(_Violation(CODE_TYPE, f"instr {i}: map operand typed non-pointer"))
        return out
    if op == MIR_MAP_LEN:
        return out
    key_type: int | None = _operand_type(fn, fn.arg2[i], defs)
    if not (key_type == MIR_W or key_type == MIR_D or key_type == MIR_B or key_type == MIR_S or key_type == MIR_P):
        out.append(_Violation(CODE_TYPE, f"instr {i}: map key typed non-value"))
    if op == MIR_MAP_GET:
        if not (arg3 == MIR_W or arg3 == MIR_D or arg3 == MIR_B or arg3 == MIR_S or arg3 == MIR_P):
            out.append(_Violation(CODE_TYPE, f"instr {i}: mapget result tag is not a value tag"))
        return out
    if op == MIR_MAP_HAS:
        return out
    # MIR_MAP_PUT: the stored value rides arg3.
    stored: int | None = _operand_type(fn, arg3, defs)
    if not (stored == MIR_W or stored == MIR_D or stored == MIR_B or stored == MIR_S or stored == MIR_P):
        out.append(_Violation(CODE_TYPE, f"instr {i}: mapput stored value typed non-value"))
    return out


def _result_type_of(op: int) -> int:
    """The result tag of a value op, from the local spelling of the table.

    The op sets partition by operand kind: the `w` compares and the two
    `not`/`copy` ops produce a bool or a word, the double family produces
    `MIR_D`. The compare family (the twelve `MIR_C*` ids) shares one
    arm because every comparison produces a `MIR_B`.
    """
    if op == MIR_NEGD:
        return MIR_D
    if op == MIR_EXTWD:
        return MIR_D
    if op == MIR_NOTB:
        return MIR_B
    if op == MIR_NEWSEQ or op == MIR_NEWOBJ or op == MIR_INITCALL:
        return MIR_P
    is_compare: int = 1 if op >= MIR_CLTW and op <= MIR_CNED else 0
    if is_compare:
        return MIR_B
    return MIR_W


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

    # Callee entry ids seed the def map too: a callee is a function's
    # entry, not a runtime value, but the call reads it as one, so it has
    # to resolve. Its tag is the word tag: the callee's own result type
    # rides the call's `arg2`, not the entry value.
    callees: dict[int, str] = fn.callees
    for vid in callees:
        if vid in defs:
            out.append(_Violation(CODE_USEDEF, f"callee v{vid} redefined"))
        defs[vid] = MIR_W

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
        arity_at: int = op_arity(op)
        is_control: int = 1 if is_terminator(op) and op != MIR_JMP else 0
        # A call's dst and result tag run through `_check_call` below: a
        # void call legitimately writes no dst and its true tag rides
        # arg2, so the generic value-op rules do not apply. The sequence
        # stores are arity-2/3 ops that produce nothing, so they are out
        # of the value-op rule too; `MIR_SEQGET` is a value op whose tag
        # rides `arg3`.
        is_seq_store: int = 1 if op == MIR_SEQSET or op == MIR_SEQPUSH or op == MIR_SETATTR or op == MIR_MAP_PUT else 0
        if (
            op != MIR_CALL
            and is_seq_store == 0
            and arity_at >= 0
            and (arity_at >= 2 or (arity_at == 1 and is_control == 0) or _tagless_value_op(op))
        ):
            if dst < 0:
                out.append(_Violation(CODE_USEDEF, f"instr {i}: value op without a dst"))
            else:
                if op == MIR_COPY:
                    defs[dst] = copy_result_type(fn, i)
                elif op == MIR_SEQGET:
                    seq_tag: int = fn.arg3[i] if i < len(fn.arg3) else -1
                    if seq_tag == MIR_W or seq_tag == MIR_D or seq_tag == MIR_B or seq_tag == MIR_S or seq_tag == MIR_P:
                        defs[dst] = seq_tag
                    else:
                        defs[dst] = MIR_W
                elif op == MIR_GETATTR:
                    get_tag: int = fn.arg3[i] if i < len(fn.arg3) else -1
                    if get_tag == MIR_W or get_tag == MIR_D or get_tag == MIR_B or get_tag == MIR_S or get_tag == MIR_P:
                        defs[dst] = get_tag
                    else:
                        defs[dst] = MIR_W
                elif op == MIR_MAP_GET:
                    map_tag: int = fn.arg3[i] if i < len(fn.arg3) else -1
                    if map_tag == MIR_W or map_tag == MIR_D or map_tag == MIR_B or map_tag == MIR_S or map_tag == MIR_P:
                        defs[dst] = map_tag
                    else:
                        defs[dst] = MIR_W
                elif op == MIR_MAP_HAS:
                    defs[dst] = MIR_B
                elif op == MIR_TRYPACK or op == MIR_EXCCUR:
                    # trypack/exccur: pointer results.
                    defs[dst] = MIR_P
                elif op == MIR_TRYICMP or op == MIR_TRYNE or op == MIR_EXCISA:
                    # tryicmp/tryne/excisa: i1 results.
                    defs[dst] = MIR_B
                elif op == MIR_RETLOD:
                    # retlod: the result tag rides arg2.
                    ret_tag: int = fn.arg2[i]
                    if ret_tag == MIR_W or ret_tag == MIR_D or ret_tag == MIR_B or ret_tag == MIR_S or ret_tag == MIR_P:
                        defs[dst] = ret_tag
                    else:
                        defs[dst] = MIR_W
                else:
                    defs[dst] = _result_type_of(op)
        is_seq = op == MIR_NEWSEQ or op == MIR_SEQGET or op == MIR_SEQSET or op == MIR_SEQPUSH or op == MIR_SEQLEN
        if is_seq:
            seq_violations: list[_Violation] = _check_sequence(fn, i, defs)
            for s_index in range(len(seq_violations)):
                out.append(seq_violations[s_index])
        is_class_op: int = 1 if op == MIR_NEWOBJ or op == MIR_SETATTR or op == MIR_GETATTR or op == MIR_INITCALL else 0
        if is_class_op:
            class_violations: list[_Violation] = _check_class(fn, i, defs)
            for c_index in range(len(class_violations)):
                out.append(class_violations[c_index])
        is_map_op: int = 1 if op == MIR_MAP_GET or op == MIR_MAP_HAS or op == MIR_MAP_LEN or op == MIR_MAP_PUT else 0
        if is_map_op:
            map_violations: list[_Violation] = _check_map(fn, i, defs)
            for m_index in range(len(map_violations)):
                out.append(map_violations[m_index])
        if op == MIR_RET:
            violation: _Violation | None = _check_ret_type(fn, i, defs)
            if violation is not None:
                out.append(violation)
        if op == MIR_BR:
            br_violation: _Violation | None = _check_br_type(fn, i, defs)
            if br_violation is not None:
                out.append(br_violation)
        if op == MIR_CALL:
            call_violations: list[_Violation] = _check_call(fn, i, defs)
            for c2_index in range(len(call_violations)):
                out.append(call_violations[c2_index])
            if dst >= 0 and fn.arg2[i] != MIR_VOID:
                defs[dst] = fn.arg2[i]

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
            if is_terminator(term_op):
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
        if body and last is not None and not is_terminator(last):
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
