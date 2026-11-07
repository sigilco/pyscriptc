"""The human-readable MIR text behind `pyscriptc compile FILE --emit mir`.

`mir.serialize` is the hash gate's byte source: numeric op ids, one flat
instruction list, no helper names. This printer is the reading form of the
same function: op mnemonics, instructions grouped under the block that owns
them, constants inlined as `c<i>`, and the out-of-band helper name a
`MIR_COPY` carries. The output is stable at fixed input (lists walked by
index, the helpers dict read by key), so tests pin it as goldens. It reads
MIR only; nothing here feeds the emitter or the digest.
"""

from __future__ import annotations

import os

from pyscriptc.mir import (
    MIR_B,
    MIR_BR,
    MIR_CALL,
    MIR_COPY,
    MIR_D,
    MIR_GETATTR,
    MIR_INITCALL,
    MIR_JMP,
    MIR_NOP,
    MIR_P,
    MIR_RAISE,
    MIR_RAISEEXC,
    MIR_RAISEMSG,
    MIR_RERAISE,
    MIR_RET,
    MIR_RETLOD,
    MIR_S,
    MIR_SEQGET,
    MIR_SEQPUSH,
    MIR_SEQSET,
    MIR_SETATTR,
    MIR_TRYCALL,
    MIR_TRYPACK,
    MIR_UNREACH,
    MIR_VOID,
    MIR_W,
    MirConst,
    MirFunction,
    MirModule,
    const_index,
    copy_result_type,
    float_bits,
    is_terminator,
    op_arity,
    op_name,
    op_result_type,
)


def tag_name(tag: int) -> str:
    """The QBE-style spelling of a type tag."""
    if tag == MIR_W:
        return "w"
    if tag == MIR_D:
        return "d"
    if tag == MIR_B:
        return "b"
    if tag == MIR_S:
        return "s"
    if tag == MIR_VOID:
        return "void"
    if tag == MIR_P:
        return "p"
    return f"t{tag}"


def _operand(fn: MirFunction, operand: int) -> str:
    if operand >= 0:
        return f"v{operand}"
    index = -operand - 1
    if index < len(fn.consts):
        return f"c{index}"
    return "_"


def _const_text(fn: MirFunction, index: int) -> str:
    const = fn.consts[index]
    if const.typ == MIR_D:
        return f"c{index} = d {const.dval!r} (0x{float_bits(const.dval):016x})"
    if const.typ == MIR_S:
        return f"c{index} = s {const.sval!r}"
    return f"c{index} = {tag_name(const.typ)} {const.ival}"


def _block_spans(fn: MirFunction) -> list[list[int]]:
    """Instruction indexes per block: each block runs to its terminator."""
    spans: list[list[int]] = []
    cursor = 0
    n = len(fn.ops)
    for _ in range(len(fn.blocks)):
        span: list[int] = []
        while cursor < n:
            span.append(cursor)
            cursor += 1
            if is_terminator(fn.ops[cursor - 1]):
                break
        spans.append(span)
    if cursor < n:
        spans.append(list(range(cursor, n)))
    return spans


def _bind(fn: MirFunction, values: list[int]) -> str:
    return ", ".join(_operand(fn, v) for v in values)


def _call_args_text(fn: MirFunction, i: int) -> str:
    if i not in fn.call_args:
        return ""
    return _bind(fn, fn.call_args[i])


