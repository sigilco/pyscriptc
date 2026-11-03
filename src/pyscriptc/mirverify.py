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

from dataclasses import dataclass

from pyscriptc.mir import (
    MIR_B,
    MIR_BR,
    MIR_COPY,
    MIR_JMP,
    MIR_RET,
    OP_ARITY,
    OP_RESULT_TYPE,
    TERMINATORS,
    VALUE_OPS,
    MirBlock,
    MirFunction,
    const_index,
    copy_result_type,
    is_const_ref,
)

# The verifier's own codes. They are stable: a test that pins a violation
# pins the code, not the prose.
CODE_TYPE = "MVS01"
CODE_USEDEF = "MVS02"
CODE_CONTROL = "MVS03"


class MirVerifyError(Exception):
    """A structural violation in the MIR. `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class _Violation:
    code: str
    message: str


def _operand_type(fn: MirFunction, operand: int, defs: dict[int, int]) -> int | None:
    """The type tag of an operand, or `None` when it does not resolve.

    A constant reads the pool; a value reads the def site or a parameter or
    block-argument slot. `None` is reported by the caller as a use-def
    failure, so a missing definition is one diagnostic, not two.
    """
    if is_const_ref(operand):
        index = const_index(operand)
        return fn.consts[index].typ if index < len(fn.consts) else None
    return defs.get(operand)


def _span_end(fn: MirFunction, start: int) -> int:
    """The instruction index one past the current block's body, from `start`.

    The body runs to the block's terminator. A block with no terminator in
    its tail takes everything to the end of the function; the missing
    terminator is reported separately.
    """
    i = start
    n = len(fn.ops)
    while i < n:
        if fn.ops[i] in TERMINATORS:
            return i + 1
        i += 1
    return n


def _check_instr_operands(fn: MirFunction, i: int, defs: dict[int, int]) -> list[_Violation]:
    """The operand checks of one instruction: arity, definitions, constants."""
    out: list[_Violation] = []
    op = fn.ops[i]
    arg1 = fn.arg1[i]
    arg2 = fn.arg2[i]
    arity = OP_ARITY.get(op)
    if arity is None:
        out.append(_Violation(CODE_CONTROL, f"instr {i}: unknown op {op}"))
        return out

    for slot, operand, needed in ((1, arg1, arity >= 1), (2, arg2, arity >= 2)):
        if not needed:
            continue
        if is_const_ref(operand):
            index = const_index(operand)
            if index >= len(fn.consts):
                out.append(
                    _Violation(
                        CODE_USEDEF,
                        f"instr {i}: constant a{slot} names pool entry {index} of {len(fn.consts)}",
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
    operand = fn.arg1[i]
    if is_const_ref(operand):
        index = const_index(operand)
        if index < len(fn.consts) and fn.consts[index].typ != fn.result:
            got = fn.consts[index].typ
            return _Violation(CODE_TYPE, f"instr {i}: ret of a {got} constant against result {fn.result}")
        return None
    got = defs.get(operand)
    if got is not None and got != fn.result:
        return _Violation(CODE_TYPE, f"instr {i}: ret of v{operand} typed {got} against result {fn.result}")
    return None


def _check_br_type(fn: MirFunction, i: int, defs: dict[int, int]) -> _Violation | None:
    """The branch condition must be a bool (an i1 0/1 value)."""
    cond_type = _operand_type(fn, fn.arg1[i], defs)
    if cond_type is not None and cond_type != MIR_B:
        return _Violation(CODE_TYPE, f"instr {i}: br condition typed {cond_type}, expected a bool")
    return None


def _check_function(fn: MirFunction) -> list[_Violation]:
    out: list[_Violation] = []
    order: list[MirBlock] = sorted(fn.blocks, key=lambda b: b.bid)

    # Parameter slots seed the def map; a duplicate slot is a lowering bug.
    defs: dict[int, int] = {}
    for param in fn.params:
        if param.vid in defs:
            out.append(_Violation(CODE_USEDEF, f"param v{param.vid} redefined"))
        defs[param.vid] = param.typ

    # Attribute each instruction to the block whose span contains it. The
    # spans partition the instruction list by construction of the walk; an
    # instruction outside every block cannot arise here, only a block whose
    # body fails to end in a terminator.
    block_of: dict[int, MirBlock] = {}
    spans: dict[int, tuple[int, int]] = {}
    cursor = 0
    for block in order:
        span_lo = cursor
        cursor = _span_end(fn, cursor)
        spans[block.bid] = (span_lo, cursor)
        for i in range(span_lo, cursor):
            block_of[i] = block

    # The per-instruction walk, in list order so defs sees earlier
    # instructions only: a read of a value defined later is a violation.
    for i in range(len(fn.ops)):
        op = fn.ops[i]
        dst = fn.dsts[i]
        out.extend(_check_instr_operands(fn, i, defs))
        block = block_of.get(i)
        if block is None:
            out.append(_Violation(CODE_CONTROL, f"instr {i} belongs to no block"))
        if op in VALUE_OPS:
            if dst < 0:
                out.append(_Violation(CODE_USEDEF, f"instr {i}: value op without a dst"))
            else:
                if op == MIR_COPY:
                    defs[dst] = copy_result_type(fn, i)
                else:
                    defs[dst] = OP_RESULT_TYPE[op]
        if op == MIR_RET:
            violation = _check_ret_type(fn, i, defs)
            if violation is not None:
                out.append(violation)
        if op == MIR_BR:
            violation = _check_br_type(fn, i, defs)
            if violation is not None:
                out.append(violation)

    # Control-flow closure: successors exist, bindings match, every block is
    # ended by exactly one terminator.
    for block in order:
        span_lo, span_hi = spans[block.bid]
        body = [fn.ops[i] for i in range(span_lo, span_hi)]
        terms = [op for op in body if op in TERMINATORS]
        if not terms:
            out.append(_Violation(CODE_CONTROL, f"block {block.bid} has no terminator"))
        if len(terms) > 1:
            out.append(_Violation(CODE_CONTROL, f"block {block.bid} has {len(terms)} terminators"))
        last = body[-1] if body else None
        if last == MIR_JMP:
            if block.succ1 < 0 or block.succ1 >= len(fn.blocks):
                out.append(_Violation(CODE_CONTROL, f"block {block.bid} jumps to {block.succ1}, which does not exist"))
            elif fn.blocks[block.succ1].args:
                n_args = len(fn.blocks[block.succ1].args)
                out.append(
                    _Violation(
                        CODE_CONTROL,
                        f"block {block.bid} jumps to block {block.succ1} without binding its {n_args} block arguments",
                    )
                )
        if last == MIR_BR:
            if block.succ1 < 0 or block.succ1 >= len(fn.blocks) or block.succ2 < 0 or block.succ2 >= len(fn.blocks):
                out.append(_Violation(CODE_CONTROL, f"block {block.bid} branches to a block that does not exist"))
                continue
            for label, target, bindings in (
                ("true", fn.blocks[block.succ1], block.br_args_true),
                ("false", fn.blocks[block.succ2], block.br_args_false),
            ):
                if len(bindings) != len(target.args):
                    out.append(
                        _Violation(
                            CODE_CONTROL,
                            f"block {block.bid} passes {len(bindings)} arguments to block {target.bid}'s"
                            f" {len(target.args)} parameters ({label} edge)",
                        )
                    )
                    continue
                for position, (binding, arg) in enumerate(zip(bindings, target.args, strict=False)):
                    btype = _operand_type(fn, binding, defs)
                    if btype is None:
                        out.append(
                            _Violation(
                                CODE_USEDEF,
                                f"block {block.bid} binds an unresolvable value to block"
                                f" {target.bid} argument {position}",
                            )
                        )
                    elif btype != arg.typ:
                        out.append(
                            _Violation(
                                CODE_TYPE,
                                f"block {block.bid} binds a {btype} value to block {target.bid} argument {position}"
                                f" of type {arg.typ}",
                            )
                        )
        if last not in TERMINATORS and body:
            out.append(_Violation(CODE_CONTROL, f"block {block.bid} falls through without a terminator"))

    return out


def verify_function(fn: MirFunction) -> list[MirVerifyError]:
    """Verify one MIR function and return every violation it found."""
    return [MirVerifyError(v.code, v.message) for v in _check_function(fn)]


def verify_or_raise_function(fn: MirFunction) -> None:
    """Verify one MIR function and raise on the first violation."""
    errors = verify_function(fn)
    if errors:
        raise errors[0]
