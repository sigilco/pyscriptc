"""LLVM emission from the 3AC MIR: the R4 leaf slice's backend lane.

The lane mirrors `emit/llvm/_driver.py`'s `emit_llvm` contract: the same
module dict goes in, the same LLVM text comes out, byte for byte, for the
functions the slice covers (`mirlower.supported_fn`). The MIR lane is a
strangler lane: an env var opts in, the direct path stays the default, and
a function outside the slice is refused here rather than half-emitted
(`mirlower.lower_module` reports the skipped names; `emit_llvm_mir` raises
rather than silently dropping one).

How byte-identity holds. The direct emitter is slot-shaped: every local is
an `alloca` plus a `store`/`load` pair per access. The MIR is value-shaped:
the lowerer's SSA shadows mean a name's live value is one MIR value id.
The emitter recovers the direct path's shape by counting, per MIR value,
its uses that are *reads from a name* (the `shadow` re-materializations the
lowerer emits as `MIR_COPY`): a value the body reads more than once gets
one alloca and one `store` per definition plus a `load` per read, in the
same instruction order the direct walk produces; a single-use value stays
a plain temp. Everything else is mechanical: the op vocabulary renders
one-to-one onto the direct emitter's helper calls (`ps.iadd` and friends
for the checked `w` arithmetic, plain `add`/`fadd` spellings, `icmp`/`fcmp`
for the compares), and block boundaries land on the same labels because the
lowerer's `emit_if` mirrors `_stmts.emit_if`'s label order (then, else,
merge).

The runtime side is not re-implemented: the header, the declaration table,
the helper bodies and the format globals come from the same `emit/llvm/`
modules the direct path uses, so a runtime change moves both lanes
together.
"""

from __future__ import annotations

from typing import Any, Dict

from pyscriptc.dwarf import DwarfEmitter
from pyscriptc.emit._ir import _fn_symbol, _llvm_double, _llvm_type, _safe
from pyscriptc.emit.llvm._driver import Emitter
from pyscriptc.errors import IRError
from pyscriptc.ir import assert_same_cpython
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
    MIR_JMP,
    MIR_MULD,
    MIR_MULW,
    MIR_NEGD,
    MIR_NEGW,
    MIR_NOTB,
    MIR_RAISE,
    MIR_RET,
    MIR_SUBD,
    MIR_SUBW,
    MIR_VOID,
    MIR_W,
    MirBlock,
    MirConst,
    MirFunction,
    MirModule,
    const_index,
    copy_result_type,
    is_const_ref,
)
from pyscriptc.mirlower import lower_module

# The MIR lane switch. One variable, three values the contract names:
#   PYSC_MIR=1  (MIR_ON)  force the MIR lane
#   PYSC_MIR=0  (MIR_OFF) force the direct emitter (the post-flip escape hatch)
#   unset/empty           today's default, MIR_DEFAULT (currently off)
# The owner-signed default flip is the one-line change `MIR_DEFAULT = True`.
# Callers must read the default only through `mir_opt_in()`; the CLI call
# site in `cli/_main.py` is the other reader.
MIR_ENV = "PYSC_MIR"
MIR_ON = "1"
MIR_OFF = "0"
MIR_DEFAULT = False

# The `icmp` predicate of each word comparison op, and the `fcmp` predicate
# of each double comparison op. The keys line up with `mirlower`'s tables.
_ICMP_W = {
    MIR_CLTW: "slt",
    MIR_CLEW: "sle",
    MIR_CGTW: "sgt",
    MIR_CGEW: "sge",
    MIR_CEQW: "eq",
    MIR_CNEW: "ne",
}
_FCMP_D = {
    MIR_CLTD: "olt",
    MIR_CLED: "ole",
    MIR_CGTD: "ogt",
    MIR_CGED: "oge",
    MIR_CEQD: "oeq",
    MIR_CNED: "une",
}

# The checked-helper arithmetic ops: `ps.iadd` and friends raise on
# overflow, so they render as calls, not as plain LLVM arithmetic.
_HELPER_W = {MIR_ADDW: "iadd", MIR_SUBW: "isub", MIR_MULW: "imul", MIR_NEGW: "ineg"}

# The plain arithmetic spellings, per tag.
_LLVM_W = {MIR_ADDW: "add", MIR_SUBW: "sub", MIR_MULW: "mul", MIR_NEGW: "sub"}
_LLVM_D = {MIR_ADDD: "fadd", MIR_SUBD: "fsub", MIR_MULD: "fmul", MIR_NEGD: "fneg"}