def _instr_text(fn: MirFunction, bid: int, i: int) -> str:
    op = fn.ops[i]
    name = op_name(op)
    a1 = fn.arg1[i]
    a2 = fn.arg2[i]
    a3 = fn.arg3[i] if i < len(fn.arg3) else -1
    if op == MIR_RET:
        if a2 == MIR_VOID:
            return "ret"
        return f"ret {_operand(fn, a1)}"
    if op == MIR_JMP:
        return f"jmp b{a1}"
    if op == MIR_RAISE:
        return f"raise {_operand(fn, a1)}"
    if op == MIR_RAISEMSG:
        return f"raisemsg {_operand(fn, a1)}, {_operand(fn, a2)}"
    if op == MIR_RERAISE:
        return "reraise"
    if op == MIR_RAISEEXC:
        return f"raiseexc {_operand(fn, a1)}"
    if op == MIR_UNREACH:
        return "unreach"
    if op == MIR_BR:
        block = fn.blocks[bid] if bid < len(fn.blocks) else None
        if block is None:
            return f"br {_operand(fn, a1)}, ?, b{a2}"
        return (
            f"br {_operand(fn, a1)}, b{block.succ1}({_bind(fn, block.br_args_true)}), "
            f"b{block.succ2}({_bind(fn, block.br_args_false)})"
        )
    if op == MIR_NOP:
        return "nop"
    if op == MIR_SEQSET:
        return f"{name} {_operand(fn, a1)}, {_operand(fn, a2)}, {_operand(fn, a3)}"
    if op == MIR_SEQPUSH:
        return f"{name} {_operand(fn, a1)}, {_operand(fn, a2)}"
    if op == MIR_SETATTR:
        return f"{name} {_operand(fn, a1)}, {_operand(fn, a2)}, {_operand(fn, a3)}"
    dst = f"v{fn.dsts[i]}"
    if op == MIR_SEQGET:
        tag = tag_name(a3)
        return f"{dst}:{tag} = {name} {_operand(fn, a1)}, {_operand(fn, a2)}"
    if op == MIR_GETATTR:
        tag = tag_name(a3)
        return f"{dst}:{tag} = {name} {_operand(fn, a1)}, {_operand(fn, a2)}"
    if op == MIR_COPY:
        tag = tag_name(copy_result_type(fn, i))
        helper = fn.helpers.get(i)
        if helper is not None:
            return f"{dst}:{tag} = call {helper.split(' ')[0]} {_operand(fn, a1)}, {_operand(fn, a2)}"
        return f"{dst}:{tag} = copy {_operand(fn, a1)}"
    if op == MIR_CALL:
        callee: str = fn.callees.get(a1, "?")
        tag = tag_name(fn.arg2[i])
        return f"{dst}:{tag} = call @{callee}({_call_args_text(fn, i)})"
    if op == MIR_INITCALL:
        const: MirConst = fn.consts[const_index(a1)]
        cname: str = const.sval
        return f"{dst}:{tag_name(MIR_P)} = initcall {cname}({_call_args_text(fn, i)})"
    if op == MIR_TRYPACK:
        helper = fn.helpers.get(i)
        suffix = ""
        if helper is not None:
            suffix = " " + helper.split(" ", 1)[1]
        return f"v{fn.dsts[i]}:p = trypack{suffix}"
    if op == MIR_TRYCALL:
        helper = fn.helpers.get(i)
        suffix = ""
        if helper is not None:
            suffix = " " + helper.split(" ", 1)[1]
        return f"v{fn.dsts[i]}:w = trycall {_operand(fn, a1)}{suffix}"
    if op == MIR_RETLOD:
        tag = tag_name(a2)
        return f"v{fn.dsts[i]}:{tag} = retlod"
    result_tag = op_result_type(op)
    if result_tag < 0:
        result_tag = MIR_W
    tag_text = tag_name(result_tag)
    arity = op_arity(op)
    if arity < 0:
        arity = 2
    if arity == 0:
        return f"{dst}:{tag_text} = {name}"
    if arity == 1:
        return f"{dst}:{tag_text} = {name} {_operand(fn, a1)}"
    return f"{dst}:{tag_text} = {name} {_operand(fn, a1)}, {_operand(fn, a2)}"


def print_function(fn: MirFunction) -> str:
    """One function's text, ending in a newline."""
    params = ", ".join(f"v{p.vid}:{tag_name(p.typ)}" for p in fn.params)
    lines = [f"fn {fn.name}({params}) -> {tag_name(fn.result)} {{"]
    for index in range(len(fn.consts)):
        lines.append(f"  {_const_text(fn, index)}")
    spans = _block_spans(fn)
    for bid in range(len(spans)):
        if bid < len(fn.blocks):
            block = fn.blocks[bid]
            bargs = ", ".join(f"v{a.vid}:{tag_name(a.typ)}" for a in block.args)
            label = f"b{block.bid}"
            if bid == fn.entry:
                label += " entry"
            elif block.hint:
                label += f" {block.hint}"
            lines.append(f"{label}({bargs}):")
        else:
            lines.append("unblocked:")
        for i in spans[bid]:
            lines.append(f"  {_instr_text(fn, bid, i)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def print_module(module: MirModule) -> str:
    """A whole module: a header, the skipped names, then each function."""
    entry = "None" if module.entry is None else module.entry
    parts = [f"; module {os.path.basename(module.source_file)} entry={entry}"]
    if module.skipped:
        parts.append(f"; skipped (direct path): {', '.join(module.skipped)}")
    for fn in module.functions:
        parts.append("")
        parts.append(print_function(fn).rstrip("\n"))
    return "\n".join(parts) + "\n"
