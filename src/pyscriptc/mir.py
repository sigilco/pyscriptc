"""The 3AC MIR: a flat four-int instruction contract over parallel lists.

MIR is the backend contract the R4 leaf slice introduces (roadmap
`2026-11-02-ir-rearchitecture-roadmap.md`). It is the arena shape the
reassessment picked for the level where data orientation pays: an
instruction is four ints (`op`, `dst`, `arg1`, `arg2`) read out of four
parallel lists, a function is a set of basic blocks plus an entry block id,
and control flow joins through typed block arguments instead of phi nodes.
The op vocabulary is QBE-flavored from day one: the type suffix of an op
(`w` word/i64, `l` long/i64, `s` single, `d` double) is part of the op id,
so a future second backend reads the same stream.

The operand encoding keeps everything an int, constants included:

- a nonnegative operand is a value reference (an instruction's `dst`, a
  parameter slot, or a block argument slot of the same global value space),
- a constant is an index into the function's constant pool, encoded as
  `-(index + 1)`, with the pool carrying the value and its type tag
  (`MIR_W` int, `MIR_D` float),
- the two-operand slots of an op that uses fewer carry `-1`.

Nothing here reads the dict IR: the lowering lives in `mirlower.py`, and
the LLVM emission lives in `miremit.py`. This module owns the shape, the
op vocabulary, the construction API, and the canonical serializer whose
SHA-256 is the MIR hash gate (stable across runs at fixed input; a dict
with insertion-ordered iteration is the only collection in the shape, and
the serializer walks lists by index, so hash-seed variation cannot move
the bytes).
"""

from __future__ import annotations

import hashlib
import struct


class MirError(Exception):
    """A bad MIR construction request or a shape the API refuses."""


# The type tags of the constant pool and the op vocabulary. `MIR_W` is the
# QBE `w` word: a 64-bit signed int on this ABI. `MIR_D` is the QBE `d`
# double. `s` (float32) is reserved, not issued.
MIR_W = 0
MIR_D = 1
MIR_B = 2  # an i1 bool: parameters, locals, constants, compare and `not` results

# Control flow. A `MIR_JMP` ends its block unconditionally; a `MIR_BR`
# dispatches on an i1 value to two successors and carries the two argument
# bindings. `MIR_RET` ends the function.
MIR_NOP = 0
MIR_JMP = 1
MIR_BR = 2
MIR_RET = 3

# Arithmetic. `w` ops on ints go through the checked helpers at emission
# (overflow raises), `d` ops are plain IEEE-754.
MIR_ADDW = 4
MIR_SUBW = 5
MIR_MULW = 6
MIR_ADDD = 7
MIR_SUBD = 8
MIR_MULD = 9
MIR_NEGW = 10
MIR_NEGD = 11

# Comparison. Both families produce a `MIR_B` 0/1 value, the bool the
# branch op reads.
MIR_CLTW = 12
MIR_CLEW = 13
MIR_CGTW = 14
MIR_CGEW = 15
MIR_CEQW = 16
MIR_CNEW = 17
MIR_CLTD = 18
MIR_CLED = 19
MIR_CGTD = 20
MIR_CGED = 21
MIR_CEQD = 22
MIR_CNED = 23

# Conversion.
MIR_EXTWD = 24

# Copy. One operand.
MIR_COPY = 25

# Bool negation: `xor i1 v, true` on the MIR_B operand.
MIR_NOTB = 26

# The op arity table. `MIR_NOP` through `MIR_COPY`: the branch reads its
# condition from `arg1`, and its two successors carry the block arguments
# (see `MirBlock`). Every value op reads `arg1`/`arg2` per this table.
OP_ARITY = {
    MIR_NOP: 0,
    MIR_NOTB: 1,
    MIR_JMP: 0,
    MIR_BR: 1,
    MIR_RET: 1,
    MIR_ADDW: 2,
    MIR_SUBW: 2,
    MIR_MULW: 2,
    MIR_ADDD: 2,
    MIR_SUBD: 2,
    MIR_MULD: 2,
    MIR_NEGW: 1,
    MIR_NEGD: 1,
    MIR_CLTW: 2,
    MIR_CLEW: 2,
    MIR_CGTW: 2,
    MIR_CGEW: 2,
    MIR_CEQW: 2,
    MIR_CNEW: 2,
    MIR_CLTD: 2,
    MIR_CLED: 2,
    MIR_CGTD: 2,
    MIR_CGED: 2,
    MIR_CEQD: 2,
    MIR_CNED: 2,
    MIR_EXTWD: 1,
    MIR_COPY: 1,
}