# The terminator ops, spelled here over the imported op constants: the
# module-level table in `mir.py` binds a global slot, and slots do not
# ride the package export table a cross-module import resolves against.
_MIR_TERMINATORS: list[int] = [MIR_JMP, MIR_BR, MIR_RET, MIR_RAISE]

# The op tables, spelled here for the same reason as `_MIR_TERMINATORS`:
# `mir.py`'s module-level tables bind global slots, and slots do not ride
# the package export table. The keys are the imported op constants, the
# values mirror `mir.py`'s tables; a drift is a mirverify or A/B
# byte-identity failure, not a silent wrong emission.
_OP_ARITY: Dict[int, int] = {
    MIR_NOTB: 1,
    MIR_RAISE: 1,
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
    MIR_COPY: 1,
}

_OP_RESULT_TYPE: Dict[int, int] = {
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
    MIR_COPY: MIR_W,
}


def mir_opt_in() -> bool:
    """Whether this process should emit through the MIR lane.

    `PYSC_MIR=0` is always off. Unset or empty follows `MIR_DEFAULT`
    (today: off). Any other nonempty value, including `PYSC_MIR=1`, is on.
    """
    value = _env(MIR_ENV)
    if value == MIR_OFF:
        return False
    if value == "":
        return MIR_DEFAULT
    return True


def _env(name: str) -> str:
    import os

    return os.environ.get(name, "")


