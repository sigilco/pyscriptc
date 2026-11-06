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

# The void tag: a function returning None has no return value, so its
# `MIR_RET` reads no operand and the function's result tag is `MIR_VOID`.
# It is never a parameter, local, constant or value-op result. The tag
# space (MIR_W, MIR_D, MIR_B, MIR_VOID, MIR_P) is its own: the numbers
# overlap the op space below by design, and the two are never mixed.
MIR_VOID = 3

# `MIR_S` is a str value: at run time the `%ps.str` aggregate (byte length,
# data pointer, code-point count, flags). A `MIR_S` constant pool entry
# carries the UTF-8 text in `sval`; a `MIR_S` value id names an SSA value
# of that aggregate shape. `MIR_P` is the shared heap-pointer tag: list
# and dict values are `ptr` at the ABI. HIR keeps the list-vs-dict and
# element-type semantics; MIR carries only the pointer shape, so one tag
# serves the pointer families. (`MIR_S` is 4, not 3: 3 is `MIR_VOID`.)
MIR_S = 4
MIR_P = 5

# The `arg3` sentinel for instructions that do not read a third operand.
# Kept as a name so self-host lowering sees a constant default, not a
# unary-minus expression.
ARG3_NONE = -1

# Control flow. A `MIR_JMP` ends its block unconditionally; a `MIR_BR`
# dispatches on an i1 value to two successors and carries the two argument
# bindings. `MIR_RET` ends the function; a void `MIR_RET` reads no operand.
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

# Raise: `call void @ps_raise_code(i32 code) noreturn; unreachable`. The
# arg1 operand is a constant-pool word carrying the exception code (the
# `EXCEPTIONS` table's int, spelled at the lowerer). A raise ends its
# block the way a `MIR_RET` does.
MIR_RAISE = 27

# Call: a direct call to a callee value (a function's entry, packed as a
# value id) with the arguments in `call_args`. `arg1` is the callee, `arg2`
# carries the result tag (`MIR_VOID` when the call produces nothing, the
# same spelling as a void `MIR_RET`). The operand slots cannot hold a
# variable argument count, so the arguments ride `MirFunction.call_args`,
# keyed by instruction index; the serializer renders them in index order
# so the hash stays construction-ordered. A call is not a terminator: a
# call's block continues.
MIR_CALL = 28

# Bitwise. `w` ops on ints render as the plain LLVM bitwise instruction
# (`and i64`); no overflow check applies. Shifts are not ops: `<<`/`>>`
# lower to the `ps.ishl`/`ps.ishr` runtime helpers through the same
# out-of-band helper channel the `//`/`%` ops use. The ids sit after the
# sequence ops (30-34) so the two ranges never collide.
MIR_ANDW = 29
MIR_ORW = 35
MIR_XORW = 36

# Maps. `MIR_MAP_PUT` stores a key/value pair through the runtime cell ABI
# (`ps_map_put`): `arg1` is the map, `arg2` the key and `arg3` the value.
# Map creation rides `MIR_CALL` on the `rt:ps_map_new` symbol so the four
# constructor words stay in `call_args`.
MIR_MAP_PUT = 40

# Sequences. `MIR_NEWSEQ` builds a list (`ps_seq_new`): `arg1` is the
# element byte size and `arg2` the element count, both word constants, and
# the result is a `MIR_P` pointer. `MIR_SEQGET` loads an element into a
# fresh runtime cell and loads it back; `arg3` carries the element's result
# tag (`MIR_W`/`MIR_D` today) because the operand slots are already spent
# on the sequence and the index. `MIR_SEQSET` stores through the same cell
# ABI with the stored value in `arg3`; `MIR_SEQPUSH` is `list.append`'s
# `ps_seq_push`; `MIR_SEQLEN` is `len(seq)`.
MIR_NEWSEQ = 30
MIR_SEQGET = 31
MIR_SEQSET = 32
MIR_SEQPUSH = 33
MIR_SEQLEN = 34