# The result type of each value op, in the constant-pool tag space.
# Control ops produce nothing (`MIR_W` here is unreachable through the
# arity table).
OP_RESULT_TYPE = {
    MIR_NOP: MIR_W,
    MIR_JMP: MIR_W,
    MIR_BR: MIR_W,
    MIR_RET: MIR_W,
    MIR_ADDW: MIR_W,
    MIR_SUBW: MIR_W,
    MIR_MULW: MIR_W,
    MIR_ADDD: MIR_D,
    MIR_SUBD: MIR_D,
    MIR_MULD: MIR_D,
    MIR_NEGW: MIR_W,
    MIR_NEGD: MIR_D,
    MIR_CLTW: MIR_B,
    MIR_CLEW: MIR_B,
    MIR_CGTW: MIR_B,
    MIR_CGEW: MIR_B,
    MIR_CEQW: MIR_B,
    MIR_CNEW: MIR_B,
    MIR_CLTD: MIR_B,
    MIR_CLED: MIR_B,
    MIR_CGTD: MIR_B,
    MIR_CGED: MIR_B,
    MIR_CEQD: MIR_B,
    MIR_CNED: MIR_B,
    MIR_NOTB: MIR_B,
    MIR_EXTWD: MIR_D,
    MIR_COPY: MIR_W,  # a copy's true tag rides arg2; the tables cannot know it
}

# The true result type of a `MIR_COPY`: the tag packed into `arg2`.


def copy_result_type(fn: MirFunction, i: int) -> int:
    # A helper-carrying copy keeps both operands in arg1/arg2, so its tag
    # rides the helpers entry beside the helper name. That entry is read
    # first: an operand value id can collide with a tag value (the id `1`
    # is `MIR_D`), and reading arg2 as the tag first would type the
    # helper's i64 result as a double.
    entry = fn.helpers.get(i)
    if entry is not None:
        tag = int(entry.split(" ")[1])
        if tag == MIR_W or tag == MIR_D or tag == MIR_B:
            return tag
    # A plain copy (a store into a name slot) packs the value's tag in
    # arg2.
    arg2 = fn.arg2[i]
    if arg2 == MIR_W or arg2 == MIR_D or arg2 == MIR_B:
        return arg2
    return MIR_W


# The ops that end a block. A list, not a set: `in` lowers for lists and
# the table is four entries, so the membership walk costs nothing.
TERMINATORS = [MIR_JMP, MIR_BR, MIR_RET]

# The ops that produce a value a later instruction can reference.
VALUE_OPS = [
    MIR_ADDW,
    MIR_SUBW,
    MIR_MULW,
    MIR_ADDD,
    MIR_SUBD,
    MIR_MULD,
    MIR_NEGW,
    MIR_NEGD,
    MIR_CLTW,
    MIR_CLEW,
    MIR_CGTW,
    MIR_CGEW,
    MIR_CEQW,
    MIR_CNEW,
    MIR_CLTD,
    MIR_CLED,
    MIR_CGTD,
    MIR_CGED,
    MIR_CEQD,
    MIR_CNED,
    MIR_EXTWD,
    MIR_COPY,
    MIR_NOTB,
]


def const_ref(index: int) -> int:
    """The operand encoding of constant-pool entry `index`."""
    if index < 0:
        raise MirError("constant index must be nonnegative")
    return -(index + 1)


def is_const_ref(operand: int) -> bool:
    """True when the operand encodes a constant-pool entry."""
    return operand < 0


def const_index(operand: int) -> int:
    """Decode a constant operand back to its pool index."""
    if operand >= 0:
        raise MirError("operand is a value reference, not a constant")
    return -operand - 1


def none_ref() -> int:
    """The unused-operand sentinel."""
    return -1


class MirParam:
    """One function parameter: a value slot and its type tag."""

    vid: int
    typ: int

    def __init__(self, vid: int, typ: int) -> None:
        self.vid = vid
        self.typ = typ


class MirConst:
    """One constant-pool entry. `typ` is `MIR_W` or `MIR_D`; exactly one of
    the two value fields is read."""

    typ: int
    ival: int
    dval: float

    def __init__(self, typ: int, ival: int, dval: float) -> None:
        self.typ = typ
        self.ival = ival
        self.dval = dval