class MirEmitter:
    """One module's MIR-to-LLVM walk. The MIR was verified before this runs.

    The field table is spelled as class-level bare annotations: the census
    lowerer collects fields from the `__init__` body, but its AnnAssign
    statement shape is not lowered, so a `self.x: T = ...` assignment fences
    every later `self.x` read. The bare-name form registers the field
    without a statement to lower.
    """

    module: Dict[str, Any]
    dwarf: DwarfEmitter | None
    wasm: int
    # The emitted symbol to the IR function name (main -> ps.user.main),
    # rebuilt per function walk below.
    _symbol_fn: Dict[str, str]
    out: list[str]
    globals: list[str]
    needed: set[str]
    abi_decls: Dict[str, tuple[str, list[str]]]
    tid: int
    # Per-function walk state.
    fn: MirFunction | None
    reg: Dict[int, str]
    slot_vid: Dict[str, int]
    slot_vid_set: set[int]

    def __init__(self, module: Dict[str, Any], dwarf: DwarfEmitter | None, wasm: bool = False) -> None:
        self.module = module
        self.dwarf = dwarf
        self.wasm = 1 if wasm else 0
        functions: list[Dict[str, Any]] = module["functions"]
        symbols: Dict[str, str] = {}
        for fn in functions:
            fn_name: str = fn["name"]
            symbols[_fn_symbol(fn_name)] = fn_name
        self._symbol_fn = symbols
        self.out = []
        self.globals = []
        self.needed = set()
        self.abi_decls = {}
        self.tid = 0
        # Per-function walk state.
        self.fn = None
        self.reg = {}
        self.slot_vid = {}
        self.slot_vid_set = set()

    # ---- module driver ----

    def emit(self, call: str | None, args: list[Any], standalone: bool, library: bool) -> str:
        mir_module = lower_module(self.module)
        by_name = {fn.name: fn for fn in mir_module.functions}
        # The strangler seam: in-slice functions emit from the MIR, the
        # rest ride the direct emitter. One Emitter renders the whole
        # module shape (classes, the entry point, the runtime tables); the
        # direct path's per-function bodies the MIR covers are replaced by
        # the MIR lane's text, which is byte-identical by construction.
        wasm_flag = self.wasm != 0
        emitter: Emitter = Emitter(self.module, self.dwarf, wasm=wasm_flag)
        direct = emitter.emit(call, args, standalone, library=library)
        if not mir_module.functions:
            return direct
        return self._splice(direct, mir_module, by_name)

    def _splice(self, direct: str, mir_module: MirModule, by_name: dict[str, MirFunction]) -> str:
        """Replace the direct text of each in-slice function with the MIR lane's.

        Function bodies are top-level `define ... }` blocks in emission
        order; the splice swaps the body lines in place, so the runtime
        header, the entry point and the trailing shims the direct path
        produces stay untouched.
        """
        lines = direct.split("\n")
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            name = self._define_name(line)
            if name is not None and name in by_name:
                i += 1
                while i < n and lines[i] != "}":
                    i += 1
                i += 1
                out.extend(self.emit_function_lines(by_name[name]))
                continue
            out.append(line)
            i += 1
        return "\n".join(out)

    def _define_name(self, line: str) -> str | None:
        """The user-function name of a `define` line, or None."""
        if not line.startswith("define ") or not line.endswith("{"):
            return None
        at = line.find(" @")
        if at < 0:
            return None
        rest = line[at + 2 :]
        paren = rest.find("(")
        if paren < 0:
            return None
        name = rest[:paren]
        # The entry points and the runtime helpers are not user functions.
        if name in ("main", "__main_argc_argv", "pyscriptc_main") or name.startswith("ps."):
            return None
        return self._symbol_fn.get(name, name)

    def emit_function_lines(self, fn: MirFunction) -> list[str]:
        """The MIR lane's lines for one function, without the trailing blank."""
        marker = len(self.out)
        self.emit_function(fn)
        lines = self.out[marker:]
        self.out = self.out[:marker]
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    # ---- function bodies ----

    def emit_function(self, fn: MirFunction) -> None:
        self.fn = fn
        self.tid = 0
        self.reg = {}
        ir_fn = self._ir_fn(fn.name)
        # The slot value ids follow the lowerer's allocation: one per
        # parameter, then one per local. Recomputing here keeps the emitter
        # independent of the lowerer's internals.
        self.slot_vid = {}
        self.slot_vid_set = set()
        fns: list[Dict[str, Any]] = ir_fn["params"]
        vid: int = len(fns)
        locs: list[Dict[str, Any]] = ir_fn["locals"]
        seen: set[int] = self.slot_vid_set
        for local in locs:
            lname: str = local["name"]
            ltyp: Dict[str, Any] = local["type"]
            lkind: str = ltyp["kind"]
            if lkind == "none":
                # A None-typed local is the singleton: the direct path
                # registers it with no slot and no alloca, so it takes no
                # value id here either.
                continue
            self.slot_vid[lname] = vid
            seen.add(vid)
            self.slot_vid_set = seen
            vid += 1
        # One alloca per name slot, in the locals order the direct walk
        # uses, the return slot for a non-void function, then the flow
        # slot: the direct path's allocation sequence, inside the entry
        # block.
        ret_type: Dict[str, Any] = ir_fn["returnType"]
        ret: str = _llvm_type(ret_type)
        plist: list[str] = []
        for p in fns:
            p_type: Dict[str, Any] = p["type"]
            p_name: str = p["name"]
            plist.append(f"{_llvm_type(p_type)} %{_safe(p_name)}")
        symbol = _fn_symbol(fn.name)
        out: list[str] = self.out
        out.append(f"define {ret} @{symbol}({', '.join(plist)}) {{")
        out.append("entry:")
        for slot_vid in self._slot_vids():
            self.reg[slot_vid] = self.tmp_reg()
            self.line(f"{self.reg[slot_vid]} = alloca {_tag_llvm(self._tag_of(slot_vid))}")
        if ret != "void":
            self.line(f"{self.tmp_reg()} = alloca {ret}")
        self.line(f"{self.tmp_reg()} = alloca i32")
        for i in self._span(0):
            self.emit_instr(fn, i)
        for bid in range(1, len(fn.blocks)):
            out.append(f"{self._label(bid)}:")
            for i in self._span(bid):
                self.emit_instr(fn, i)
        out.append("}")
        out.append("")
        self.out = out

    def _ir_fn(self, name: str) -> Dict[str, Any]:
        functions: list[Dict[str, Any]] = self.module["functions"]
        for fn in functions:
            fn_name: str = fn["name"]
            if fn_name == name:
                return fn
        raise IRError(f"no IR function {name} behind the MIR")

    def _slot_vids(self) -> list[int]:
        """The name-slot value ids, in locals order (the alloca order)."""
        current = self.fn
        if current is None:
            raise IRError("no function walk is active")
        ir_fn = self._ir_fn(current.name)
        names: list[str] = []
        walk_locals: list[Dict[str, Any]] = ir_fn["locals"]
        for local in walk_locals:
            lname: str = local["name"]
            ltyp: Dict[str, Any] = local["type"]
            lkind: str = ltyp["kind"]
            if lkind == "none":
                # The None singleton has no slot, so it takes no alloca.
                continue
            names.append(lname)
        vids: list[int] = []
        for name in names:
            vid = self.slot_vid.get(name)
            if vid is not None:
                vids.append(vid)
        return vids

    def _tag_of(self, vid: int) -> int:
        fn = self.fn
        if fn is None:
            raise IRError("no function walk is active")
        for i in range(len(fn.ops)):
            if fn.dsts[i] == vid:
                return copy_result_type(fn, i)
        raise IRError(f"no definition of v{vid}")

    def _span(self, bid: int) -> list[int]:
        """The instruction indexes of block `bid` (construction order spans)."""
        fn = self.fn
        if fn is None:
            raise IRError("no function walk is active")
        cursor: int = 0
        n: int = len(fn.ops)
        for _ in range(bid):
            while cursor < n and fn.ops[cursor] not in _MIR_TERMINATORS:
                cursor += 1
            cursor += 1
        start = cursor
        while cursor < n and fn.ops[cursor] not in _MIR_TERMINATORS:
            cursor += 1
        out: list[int] = []
        for i in range(start, cursor + 1):
            out.append(i)
        return out

    def _label(self, bid: int) -> str:
        """The direct path's label spelling of MIR block `bid`.

        Blocks with a hint (`while.cond` and friends, spelled by the
        lowerer) render `L<n>.<hint>`. Unhinted blocks append as entry,
        then (then, else, merge) per `if` in source order; `lid` counts 3
        per `if` starting at 1, exactly the direct emitter's `fresh()`
        sequence.
        """
        if bid == 0:
            return "entry"
        fn = self.fn
        if fn is None:
            raise IRError("no function walk is active")
        block = fn.blocks[bid]
        hint: str = block.hint
        if hint:
            # A lowered block carries the direct path's label number (the
            # `fresh()` sequence the lowerer replays), so a block nested
            # inside an arm keeps the number the direct emitter gave it.
            lid: int = block.label_id
            if lid > 0:
                return f"L{lid}.{hint}"
            return f"L{bid}.{hint}"
        k, which = divmod(bid - 1, 3)
        return f"L{3 * k + which + 1}.{'then' if which == 0 else 'else' if which == 1 else 'merge'}"

    def emit_instr(self, fn: MirFunction, i: int) -> None:
        op = fn.ops[i]
        dst = fn.dsts[i]
        a1 = fn.arg1[i]
        a2 = fn.arg2[i]
        if op == MIR_RET:
            # A void return reads no operand and renders `ret void`; a
            # value return renders the value's type and the value.
            if a2 == MIR_VOID:
                self.line("ret void")
                return
            self.line(f"ret {self.llvm_of(fn, a1)} {self.operand(fn, a1)}")
            return
        if op == MIR_JMP:
            self.line(f"br label %{self._label(a1)}")
            return
        if op == MIR_RAISE:
            code = self.operand(fn, a1)
            self.line(f"call void @ps_raise_code(i32 {code}) noreturn")
            self.line("unreachable")
            return
        if op == MIR_BR:
            block = self._block_of(fn, i)
            self.line(
                f"br i1 {self.operand(fn, a1)}, label %{self._label(block.succ1)}, label %{self._label(block.succ2)}"
            )
            return
        if op == MIR_COPY:
            entry = fn.helpers.get(i)
            if entry is not None:
                helper = entry.split(" ")[0]
                tag = int(entry.split(" ")[1])
                needed: set[str] = self.needed
                needed.add(helper)
                self.needed = needed
                llvm = _tag_llvm(tag)
                args = [self.operand(fn, x) for x in (a1, a2)]
                rendered = ", ".join(f"{llvm} {arg}" for arg in args)
                reg = self.tmp_reg()
                self.line(f"{reg} = call {llvm} @ps.{helper}({rendered})")
                self.reg[dst] = reg
                return
            if dst in self.slot_vid_set:
                # A store: the destination is a name slot.
                self.line(f"store {_tag_llvm(self._tag_of(dst))} {self.operand(fn, a1)}, ptr {self.reg[dst]}")
                return
            # A copy whose source is a name slot is a read: the lowerer
            # emits one copy per slot read, in source order, so the loads
            # land in the same order the direct walk produces.
            if a1 in self.slot_vid_set:
                llvm = _tag_llvm(self._tag_of(dst))
                reg = self.tmp_reg()
                self.line(f"{reg} = load {llvm}, ptr {self.reg[a1]}")
                self.reg[dst] = reg
                return
            self.reg[dst] = self.operand(fn, a1)
            return
        if op == MIR_CALL:
            self._emit_call(fn, i, dst, a1, a2)
            return
        if op in _HELPER_W:
            needed = self.needed
            needed.add(_HELPER_W[op])
            self.needed = needed
            # The checked helpers are unary (`ps.ineg`) or binary
            # (`ps.iadd`); the arity table says which, and the unused
            # operand slot of a unary op carries the `-1` sentinel that
            # must not render as an argument.
            args = [self.operand(fn, a1)]
            if _OP_ARITY[op] == 2:
                args.append(self.operand(fn, a2))
            rendered = ", ".join(f"i64 {arg}" for arg in args)
            reg = self.tmp_reg()
            self.line(f"{reg} = call i64 @ps.{_HELPER_W[op]}({rendered})")
            self.reg[dst] = reg
            return
        if op == MIR_NOTB:
            value = self.operand(fn, a1)
            reg = self.tmp_reg()
            self.line(f"{reg} = xor i1 {value}, true")
            self.reg[dst] = reg
            return
        if op in _LLVM_W:
            left = self.operand(fn, a1)
            right = self.operand(fn, a2)
            reg = self.tmp_reg()
            self.line(f"{reg} = {_LLVM_W[op]} i64 {left}, {right}")
            self.reg[dst] = reg
            return
        if op in _LLVM_D:
            left = self.operand(fn, a1)
            right = self.operand(fn, a2)
            reg = self.tmp_reg()
            if op == MIR_NEGD:
                self.line(f"{reg} = fneg double {left}")
            else:
                self.line(f"{reg} = {_LLVM_D[op]} double {left}, {right}")
            self.reg[dst] = reg
            return
        if op in _ICMP_W:
            left = self.operand(fn, a1)
            right = self.operand(fn, a2)
            reg = self.tmp_reg()
            self.line(f"{reg} = icmp {_ICMP_W[op]} i64 {left}, {right}")
            self.reg[dst] = reg
            return
        if op in _FCMP_D:
            left = self.operand(fn, a1)
            right = self.operand(fn, a2)
            reg = self.tmp_reg()
            self.line(f"{reg} = fcmp {_FCMP_D[op]} double {left}, {right}")
            self.reg[dst] = reg
            return
        raise IRError(f"the MIR leaf slice cannot emit op {op}")

    def _emit_call(self, fn: MirFunction, i: int, dst: int, callee_vid: int, tag: int) -> None:
        """A `MIR_CALL`: the callee's symbol, the args, the result type.

        The callee value id resolves through the function's callee table
        (name -> symbol via `_fn_symbol`, the direct emitter's spelling);
        the arguments ride `call_args` and each renders with its own LLVM
        type from the IR, byte-for-byte the direct `call_function`'s
        argument text. A void call renders bare and returns nothing; a
        value call takes a fresh temp.
        """
        name: str = fn.callees[callee_vid]
        ir_fn: Dict[str, Any] = self._ir_fn(name)
        ret_type: Dict[str, Any] = ir_fn["returnType"]
        ret: str = _llvm_type(ret_type)
        ir_params: list[Dict[str, Any]] = ir_fn["params"]
        args_i: list[int] = fn.call_args[i]
        parts: list[str] = []
        for position in range(len(args_i)):
            param: Dict[str, Any] = ir_params[position]
            p_type: Dict[str, Any] = param["type"]
            parts.append(f"{_llvm_type(p_type)} {self.operand(fn, args_i[position])}")
        symbol: str = _fn_symbol(name)
        rendered: str = ", ".join(parts)
        if ret == "void":
            self.line(f"call void @{symbol}({rendered})")
            return
        reg: str = self.tmp_reg()
        self.line(f"{reg} = call {ret} @{symbol}({rendered})")
        self.reg[dst] = reg

    def _block_of(self, fn: MirFunction, i: int) -> MirBlock:
        for span_bid in range(len(fn.blocks)):
            if i in self._span(span_bid):
                return fn.blocks[span_bid]
        raise IRError(f"instr {i} belongs to no block")

    def operand(self, fn: MirFunction, operand: int) -> str:
        if is_const_ref(operand):
            const = fn.consts[const_index(operand)]
            # A bool const is the pool's own tag: the direct path spells it
            # `true`/`false`, and only a word const reads `ival` as-is.
            if const.typ == MIR_B:
                return "true" if const.ival else "false"
            return str(const.ival) if const.typ == MIR_W else _llvm_double(const.dval)
        if operand in self.reg:
            slot = self.reg[operand]
            if self._is_slot(operand):
                reg = self.tmp_reg()
                self.line(f"{reg} = load {_tag_llvm(self._tag_of(operand))}, ptr {slot}")
                return reg
            return slot
        # A raw parameter read (the prologue store's source).
        return self.param_name(operand)

    def llvm_of(self, fn: MirFunction, operand: int) -> str:
        if is_const_ref(operand):
            return _tag_llvm(fn.consts[const_index(operand)].typ)
        if operand in self.slot_vid_set:
            return _tag_llvm(self._tag_of(operand))
        if operand in self.reg and not self._is_slot(operand):
            # A value op result: infer its tag from the defining instr.
            return _tag_llvm(self._def_tag(operand))
        return "i64"

    def _def_tag(self, vid: int) -> int:
        fn = self.fn
        if fn is None:
            raise IRError("no function walk is active")
        for i in range(len(fn.ops)):
            if fn.dsts[i] == vid:
                return _result_tag(fn, i)
        raise IRError(f"no definition of v{vid}")

    def _is_slot(self, vid: int) -> bool:
        return vid in self.slot_vid_set

    def param_name(self, vid: int) -> str:
        current = self.fn
        if current is None:
            raise IRError("no function walk is active")
        ir_fn = self._ir_fn(current.name)
        params: list[Dict[str, Any]] = ir_fn["params"]
        param: Dict[str, Any] = params[vid]
        pname: str = param["name"]
        return f"%{_safe(pname)}"

    def tmp_reg(self) -> str:
        nxt: int = self.tid + 1
        self.tid = nxt
        return f"%t{nxt}"

    def line(self, text: str) -> None:
        out: list[str] = self.out
        out.append(f"  {text}")
        self.out = out

    # ---- the entry points ----

    # ---- the entry points ----

    def emit_main(self, call: str, args: list[Any], library: bool) -> None:
        raise IRError("the MIR leaf slice does not emit a call entry yet")

    def emit_standalone(self, library: bool) -> None:
        raise IRError("the MIR leaf slice does not emit a standalone entry yet")

    def _abi_lines(self) -> list[str]:
        lines: list[str] = []
        for symbol, sig in self.abi_decls.items():
            ret: str = sig[0]
            params: list[str] = sig[1]
            rendered = ", ".join(params)
            lines.append(f"declare {ret} @{symbol}({rendered})")
        return lines