# Class values. A class instance is a heap pointer, so it rides the
# shared `MIR_P` tag like the other pointer shapes; the class identity
# stays in HIR the way a list's element type does. `MIR_NEWOBJ` allocates
# one (`ps_alloc` over the `%cls.<name>` struct size); `arg1` is a
# `MIR_S` constant carrying the class name, which the emitter reads
# without materializing (the name is not a runtime str value). The
# synthesized `tuple_N_*` classes the lowerer emits for unpacking have
# no `__init__`, so the constructor call carries no arguments and no
# initializer call.
#
# `MIR_SETATTR` writes one field: `arg1` the object, `arg2` the value,
# `arg3` a `MIR_S` constant spelling `<class>.<field>`; it produces
# nothing. `MIR_GETATTR` reads one field the same way with the result
# tag in `arg3` (the `MIR_SEQGET` convention), `arg2` the same key
# constant.
MIR_NEWOBJ = 37
MIR_SETATTR = 38
MIR_GETATTR = 39
# The op arity table. `MIR_NOP` through `MIR_COPY`: the branch reads its
# condition from `arg1`, and its two successors carry the block arguments
# (see `MirBlock`). Every value op reads `arg1`/`arg2` per this table;
# `MIR_SEQSET` is the first op that also reads `arg3`.
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
    MIR_RAISE: 1,
    MIR_CALL: 2,
    MIR_ANDW: 2,
    MIR_NEWSEQ: 2,
    MIR_SEQGET: 2,
    MIR_SEQSET: 3,
    MIR_SEQPUSH: 2,
    MIR_SEQLEN: 1,
    MIR_ORW: 2,
    MIR_XORW: 2,
    MIR_NEWOBJ: 1,
    MIR_SETATTR: 3,
    MIR_GETATTR: 3,
    MIR_MAP_PUT: 3,
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
    MIR_RAISE: MIR_W,
    MIR_CALL: MIR_W,  # a call's true tag rides arg2; the tables cannot know it
    MIR_ANDW: MIR_W,
    MIR_NEWSEQ: MIR_P,
    MIR_SEQGET: MIR_W,  # a seqget's true tag rides arg3; the tables cannot know it
    MIR_SEQSET: MIR_W,
    MIR_SEQPUSH: MIR_W,
    MIR_SEQLEN: MIR_W,
    MIR_ORW: MIR_W,
    MIR_XORW: MIR_W,
    MIR_NEWOBJ: MIR_P,
    MIR_SETATTR: MIR_W,
    MIR_GETATTR: MIR_P,  # a getattr's true tag rides arg3; the tables cannot know it
    MIR_MAP_PUT: MIR_W,
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
        if tag == MIR_W or tag == MIR_D or tag == MIR_B or tag == MIR_S or tag == MIR_P:
            return tag
    # A plain copy (a store into a name slot) packs the value's tag in
    # arg2.
    arg2 = fn.arg2[i]
    if arg2 == MIR_W or arg2 == MIR_D or arg2 == MIR_B or arg2 == MIR_S or arg2 == MIR_P:
        return arg2
    return MIR_W


# The ops that end a block. A list, not a set: `in` lowers for lists and
# the table is five entries, so the membership walk costs nothing.
TERMINATORS = [MIR_JMP, MIR_BR, MIR_RET, MIR_RAISE]

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
    MIR_ANDW,
    MIR_NEWSEQ,
    MIR_SEQGET,
    MIR_SEQLEN,
    MIR_ORW,
    MIR_XORW,
    MIR_NEWOBJ,
    MIR_GETATTR,
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
    """The unused-operand sentinel.

    `-1` doubles as `const_ref(0)`: a constant of pool index 0 encodes to
    the same int. The invariant that makes this safe is that a required
    operand is never "unused": the sentinel only sits in a slot the op's
    arity does not read, and a real constant of pool index 0 is only read
    from a slot that does. Do not "fix" `-1` by skipping pool index 0.
    """
    return -1


class MirParam:
    """One function parameter: a value slot and its type tag."""

    vid: int
    typ: int

    def __init__(self, vid: int, typ: int) -> None:
        self.vid = vid
        self.typ = typ