class MirBlockArg:
    """One typed block argument of a block: a value slot a predecessor
    passes and the block body reads. Types are `MIR_W`/`MIR_D`."""

    vid: int
    typ: int

    def __init__(self, vid: int, typ: int) -> None:
        self.vid = vid
        self.typ = typ


class MirBlock:
    """One basic block: instructions over the function's parallel lists,
    its typed block arguments, and its successors.

    `arg1`/`arg2` of the terminating `MIR_BR` name the two successor
    block ids in the same order as `succ1`/`succ2`; `MIR_JMP` names one in
    `succ1`. The `MIR_BR` branch condition is `arg1`. `br_args_true` and
    `br_args_false` bind the two targets' block arguments positionally;
    they stay empty unless the block ends in `MIR_BR`.
    """

    bid: int
    args: list[MirBlockArg]
    succ1: int
    succ2: int
    br_args_true: list[int]
    br_args_false: list[int]
    # The label hint the emitter renders (`then`, `while.cond`, ...). Empty
    # for blocks built without one: the emitter falls back to the `if`
    # shape's derived hints for those.
    hint: str

    def __init__(
        self,
        bid: int,
        args: list[MirBlockArg] | None = None,
        succ1: int = 0,
        succ2: int = 0,
        br_args_true: list[int] | None = None,
        br_args_false: list[int] | None = None,
        hint: str = "",
    ) -> None:
        self.bid = bid
        self.hint = hint
        block_args: list[MirBlockArg]
        if args is None:
            block_args = []
        else:
            block_args = list(args)
        self.args = block_args
        self.succ1 = succ1
        self.succ2 = succ2
        br_true: list[int]
        if br_args_true is None:
            br_true = []
        else:
            br_true = list(br_args_true)
        self.br_args_true = br_true
        br_false: list[int]
        if br_args_false is None:
            br_false = []
        else:
            br_false = list(br_args_false)
        self.br_args_false = br_false


class MirFunction:
    """One lowered function: parallel instruction lists over a value-id
    space, a constant pool, its blocks, and the entry block id.

    `params` and every block argument take their value ids from the same
    space the instruction `dst`s use, so an operand int names a value
    without a per-block renamer.
    """

    name: str
    params: list[MirParam]
    result: int
    entry: int
    ops: list[int]
    dsts: list[int]
    arg1: list[int]
    arg2: list[int]
    blocks: list[MirBlock]
    consts: list[MirConst]
    # Out-of-band helper names keyed by instruction index. The leaf slice
    # lowers a runtime-helper binary (`//`, `%`, `/`, ...) to a `MIR_COPY`
    # and records the `ps_*` helper the emitter renders at that index; the
    # canonical serializer deliberately omits it (the hash covers the
    # instruction stream, not the target's spelling of it).
    helpers: dict[int, str]

    def __init__(self, name: str) -> None:
        self.name = name
        self.params = []
        self.result = MIR_W
        self.entry = 0
        self.ops = []
        self.dsts = []
        self.arg1 = []
        self.arg2 = []
        self.blocks = []
        self.consts = []
        self.helpers = {}

    def n_instr(self) -> int:
        return len(self.ops)


class MirModule:
    """One lowered module: its functions and the entry function name."""

    source_file: str
    functions: list[MirFunction]
    # The entry function name the direct path would have run, if any. The
    # None-vs-"" spelling is the dict's: None means no entry, "" an empty
    # name.
    entry: str | None
    # Out-of-slice function names: lowered nowhere, emitted by the direct
    # path unchanged. Empty when the whole module is in the slice.
    skipped: list[str]

    def __init__(self, source_file: str, functions: list[MirFunction], entry: str | None = None) -> None:
        self.source_file = source_file
        self.functions = functions
        self.entry = entry
        self.skipped = []