def _tag_llvm(tag: int) -> str:
    """The LLVM spelling of a MIR tag: w -> i64, b -> i1, d -> double."""
    if tag == MIR_D:
        return "double"
    if tag == MIR_B:
        return "i1"
    return "i64"


def _result_tag(fn: MirFunction, i: int) -> int:
    """The result tag of the value an instruction defines.

    Only a `MIR_COPY` carries its tag out of band (in `arg2`, or beside a
    helper name); every other value op takes its tag from the vocabulary's
    result-type table, so a compare is a `MIR_B` (i1) and a word op is a
    `MIR_W` whatever its operands happen to be.
    """
    op = fn.ops[i]
    if op == MIR_COPY:
        return copy_result_type(fn, i)
    if op == MIR_CALL:
        return fn.arg2[i]
    tag: int | None = _OP_RESULT_TYPE.get(op)
    if tag is None:
        raise IRError(f"no result tag for op {op}")
    return tag


def _llvm_of(fn: MirFunction, operand: int) -> str:
    if is_const_ref(operand):
        const: MirConst = fn.consts[const_index(operand)]
        return _tag_llvm(const.typ)
    return "i64"


def _llvm_of_dst(fn: MirFunction, dst: int) -> str:
    return "i64"


def emit_llvm_mir(
    module: Dict[str, Any],
    call: str | None = None,
    args: list[Any] | None = None,
    standalone: bool = False,
    debug_info: bool = False,
    library: bool = False,
    wasm: bool = False,
) -> str:
    """The MIR lane's `emit_llvm`. Same contract, same bytes, opt-in only."""
    assert_same_cpython(module)
    fences: list[Any] = module["fences"]
    if fences:
        raise IRError("refusing to emit LLVM while fences are present")
    functions: list[Any] = module["functions"]
    if not functions:
        raise IRError("module has no functions to emit")
    source_file: str = module["sourceFile"]
    dwarf = DwarfEmitter(source_file) if debug_info else None
    emitter: MirEmitter = MirEmitter(module, dwarf)
    call_args: list[Any] = []
    if args is not None:
        call_args = args
    return emitter.emit(call, call_args, standalone, library)
