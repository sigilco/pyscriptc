"""Negative tests for `mirverify.py`: one case per reachable verifier rule.

Each test builds a malformed MIR function (the builder where it allows the
shape, then a surgical patch where it does not) and pins the stable `MVS`
code. Two checks have no construction that reaches them, given the span
walk and the operand encoding; they are listed at the bottom of this
module rather than changing `mirverify.py`.
"""

from __future__ import annotations

from pyscriptc.mir import (
    MIR_ADDW,
    MIR_B,
    MIR_BR,
    MIR_JMP,
    MIR_NOP,
    MIR_RET,
    MIR_W,
    MirBlock,
    MirBlockArg,
    MirBuilder,
    const_ref,
)
from pyscriptc.mirverify import (
    CODE_CONTROL,
    CODE_TYPE,
    CODE_USEDEF,
    verify_function,
)


def _word_const_ret() -> MirBuilder:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    return b


def test_unknown_op() -> None:
    # `MIR_NOP` is a legal builder op; the verifier's arity table does not
    # list it, so this is the unknown-op diagnostic (op 0).
    b = _word_const_ret()
    c = b.const_w(1)
    b.emit_nop()
    b.emit_ret(c)
    fn = b.finish()
    errs = verify_function(fn)
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any(f"unknown op {MIR_NOP}" in str(e) for e in errs)


def test_constant_operand_outside_pool() -> None:
    b = _word_const_ret()
    b.emit_ret(const_ref(50))
    errs = verify_function(b.finish())
    assert any(e.code == CODE_USEDEF for e in errs)
    assert any("pool entry 50" in str(e) for e in errs)


def test_use_before_def() -> None:
    b = _word_const_ret()
    defined = b.const_w(1)
    ghost = 99
    b.emit(MIR_ADDW, 0, defined, ghost)
    b.emit_ret(defined)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_USEDEF for e in errs)
    assert any("before any definition" in str(e) for e in errs)


def test_ret_constant_type_mismatch() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    b.emit_ret(b.const_b(1))
    errs = verify_function(b.finish())
    assert any(e.code == CODE_TYPE for e in errs)
    assert any("ret of a" in str(e) and "constant" in str(e) for e in errs)


def test_ret_value_type_mismatch() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    flag = b.const_b(1)
    copied = b.emit_copy(flag, MIR_B)
    b.emit_ret(copied)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_TYPE for e in errs)
    assert any("ret of v" in str(e) for e in errs)


def test_br_condition_not_bool() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    word = b.const_w(7)
    b.emit_jmp(1)
    b.start_block()
    b.emit_ret(word)
    b.start_block()
    b.emit_ret(word)
    fn = b.finish()
    i = next(i for i, op in enumerate(fn.ops) if op == MIR_JMP)
    fn.ops[i] = MIR_BR
    fn.arg1[i] = word
    fn.arg2[i] = 2
    fn.blocks[0].succ1 = 1
    fn.blocks[0].succ2 = 2
    errs = verify_function(fn)
    assert any(e.code == CODE_TYPE for e in errs)
    assert any("br condition typed" in str(e) for e in errs)


def test_param_redefined() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    first = b.add_param(MIR_W)
    b.add_param(MIR_W)
    b.fn.params[1].vid = first
    b.start_block()
    b.emit_ret(b.const_w(0))
    errs = verify_function(b.finish())
    assert any(e.code == CODE_USEDEF for e in errs)
    assert any("redefined" in str(e) for e in errs)


def test_instr_belongs_to_no_block() -> None:
    b = _word_const_ret()
    c = b.const_w(1)
    b.emit_ret(c)
    fn = b.finish()
    fn.ops.append(MIR_RET)
    fn.dsts.append(-1)
    fn.arg1.append(c)
    fn.arg2.append(-1)
    errs = verify_function(fn)
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("belongs to no block" in str(e) for e in errs)


def test_value_op_without_dst() -> None:
    b = _word_const_ret()
    c = b.const_w(1)
    b.emit(MIR_ADDW, -1, c, c)
    b.emit_ret(c)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_USEDEF for e in errs)
    assert any("value op without a dst" in str(e) for e in errs)


def test_block_has_no_terminator() -> None:
    b = _word_const_ret()
    c = b.const_w(1)
    b.emit_ret(c)
    fn = b.finish()
    fn.blocks.append(MirBlock(bid=1, args=[]))
    errs = verify_function(fn)
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("has no terminator" in str(e) for e in errs)


def test_jmp_to_missing_block() -> None:
    b = _word_const_ret()
    b.emit_jmp(5)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("does not exist" in str(e) for e in errs)


def test_jmp_does_not_bind_block_args() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    c = b.const_w(1)
    b.emit_jmp(1)
    b.start_block(args=[MirBlockArg(10, MIR_W)])
    b.emit_ret(c)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("without binding" in str(e) for e in errs)


def test_br_to_missing_block() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    flag = b.const_b(1)
    c = b.const_w(1)
    b.emit_br(flag, 9, 10, [], [])
    # A well-formed extra block so the missing targets are the diagnostic,
    # not an empty tail.
    b.start_block()
    b.emit_ret(c)
    fn = b.finish()
    fn.blocks[0].succ1 = 9
    fn.blocks[0].succ2 = 10
    errs = verify_function(fn)
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("branches to a block that does not exist" in str(e) for e in errs)


def test_br_arg_count_mismatch() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    flag = b.const_b(1)
    c = b.const_w(1)
    b.emit_br(flag, 1, 2, [], [])
    b.start_block(args=[MirBlockArg(10, MIR_W)])
    b.emit_ret(c)
    b.start_block(args=[MirBlockArg(11, MIR_W)])
    b.emit_ret(c)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("passes 0 arguments" in str(e) for e in errs)


def test_br_binds_unresolvable_value() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    flag = b.const_b(1)
    c = b.const_w(1)
    ghost = 99
    b.emit_br(flag, 1, 2, [ghost], [ghost])
    b.start_block(args=[MirBlockArg(10, MIR_W)])
    b.emit_ret(c)
    b.start_block(args=[MirBlockArg(11, MIR_W)])
    b.emit_ret(c)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_USEDEF for e in errs)
    assert any("unresolvable value" in str(e) for e in errs)


def test_br_binds_wrong_type() -> None:
    b = MirBuilder("f")
    b.set_result(MIR_W)
    b.start_block()
    flag = b.const_b(1)
    word = b.const_w(1)
    b.emit_br(flag, 1, 2, [word], [word])
    b.start_block(args=[MirBlockArg(10, MIR_B)])
    b.emit_ret(word)
    b.start_block(args=[MirBlockArg(11, MIR_B)])
    b.emit_ret(word)
    errs = verify_function(b.finish())
    assert any(e.code == CODE_TYPE for e in errs)
    assert any("binds a" in str(e) and "argument" in str(e) for e in errs)


def test_block_falls_through_without_terminator() -> None:
    b = _word_const_ret()
    c = b.const_w(1)
    b.emit_val(MIR_ADDW, c, c, MIR_W)
    b.terminated = 1
    errs = verify_function(b.finish())
    assert any(e.code == CODE_CONTROL for e in errs)
    assert any("falls through without a terminator" in str(e) for e in errs)


# Unreachable with the current verifier, reported rather than forcing a
# `mirverify.py` edit:
# - `sentinel a{slot} where a value is read`: `is_const_ref` is `operand < 0`,
#   so the later `operand < 0` arm never runs (`none_ref()` is pool index 0).
# - `block {bid} has {n} terminators`: `_span_end` closes a block at the first
#   terminator, so a span never contains two.