class MirBuilder:
    """The construction API: one builder per function body.

    The builder owns the value-id counter, the constant pool and the
    block table; `emit` appends to the function's parallel lists. Control
    flow is explicit: `start_block` opens a block, `emit_jmp`/`emit_br`/
    `emit_ret` close it. A value op inside no block, or a second value op
    after a terminator, is a `MirError`.

    The `terminated` flag is an int (0/1), not a bool: bool fields do not
    lower. The per-access list locals (`ops`, `dsts`, ...) alias the
    function's lists because attribute-chained `append` does not lower.
    """

    fn: MirFunction
    next_vid: int
    current: MirBlock | None
    terminated: int

    def __init__(self, name: str) -> None:
        self.fn = MirFunction(name)
        self.next_vid = 0
        self.current = None
        self.terminated = 0

    def add_param(self, typ: int) -> int:
        """Declare a parameter, return its value id."""
        if typ != MIR_W and typ != MIR_D and typ != MIR_B:
            raise MirError("parameter type must be MIR_W, MIR_B or MIR_D")
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        params: list[MirParam] = self.fn.params
        params.append(MirParam(vid, typ))
        return vid

    def set_result(self, typ: int) -> None:
        if typ != MIR_W and typ != MIR_D and typ != MIR_B:
            raise MirError("result type must be MIR_W, MIR_B or MIR_D")
        self.fn.result = typ

    def const_b(self, value: int) -> int:
        """Intern a bool constant (0/1), return its operand encoding."""
        consts: list[MirConst] = self.fn.consts
        for index in range(len(consts)):
            const = consts[index]
            if const.typ == MIR_B and const.ival == value:
                return const_ref(index)
        consts.append(MirConst(MIR_B, value, 0.0))
        return const_ref(len(consts) - 1)

    def const_w(self, value: int) -> int:
        """Intern an int constant, return its operand encoding."""
        consts: list[MirConst] = self.fn.consts
        for index in range(len(consts)):
            const = consts[index]
            if const.typ == MIR_W and const.ival == value:
                return const_ref(index)
        consts.append(MirConst(MIR_W, value, 0.0))
        return const_ref(len(consts) - 1)

    def const_d(self, value: float) -> int:
        """Intern a float constant, return its operand encoding.

        The lookup key is the IEEE-754 bit pattern, so `0.0` and `-0.0`
        stay distinct and a float equality quirk cannot merge them.
        """
        consts: list[MirConst] = self.fn.consts
        bits = float_bits(value)
        for index in range(len(consts)):
            const = consts[index]
            if const.typ == MIR_D and float_bits(const.dval) == bits:
                return const_ref(index)
        consts.append(MirConst(MIR_D, 0, value))
        return const_ref(len(consts) - 1)

    def start_block(self, args: list[MirBlockArg] | None = None, hint: str = "") -> int:
        """Open a block; returns its id. The first block is the entry.

        `hint` is the label hint the emitter renders (`then`,
        `while.cond`, ...); blocks built without one fall back to the
        emitter's derived `if` hints.
        """
        current: MirBlock | None = self.current
        if current is not None and not self.terminated:
            raise MirError(f"block {current.bid} has no terminator")
        blocks: list[MirBlock] = self.fn.blocks
        bid: int = len(blocks)
        block = MirBlock(bid, args, -1, -1, None, None, hint)
        blocks.append(block)
        self.current = block
        self.terminated = 0
        return bid

    def _check_open(self, op: int) -> None:
        current: MirBlock | None = self.current
        if current is None:
            raise MirError(f"op {op} emitted outside a block")
        if self.terminated:
            raise MirError(f"op {op} emitted after a terminator")

    def emit(self, op: int, dst: int, arg1: int, arg2: int) -> int:
        """Append one instruction; returns its instruction index.

        `dst` is `-1` for ops that produce nothing.
        """
        arity = OP_ARITY.get(op)
        if arity is None:
            raise MirError(f"unknown op {op}")
        current: MirBlock | None = self.current
        if current is None:
            raise MirError(f"op {op} emitted outside a block")
        if self.terminated:
            raise MirError(f"op {op} emitted after a terminator")
        ops: list[int] = self.fn.ops
        dsts: list[int] = self.fn.dsts
        f_arg1: list[int] = self.fn.arg1
        f_arg2: list[int] = self.fn.arg2
        ops.append(op)
        dsts.append(dst)
        f_arg1.append(arg1)
        f_arg2.append(arg2)
        return len(ops) - 1

    def emit_val(self, op: int, arg1: int, arg2: int, typ: int) -> int:
        """Emit a value op with a fresh destination; returns the value id."""
        if op not in VALUE_OPS:
            raise MirError(f"op {op} is not a value op")
        if typ != MIR_W and typ != MIR_D and typ != MIR_B:
            raise MirError("value type must be MIR_W, MIR_B or MIR_D")
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        self.emit(op, vid, arg1, arg2)
        return vid

    def emit_nop(self) -> None:
        self.emit(MIR_NOP, -1, none_ref(), none_ref())

    def emit_copy(self, src: int, typ: int) -> int:
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        self.emit(MIR_COPY, vid, src, typ)
        return vid

    def emit_ret(self, value: int) -> None:
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("ret outside an open block")
        self.emit(MIR_RET, -1, value, none_ref())
        self.terminated = 1

    def emit_jmp(self, target: int) -> None:
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("jmp outside an open block")
        self.emit(MIR_JMP, -1, target, none_ref())
        current.succ1 = target
        self.terminated = 1

    def emit_br(self, cond: int, if_true: int, if_false: int, args_true: list[int], args_false: list[int]) -> None:
        """A conditional branch. `args_true`/`args_false` bind the target
        blocks' arguments positionally; `arg2` packs the false-target id
        and the false bindings live in the block's `succ2`-adjacent list.
        """
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("br outside an open block")
        self.emit(MIR_BR, -1, cond, if_false)
        current.succ1 = if_true
        current.succ2 = if_false
        current.br_args_true = list(args_true)
        current.br_args_false = list(args_false)
        self.terminated = 1

    def finish(self) -> MirFunction:
        """Return the function; refuses dangling open blocks."""
        current: MirBlock | None = self.current
        if current is not None and not self.terminated:
            raise MirError(f"block {current.bid} has no terminator")
        return self.fn