class MirConst:
    """One constant-pool entry. `typ` is `MIR_W`, `MIR_D`, `MIR_B` or
    `MIR_S`; the value fields are read per tag: `ival` for the word and
    bool tags, `dval` for `MIR_D`, `sval` for `MIR_S`."""

    typ: int
    ival: int
    dval: float
    sval: str

    def __init__(self, typ: int, ival: int, dval: float, sval: str = "") -> None:
        self.typ = typ
        self.ival = ival
        self.dval = dval
        self.sval = sval


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
    # The direct path's label number for this block, or 0 when the block
    # carries no explicit label. The direct emitter allocates its labels
    # lazily, in statement pre-order, so the label numbers do not follow
    # the block construction order: the lowerer records the number it
    # reserved for the block, and the emitter renders it. 0 is safe as the
    # sentinel: the entry block is bid 0 and renders as `entry`, and the
    # direct emitter's label numbers start at 1.
    label_id: int

    def __init__(
        self,
        bid: int,
        args: list[MirBlockArg] | None = None,
        succ1: int = 0,
        succ2: int = 0,
        br_args_true: list[int] | None = None,
        br_args_false: list[int] | None = None,
        hint: str = "",
        label_id: int = 0,
    ) -> None:
        self.bid = bid
        self.hint = hint
        self.label_id = label_id
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
    # The third operand slot. Most ops leave it at the `-1` sentinel; the
    # sequence stores (`MIR_SEQSET`) read the stored value here, and
    # `MIR_SEQGET` carries its element result tag here (a tag, not an
    # operand). The serializer prints the slot on every instruction so the
    # digest covers the full operand stream.
    arg3: list[int]
    blocks: list[MirBlock]
    consts: list[MirConst]
    # out-of-band helper names keyed by instruction index. The leaf slice
    # lowers a runtime-helper binary (`//`, `%`, `/`, ...) to a `MIR_COPY`
    # and records the `ps_*` helper the emitter renders at that index; the
    # canonical serializer deliberately omits it (the hash covers the
    # instruction stream, not the target's spelling of it).
    helpers: dict[int, str]
    # Call arguments keyed by instruction index, one list per `MIR_CALL`
    # in index order. A separate dict, not a slot packing: an instruction
    # has two operand slots and a call reads a variable argument count.
    call_args: dict[int, list[int]]
    # The callee table: a callee value id to the callee function's name.
    # A direct call names a same-module function; the slice's callees are
    # plain module-level functions, so the lowerer packs one callee value
    # id per distinct callee (allocated through `MirBuilder.add_callee`)
    # and `arg1` of the call reads it like any value. The serializer
    # renders the table construction-ordered, so the hash stays stable.
    callees: dict[int, str]

    def __init__(self, name: str) -> None:
        self.name = name
        self.params = []
        self.result = MIR_W
        self.entry = 0
        self.ops = []
        self.dsts = []
        self.arg1 = []
        self.arg2 = []
        self.arg3 = []
        self.blocks = []
        self.consts = []
        self.helpers = {}
        self.call_args = {}
        self.callees = {}

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
        if typ != MIR_W and typ != MIR_D and typ != MIR_B and typ != MIR_S and typ != MIR_P:
            raise MirError("parameter type must be MIR_W, MIR_B, MIR_D, MIR_S or MIR_P")
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        params: list[MirParam] = self.fn.params
        params.append(MirParam(vid, typ))
        return vid

    def set_result(self, typ: int) -> None:
        if typ != MIR_W and typ != MIR_D and typ != MIR_B and typ != MIR_S and typ != MIR_P and typ != MIR_VOID:
            raise MirError("result type must be MIR_W, MIR_B, MIR_D, MIR_S, MIR_P or MIR_VOID")
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

    def const_s(self, text: str) -> int:
        """Intern a str constant, return its operand encoding.

        The lookup key is the exact string (its UTF-8 bytes), so
        hash-seed variation cannot merge or split entries. Lone
        surrogates never reach the pool: the direct path's lowering
        refuses them before MIR sees the constant.
        """
        consts: list[MirConst] = self.fn.consts
        for index in range(len(consts)):
            const = consts[index]
            if const.typ == MIR_S and const.sval == text:
                return const_ref(index)
        consts.append(MirConst(MIR_S, 0, 0.0, text))
        return const_ref(len(consts) - 1)

    def start_block(self, args: list[MirBlockArg] | None = None, hint: str = "", label_id: int = 0) -> int:
        """Open a block; returns its id. The first block is the entry.

        `hint` is the label hint the emitter renders (`then`,
        `while.cond`, ...); blocks built without one fall back to the
        emitter's derived `if` hints. `label_id` is the direct path's
        label number for the block, recorded by the lowerer so the
        emitter renders the same number.
        """
        current: MirBlock | None = self.current
        if current is not None and not self.terminated:
            raise MirError(f"block {current.bid} has no terminator")
        blocks: list[MirBlock] = self.fn.blocks
        bid: int = len(blocks)
        block = MirBlock(bid, args, -1, -1, None, None, hint, label_id)
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

    def emit(self, op: int, dst: int, arg1: int, arg2: int, arg3: int = ARG3_NONE) -> int:
        """Append one instruction; returns its instruction index.

        `dst` is `-1` for ops that produce nothing. `arg3` is the third
        operand slot (`MIR_SEQSET`'s stored value, `MIR_SEQGET`'s result
        tag); every other op leaves it at the `-1` sentinel.
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
        f_arg3: list[int] = self.fn.arg3
        ops.append(op)
        dsts.append(dst)
        f_arg1.append(arg1)
        f_arg2.append(arg2)
        f_arg3.append(arg3)
        return len(ops) - 1

    def emit_val(self, op: int, arg1: int, arg2: int, typ: int) -> int:
        """Emit a value op with a fresh destination; returns the value id."""
        if op not in VALUE_OPS:
            raise MirError(f"op {op} is not a value op")
        if typ != MIR_W and typ != MIR_D and typ != MIR_B and typ != MIR_S and typ != MIR_P:
            raise MirError("value type must be MIR_W, MIR_B, MIR_D, MIR_S or MIR_P")
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        self.emit(op, vid, arg1, arg2)
        return vid

    def emit_val3(self, op: int, arg1: int, arg2: int, arg3: int, typ: int) -> int:
        """Emit a three-slot value op with a fresh destination."""
        if op not in VALUE_OPS:
            raise MirError(f"op {op} is not a value op")
        if typ != MIR_W and typ != MIR_D and typ != MIR_B and typ != MIR_S and typ != MIR_P:
            raise MirError("value type must be MIR_W, MIR_B, MIR_D, MIR_S or MIR_P")
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        self.emit(op, vid, arg1, arg2, arg3)
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

    def emit_ret_void(self) -> None:
        """A void return: `MIR_RET` reading no operand, ending the block.

        The ret's unused operand slot carries the void tag, so the
        emitter renders `ret void` and the verifier skips the operand
        checks a value return runs. `arg1` keeps the unused-operand
        sentinel; a value return never spells it that way, so the two
        forms stay distinguishable in the canonical text.
        """
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("ret outside an open block")
        self.emit(MIR_RET, -1, none_ref(), MIR_VOID)
        self.terminated = 1

    def add_callee(self, name: str) -> int:
        """Declare a callee: allocate its entry value id, return the id.

        A direct call names a same-module function; the entry value id is
        the value `arg1` of the `MIR_CALL` reads, and the name is what
        the emitter resolves to the LLVM symbol. One id per distinct
        callee: a second `add_callee` of the same name returns the first
        id.
        """
        callees: dict[int, str] = self.fn.callees
        for vid in callees:
            if callees[vid] == name:
                return vid
        vid: int = self.next_vid
        self.next_vid = self.next_vid + 1
        callees[vid] = name
        return vid

    def emit_call(self, callee: int, args: list[int], result_type: int) -> int:
        """A direct call: `callee` called with `args`.

        `result_type` is the call's result tag (`MIR_W`, `MIR_D`,
        `MIR_B` or `MIR_S`), or `MIR_VOID` when the call produces no
        value. A void
        call returns `-1`; a value call returns the fresh value id.

        The callee is a value id (a function's entry), so the call reads
        its arguments from `call_args`, keyed by instruction index: an
        instruction has two operand slots and a call reads a variable
        argument count.
        """
        if (
            result_type != MIR_W
            and result_type != MIR_D
            and result_type != MIR_B
            and result_type != MIR_S
            and result_type != MIR_P
            and result_type != MIR_VOID
        ):
            raise MirError("call result type must be MIR_W, MIR_B, MIR_D, MIR_S, MIR_P or MIR_VOID")
        dst: int = -1
        if result_type != MIR_VOID:
            dst = self.next_vid
            self.next_vid = self.next_vid + 1
        index: int = self.emit(MIR_CALL, dst, callee, result_type)
        self.fn.call_args[index] = list(args)
        return dst

    def emit_raise(self, code: int) -> None:
        """Raise the exception with the given code; ends the block.

        The code is a constant-pool operand (`const_w`'s encoding), the
        int the emitter renders as the `ps_raise_code` immediate.
        """
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("raise outside an open block")
        self.emit(MIR_RAISE, -1, code, none_ref())
        self.terminated = 1

    def emit_jmp(self, target: int) -> None:
        current: MirBlock | None = self.current
        if current is None or self.terminated:
            raise MirError("jmp outside an open block")
        self.emit(MIR_JMP, -1, target, none_ref())
        current.succ1 = target
        self.terminated = 1

    def set_arg(self, index: int, arg1: int, arg2: int) -> None:
        """Set an instruction's two operand slots by instruction index.

        The lowerer needs this when a branch's target is not known when
        the branch is emitted (a nested block in an arm opens more
        blocks, so the join block's id is only known once the arm is
        walked).
        """
        args1: list[int] = self.fn.arg1
        args2: list[int] = self.fn.arg2
        args1[index] = arg1
        args2[index] = arg2

    def set_succ(self, bid: int, succ1: int, succ2: int) -> None:
        """Set a block's successors by block id.

        The lowerer needs this when a block's successor is not known when
        the branch is emitted (a nested block in an arm opens more
        blocks, so the join block's id is only known once the arm is
        walked).
        """
        blocks: list[MirBlock] = self.fn.blocks
        block: MirBlock = blocks[bid]
        block.succ1 = succ1
        block.succ2 = succ2

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

    def current_bid(self) -> int:
        """The id of the open block, or -1 when no block is open.

        The lowerer needs the id of the block a statement's body ends
        in, to patch that block's forward jump once the target's id is
        final. Reading the id here keeps the optional-block type out of
        the lowerer's annotations.
        """
        current: MirBlock | None = self.current
        if current is None:
            return -1
        return current.bid

    def patch_succ1(self, bid: int, target: int) -> None:
        """Rewrite block `bid`'s first successor edge.

        The lowerer patches a forward jump's operand once the block it
        names opens; the successor edge lives on the block, and the
        rewrite stays here so the lowerer does not reach into the
        successor field of a block it only holds by id.
        """
        block = self.fn.blocks[bid]
        block.succ1 = target

    def patch_succ2(self, bid: int, target: int) -> None:
        """Rewrite block `bid`'s second successor edge; see `patch_succ1`."""
        block = self.fn.blocks[bid]
        block.succ2 = target

    def finish(self) -> MirFunction:
        """Return the function; refuses dangling open blocks."""
        current: MirBlock | None = self.current
        if current is not None and not self.terminated:
            raise MirError(f"block {current.bid} has no terminator")
        return self.fn


def float_bits(value: float) -> int:
    """The IEEE-754 bit pattern of a float as an unsigned int."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


_HEX_DIGITS = "0123456789abcdef"


def _utf8_hex(data: bytes) -> str:
    """Lowercase hex of a byte string, spelled with lowered operations only:
    `bytes.hex` is not in the self-host subset, so the digits come from a
    table walk over the bytes (a `for` over `bytes` yields ints)."""
    parts: list[str] = []
    for byte in data:
        parts.append(_HEX_DIGITS[byte >> 4])
        parts.append(_HEX_DIGITS[byte & 15])
    return "".join(parts)


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
        elif const.typ == MIR_S:
            # Hex, not a quoted string: the serializer grows no escape
            # dialect, and a newline or NUL in the constant cannot split
            # a line. The empty string renders `const N s 0` with no hex
            # token.
            data = const.sval.encode("utf-8")
            if data:
                lines.append(f"const {index} s {len(data)} {_utf8_hex(data)}")
            else:
                lines.append(f"const {index} s 0")
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
    f_arg3: list[int] = fn.arg3
    n_arg3: int = len(f_arg3)
    for i in range(len(ops)):
        a3: int = f_arg3[i] if i < n_arg3 else -1
        lines.append(f"instr {i} op{ops[i]} dst{dsts[i]} a1={f_arg1[i]} a2={f_arg2[i]} a3={a3}")
    # The call arguments render after the instruction stream, keyed by
    # instruction index. Insertion order is index order by construction
    # (`emit_call` appends at emission), so the bytes stay
    # construction-ordered; an empty dict renders nothing, and the
    # existing leaf functions keep their bytes.
    call_args: dict[int, list[int]] = fn.call_args
    for i in call_args:
        args_i: list[int] = call_args[i]
        spelled: str = ",".join(str(args_i[k]) for k in range(len(args_i)))
        lines.append(f"callargs {i} [{spelled}]")
    # The callee table renders after the call arguments, in value-id
    # order. The ids are allocated in allocation order, so the bytes stay
    # construction-ordered; an empty table renders nothing.
    callees: dict[int, str] = fn.callees
    for vid in callees:
        lines.append(f"callee v{vid} {callees[vid]}")
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
