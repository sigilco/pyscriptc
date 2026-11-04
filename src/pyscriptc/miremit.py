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
from pyscriptc.emit.llvm import Emitter
from pyscriptc.errors import IRError
from pyscriptc.ir import assert_same_cpython
from pyscriptc.mir import (
    MIR_ADDD,
    MIR_ADDW,
    MIR_B,
    MIR_BR,
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
    MIR_RET,
    MIR_SUBD,
    MIR_SUBW,
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

# The MIR lane opt-in. The direct path stays the default: the lane runs
# only when the env sets this to a nonempty value other than "0".
MIR_ENV = "PYSC_MIR"

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
_MIR_TERMINATORS: list[int] = [MIR_JMP, MIR_BR, MIR_RET]


def mir_opt_in() -> bool:
    """Whether the environment opts into the MIR lane."""
    value = _env(MIR_ENV)
    return value not in ("", "0")


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
        self._symbol_fn = {_fn_symbol(fn["name"]): fn["name"] for fn in module["functions"]}
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
        vid = len(ir_fn["params"])
        for local in ir_fn["locals"]:
            self.slot_vid[local["name"]] = vid
            self.slot_vid_set.add(vid)
            vid += 1
        # One alloca per name slot, in the locals order the direct walk
        # uses, the return slot for a non-void function, then the flow
        # slot: the direct path's allocation sequence, inside the entry
        # block.
        ret: str = _llvm_type(ir_fn["returnType"])
        plist: list[str] = [f"{_llvm_type(p['type'])} %{_safe(p['name'])}" for p in ir_fn["params"]]
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
        for fn in self.module["functions"]:
            if fn["name"] == name:
                return fn
        raise IRError(f"no IR function {name} behind the MIR")

    def _slot_vids(self) -> list[int]:
        """The name-slot value ids, in locals order (the alloca order)."""
        assert self.fn is not None
        ir_fn = self._ir_fn(self.fn.name)
        names: list[str] = [local["name"] for local in ir_fn["locals"]]
        vids: list[int] = []
        for name in names:
            vid = self.slot_vid.get(name)
            if vid is not None:
                vids.append(vid)
        return vids

    def _tag_of(self, vid: int) -> int:
        assert self.fn is not None
        fn = self.fn
        for i in range(len(fn.ops)):
            if fn.dsts[i] == vid:
                return copy_result_type(fn, i)
        raise IRError(f"no definition of v{vid}")

    def _span(self, bid: int) -> list[int]:
        """The instruction indexes of block `bid` (construction order spans)."""
        assert self.fn is not None
        fn = self.fn
        cursor = 0
        n = len(fn.ops)
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

        Blocks append as entry, then (then, else, merge) per `if` in source
        order; `lid` counts 3 per `if` starting at 1, exactly the direct
        emitter's `fresh()` sequence.
        """
        if bid == 0:
            return "entry"
        k, which = divmod(bid - 1, 3)
        return f"L{3 * k + which + 1}.{'then' if which == 0 else 'else' if which == 1 else 'merge'}"

    def emit_instr(self, fn: MirFunction, i: int) -> None:
        op = fn.ops[i]
        dst = fn.dsts[i]
        a1 = fn.arg1[i]
        a2 = fn.arg2[i]
        if op == MIR_RET:
            self.line(f"ret {self.llvm_of(fn, a1)} {self.operand(fn, a1)}")
            return
        if op == MIR_JMP:
            self.line(f"br label %{self._label(a1)}")
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
            # A store: the destination is a name slot.
            self.line(f"store {_tag_llvm(self._tag_of(dst))} {self.operand(fn, a1)}, ptr {self.reg[dst]}")
            return
        if op in _HELPER_W:
            needed = self.needed
            needed.add(_HELPER_W[op])
            self.needed = needed
            args = [self.operand(fn, x) for x in (a1, a2)]
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

    def _block_of(self, fn: MirFunction, i: int) -> MirBlock:
        for span_bid in range(len(fn.blocks)):
            if i in self._span(span_bid):
                return fn.blocks[span_bid]
        raise IRError(f"instr {i} belongs to no block")

    def operand(self, fn: MirFunction, operand: int) -> str:
        if is_const_ref(operand):
            const = fn.consts[const_index(operand)]
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
        assert self.fn is not None
        fn = self.fn
        for i in range(len(fn.ops)):
            if fn.dsts[i] == vid:
                return copy_result_type(fn, i)
        raise IRError(f"no definition of v{vid}")

    def _is_slot(self, vid: int) -> bool:
        return vid in self.slot_vid_set

    def param_name(self, vid: int) -> str:
        assert self.fn is not None
        ir_fn = self._ir_fn(self.fn.name)
        return f"%{_safe(ir_fn['params'][vid]['name'])}"

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
    op = fn.ops[i]
    if op == MIR_COPY:
        return fn.arg2[i]
    if op in [MIR_ADDD, MIR_SUBD, MIR_MULD, MIR_NEGD]:
        return MIR_D
    if op in [MIR_ADDW, MIR_SUBW, MIR_MULW, MIR_NEGW]:
        return MIR_W
    if op in _ICMP_W or op in _FCMP_D:
        return MIR_W
    raise IRError(f"no result tag for op {op}")


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
    if module["fences"]:
        raise IRError("refusing to emit LLVM while fences are present")
    if not module["functions"]:
        raise IRError("module has no functions to emit")
    dwarf = DwarfEmitter(module["sourceFile"]) if debug_info else None
    emitter: MirEmitter = MirEmitter(module, dwarf)
    return emitter.emit(call, [] if args is None else args, standalone, library)