def float_bits(value: float) -> int:
    """The IEEE-754 bit pattern of a float as an unsigned int."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def serialize(fn: MirFunction) -> str:
    """The canonical text form: one line per instruction, field order fixed.

    The serializer is the hash gate's byte source. It walks the parallel
    lists by index and the constant pool in insertion order, so the bytes
    depend only on the construction order, never on a dict or set. Block
    arguments and successors render after the block id; the branch
    bindings render with the block that owns them.
    """
    lines: list[str] = []
    lines.append(f"fn {fn.name} result={fn.result} entry={fn.entry}")
    params: list[MirParam] = fn.params
    for i in range(len(params)):
        param = params[i]
        lines.append(f"param v{param.vid} t{param.typ}")
    consts: list[MirConst] = fn.consts
    for index in range(len(consts)):
        const = consts[index]
        if const.typ == MIR_W:
            lines.append(f"const {index} w {const.ival}")
        elif const.typ == MIR_B:
            lines.append(f"const {index} b {const.ival}")
        else:
            lines.append(f"const {index} d {float_bits(const.dval)}")
    blocks: list[MirBlock] = fn.blocks
    for bi in range(len(blocks)):
        block = blocks[bi]
        bargs: list[MirBlockArg] = block.args
        arg_ids: str = ",".join(f"v{bargs[i].vid}:t{bargs[i].typ}" for i in range(len(bargs)))
        lines.append(f"block {block.bid} args[{arg_ids}] succ {block.succ1} {block.succ2}")
        br_true: list[int] = block.br_args_true
        br_false: list[int] = block.br_args_false
        if br_true or br_false:
            t: str = ",".join(str(br_true[i]) for i in range(len(br_true)))
            f: str = ",".join(str(br_false[i]) for i in range(len(br_false)))
            lines.append(f"brbind true[{t}] false[{f}]")
    ops: list[int] = fn.ops
    dsts: list[int] = fn.dsts
    f_arg1: list[int] = fn.arg1
    f_arg2: list[int] = fn.arg2
    for i in range(len(ops)):
        lines.append(f"instr {i} op{ops[i]} dst{dsts[i]} a1={f_arg1[i]} a2={f_arg2[i]}")
    return "\n".join(lines) + "\n"


def module_serialize(module: MirModule) -> str:
    """The canonical text form of a whole module, functions in list order."""
    functions: list[MirFunction] = module.functions
    entry: str | None = module.entry
    entry_text: str = "None" if entry is None else entry
    parts: list[str] = []
    parts.append(f"module {module.source_file} entry={entry_text} n={len(functions)}")
    for i in range(len(functions)):
        text = serialize(functions[i])
        parts.append(text[: len(text) - 1])
    return "\n".join(parts) + "\n"


def module_hash(module: MirModule) -> str:
    """The SHA-256 of the canonical form, hex. Stable across runs at fixed input."""
    return hashlib.sha256(module_serialize(module).encode("utf-8")).hexdigest()
