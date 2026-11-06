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
    MIR_ADDD,
    MIR_ADDW,
    MIR_B,
    MIR_BR,
    MIR_CALL,
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
    MIR_EXTWD,
    MIR_JMP,
    MIR_MULD,
    MIR_MULW,
    MIR_NEGD,
    MIR_NEGW,
    MIR_NOP,
    MIR_NOTB,
    MIR_RAISE,
    MIR_RET,
    MIR_SUBD,
    MIR_SUBW,
    MIR_VOID,
    MIR_W,
    OP_ARITY,
    OP_RESULT_TYPE,
    MirFunction,
    MirModule,
    copy_result_type,
    float_bits,
)

OP_NAMES: dict[int, str] = {
    MIR_NOP: "nop",
    MIR_JMP: "jmp",
    MIR_BR: "br",
    MIR_RET: "ret",
    MIR_ADDW: "addw",
    MIR_SUBW: "subw",
    MIR_MULW: "mulw",
    MIR_ADDD: "addd",
    MIR_SUBD: "subd",
    MIR_MULD: "muld",
    MIR_NEGW: "negw",
    MIR_NEGD: "negd",
    MIR_CLTW: "cltw",
    MIR_CLEW: "clew",
    MIR_CGTW: "cgtw",
    MIR_CGEW: "cgew",
    MIR_CEQW: "ceqw",
    MIR_CNEW: "cnew",
    MIR_CLTD: "cltd",
    MIR_CLED: "cled",
    MIR_CGTD: "cgtd",
    MIR_CGED: "cged",
    MIR_CEQD: "ceqd",
    MIR_CNED: "cned",
    MIR_EXTWD: "extwd",
    MIR_COPY: "copy",
    MIR_NOTB: "notb",
    MIR_RAISE: "raise",
    MIR_CALL: "call",
}

_TERMINATORS: list[int] = [MIR_JMP, MIR_BR, MIR_RET, MIR_RAISE]


def tag_name(tag: int) -> str:
    """The QBE-style spelling of a type tag."""
    if tag == MIR_W:
        return "w"
    if tag == MIR_D:
        return "d"
    if tag == MIR_B:
        return "b"
    if tag == MIR_VOID:
        return "void"
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
            if fn.ops[cursor - 1] in _TERMINATORS:
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
    name = OP_NAMES.get(op, f"op{op}")
    a1 = fn.arg1[i]
    a2 = fn.arg2[i]
    if op == MIR_RET:
        if a2 == MIR_VOID:
            return "ret"
        return f"ret {_operand(fn, a1)}"
    if op == MIR_JMP:
        return f"jmp b{a1}"
    if op == MIR_RAISE:
        return f"raise {_operand(fn, a1)}"
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
    dst = f"v{fn.dsts[i]}"
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
    tag = tag_name(OP_RESULT_TYPE.get(op, MIR_W))
    if OP_ARITY.get(op, 2) == 1:
        return f"{dst}:{tag} = {name} {_operand(fn, a1)}"
    return f"{dst}:{tag} = {name} {_operand(fn, a1)}, {_operand(fn, a2)}"


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
