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
from pyscriptc.emit._ir import (
    _class_fields,
    _field_name,
    _field_type,
    _fn_symbol,
    _items,
    _llvm_bytes,
    _llvm_double,
    _llvm_field_type,
    _llvm_type,
    _opt_str,
    _safe,
)
from pyscriptc.emit._mir_in import plan_module
from pyscriptc.emit.llvm._driver import Emitter
from pyscriptc.errors import IRError
from pyscriptc.ir import assert_same_cpython
from pyscriptc.mir import (
    MIR_ADDD,
    MIR_ADDW,
    MIR_ANDW,
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
    MIR_GETATTR,
    MIR_JMP,
    MIR_MULD,
    MIR_MULW,
    MIR_NEGD,
    MIR_NEGW,
    MIR_NEWOBJ,
    MIR_NEWSEQ,
    MIR_NOTB,
    MIR_ORW,
    MIR_P,
    MIR_RAISE,
    MIR_RET,
    MIR_S,
    MIR_SEQGET,
    MIR_SEQLEN,
    MIR_SEQPUSH,
    MIR_SEQSET,
    MIR_SETATTR,
    MIR_SUBD,
    MIR_SUBW,
    MIR_VOID,
    MIR_W,
    MIR_XORW,
    MirBlock,
    MirConst,
    MirFunction,
    MirModule,
    const_index,
    copy_result_type,
    is_const_ref,
)

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
_LLVM_W = {
    MIR_ADDW: "add",
    MIR_SUBW: "sub",
    MIR_MULW: "mul",
    MIR_NEGW: "sub",
    MIR_ANDW: "and",
    MIR_ORW: "or",
    MIR_XORW: "xor",
}
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
    MIR_EXTWD: 1,
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
    MIR_EXTWD: MIR_D,
    MIR_ANDW: MIR_W,
    MIR_NEWSEQ: MIR_P,
    MIR_SEQGET: MIR_W,  # a seqget's true tag rides arg3
    MIR_SEQSET: MIR_W,
    MIR_SEQPUSH: MIR_W,
    MIR_SEQLEN: MIR_W,
    MIR_ORW: MIR_W,
    MIR_XORW: MIR_W,
    MIR_NEWOBJ: MIR_P,
    MIR_SETATTR: MIR_W,
    MIR_GETATTR: MIR_P,  # a getattr's true tag rides arg3
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
    # Per-function str state: `_str_seq` is the ordered list of `@ps.str.N`
    # indices the direct emitter's body for the function being spliced
    # references — one per `string_value` materialization, in emission
    # order — and `_str_n` counts this function's materializations, which
    # is the order the MIR lane's str constants consume them.
    # `_hoisted` collects the `%ps.str` staging allocas a non-entry
    # block's str slot read would hoist into the entry block.
    _str_seq: list[int]
    _str_n: int
    _hoisted: list[str]
    _in_entry: int

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
        self._str_seq = []
        self._str_n = 0
        self._hoisted = []
        self._in_entry = 1

    # ---- module driver ----

    def emit(self, call: str | None, args: list[Any], standalone: bool, library: bool) -> str:
        # The B11 adapter owns the per-function MIR-or-direct decision and
        # records each fallback; `plan["mir"]` is the same `MirModule`
        # `lower_module` produced, so the splice below is unchanged.
        plan = plan_module(self.module)
        mir_module: MirModule = plan["mir"]
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
        # The format-global interning reads the direct walk's complete
        # global table: the splice replaces bodies only, so the globals
        # and declarations the direct emitter rendered for the same
        # functions are already final. Seeding keeps `format_global`'s
        # dedup on the same symbols the direct path picked (a `@ps.fmt.s`
        # suffix collision across the slice boundary would otherwise
        # reference a global the direct walk defined for other bytes).
        self.globals = list(emitter.globals)
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
                body_start = i
                i += 1
                while i < n and lines[i] != "}":
                    i += 1
                self._str_seq = _str_refs(lines[body_start:i])
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
        self._str_n = 0
        self._hoisted = []
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
            self.line(f"{self.reg[slot_vid]} = alloca {_mem_llvm(self._tag_of(slot_vid))}")
        if ret != "void":
            self.line(f"{self.tmp_reg()} = alloca {ret}")
        self.line(f"{self.tmp_reg()} = alloca i32")
        self._in_entry = 1
        for i in self._span(0):
            self.emit_instr(fn, i)
        self._in_entry = 0
        for bid in range(1, len(fn.blocks)):
            out.append(f"{self._label(bid)}:")
            for i in self._span(bid):
                self.emit_instr(fn, i)
        if self._hoisted:
            # A str slot read inside a non-entry block stages its widened
            # `%ps.str` value through an alloca the direct path hoists to
            # the entry block, just before the entry terminator. The MIR
            # lane collects the same allocas during the walk and lands
            # them at the same spot.
            start = 0
            for index in range(len(out) - 1, -1, -1):
                if out[index] == "entry:":
                    start = index
                    break
            at = len(out)
            for index in range(start + 1, len(out)):
                text = out[index]
                if text.startswith("  br ") or text.startswith("  ret ") or text == "  unreachable":
                    at = index
                    break
            out[at:at] = self._hoisted
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
        a3 = fn.arg3[i] if i < len(fn.arg3) else -1
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
                if helper.startswith("str_cmp_"):
                    self._emit_str_cmp(fn, dst, a1, a2, helper)
                    return
                if helper == "str_contains" or helper == "str_ncontains":
                    self._emit_str_contains(fn, dst, a1, a2, helper == "str_ncontains")
                    return
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
                # A store: the destination is a name slot. A str store
                # narrows the `%ps.str` value to the 16-byte `%ps.buf`
                # prefix the slot holds, the way the direct path's
                # `store` does.
                if self._tag_of(dst) == MIR_S:
                    self._store_str_slot(fn, dst, a1)
                    return
                self.line(f"store {_tag_llvm(self._tag_of(dst))} {self.operand(fn, a1)}, ptr {self.reg[dst]}")
                return
            # A copy whose source is a name slot is a read: the lowerer
            # emits one copy per slot read, in source order, so the loads
            # land in the same order the direct walk produces. A str read
            # widens the `%ps.buf` cell back to `%ps.str` through
            # `ps_str_counts`, the direct path's `_widen_cell`.
            if a1 in self.slot_vid_set:
                if self._tag_of(dst) == MIR_S:
                    self.reg[dst] = self._widen_str_slot(a1)
                    return
                llvm = _tag_llvm(self._tag_of(dst))
                reg = self.tmp_reg()
                self.line(f"{reg} = load {llvm}, ptr {self.reg[a1]}")
                self.reg[dst] = reg
                return
            self.reg[dst] = self.operand(fn, a1)
            return
        if op == MIR_NEWSEQ:
            needed = self.needed
            needed.add("seq")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call ptr @ps_seq_new(i64 {self.operand(fn, a1)}, i64 {self.operand(fn, a2)})")
            self.reg[dst] = reg
            return
        if op == MIR_SEQLEN:
            needed = self.needed
            needed.add("seq")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call i64 @ps_seq_len(ptr {self.operand(fn, a1)})")
            self.reg[dst] = reg
            return
        if op == MIR_SEQGET:
            needed = self.needed
            needed.add("seq")
            self.needed = needed
            tag: int = a3
            llvm: str = _tag_llvm(tag)
            out_slot: str = self._mir_alloca(llvm)
            self.line(f"call void @ps_seq_get(ptr {self.operand(fn, a1)}, i64 {self.operand(fn, a2)}, ptr {out_slot})")
            reg = self.tmp_reg()
            self.line(f"{reg} = load {llvm}, ptr {out_slot}")
            self.reg[dst] = reg
            return
        if op == MIR_SEQSET:
            needed = self.needed
            needed.add("seq")
            self.needed = needed
            value_tag: int = self._operand_tag(fn, a3)
            slot: str = self._stash_cell_tag(value_tag, self.operand(fn, a3))
            self.line(f"call void @ps_seq_set(ptr {self.operand(fn, a1)}, i64 {self.operand(fn, a2)}, ptr {slot})")
            return
        if op == MIR_SEQPUSH:
            needed = self.needed
            needed.add("seq")
            self.needed = needed
            value_tag = self._operand_tag(fn, a2)
            slot = self._stash_cell_tag(value_tag, self.operand(fn, a2))
            self.line(f"call void @ps_seq_push(ptr {self.operand(fn, a1)}, ptr {slot})")
            return
        if op == MIR_CALL:
            self._emit_call(fn, i, dst, a1, a2)
            return
        if op == MIR_NEWOBJ:
            self._emit_newobj(fn, dst, a1)
            return
        if op == MIR_SETATTR:
            self._emit_setattr(fn, a1, a2, a3)
            return
        if op == MIR_GETATTR:
            self._emit_getattr(fn, dst, a1, a2)
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
        if op == MIR_EXTWD:
            # `int_to_float`: the direct path's `sitofp i64 v to double`.
            value = self.operand(fn, a1)
            reg = self.tmp_reg()
            self.line(f"{reg} = sitofp i64 {value} to double")
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
        marker: str | None = fn.helpers.get(i)
        if marker == "print":
            # The builtin `print` marks its call out of band: a user
            # function named `print` shares the callee-table name, so the
            # name alone cannot tell the two apart.
            self._emit_print(fn, i, fn.call_args[i])
            return
        if name.startswith("rt:"):
            # A runtime-ABI call: the `rt:` prefix marks a `ps_*` symbol
            # the lowerer admitted, not a module function name.
            self._emit_rt_call(fn, i, dst, name[3:], tag)
            return
        if marker is not None and marker.startswith("builtin:"):
            self._emit_builtin_call(fn, i, dst, fn.call_args[i], marker[len("builtin:") :])
            return
        if marker is not None and marker.startswith("abi "):
            self._emit_abi_call(fn, i, dst, tag, fn.call_args[i], marker[len("abi ") :])
            return
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

    def _emit_print(self, fn: MirFunction, i: int, args_i: list[int]) -> None:
        """The `print` builtin's emission: the direct `_print_one` walk.

        The slice admits scalar and str arguments (the slice test
        refuses every other kind before this runs), so the value
        spellings are the arms of `_print_one`: an int is one `printf`
        with the `%lld` format global, a bool selects between the
        `True`/`False` globals and prints through `%s`, a float renders
        `ps_f64_str` and the `%.*s` pair, a str extracts its length and
        data pointer for the same `%.*s` pair. The separator and the
        terminator match the defaults (space, newline); the slice test
        refuses `sep=`/`end=`.

        The argument kinds come from the MIR tags, not from the IR: a
        `MIR_W` is an int, a `MIR_B` a bool, a `MIR_D` a float and a
        `MIR_S` a str, the same tags the leaf slice admits.
        `self.needed`,
        `self.globals` and `self.abi_decls` are the same stores the
        direct emitter reads, so the declarations land identically.
        """
        needed: set[str] = self.needed
        needed.add("printf")
        self.needed = needed
        for position in range(len(args_i)):
            if position:
                self._print_fmt("space", b" ")
            value: str = self.operand(fn, args_i[position])
            tag: int = self._operand_tag(fn, args_i[position])
            if tag == MIR_B:
                picked = self.tmp_reg()
                self.line(
                    f"{picked} = select i1 {value}, ptr {self.format_global('boolT', b'True')}, "
                    f"ptr {self.format_global('boolF', b'False')}"
                )
                self._print_fmt("s", b"%s", [f"ptr {picked}"])
            elif tag == MIR_D:
                out = self._buf_out("ps_f64_str", [f"double {value}"])
                length, ptr = self._buf_parts(out)
                self._abi_decl("ps_f64_str", "void", ["double", "ptr"])
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"])
            elif tag == MIR_S:
                length, ptr = self._buf_parts(value)
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"])
            else:
                self._print_fmt("int", b"%lld", [f"i64 {value}"])
        self._print_fmt("nl", b"\n")

    def _operand_tag(self, fn: MirFunction, operand: int) -> int:
        """The value tag of an operand: a constant reads the pool; a value
        reads the tag of the instruction that defined it."""
        if is_const_ref(operand):
            const = fn.consts[const_index(operand)]
            return const.typ
        return self._def_tag(operand)

    def _emit_builtin_call(self, fn: MirFunction, i: int, dst: int, args_i: list[int], name: str) -> None:
        """An admitted builtin call's emission: the direct `emit_builtin` arms.

        `len` of a str is the code-point count (field 2); `str` renders an
        int through `ps_i64_str`, a float through `ps_f64_str` and a bool
        through the `True`/`False` select aggregate; `int` of a str reads
        `ps_int_from_bytes` off the buffer parts and `int` of a float
        calls `ps_float_to_int`. Each spelling matches the direct
        emitter's line order.
        """
        value: str = self.operand(fn, args_i[0])
        vtag: int = self._operand_tag(fn, args_i[0])
        if name == "len":
            reg = self.tmp_reg()
            self.line(f"{reg} = extractvalue %ps.str {value}, 2")
            self.reg[dst] = reg
            return
        if name == "str":
            if vtag == MIR_S:
                self.reg[dst] = value
                return
            if vtag == MIR_B:
                t_len = self.tmp_reg()
                t_ptr = self.tmp_reg()
                self.line(f"{t_len} = select i1 {value}, i64 4, i64 5")
                self.line(
                    f"{t_ptr} = select i1 {value}, ptr {self.format_global('boolT', b'True')}, "
                    f"ptr {self.format_global('boolF', b'False')}"
                )
                agg = self.tmp_reg()
                p1 = self.tmp_reg()
                p2 = self.tmp_reg()
                ret = self.tmp_reg()
                self.line(f"{agg} = insertvalue %ps.str poison, i64 {t_len}, 0")
                self.line(f"{p1} = insertvalue %ps.str {agg}, ptr {t_ptr}, 1")
                self.line(f"{p2} = insertvalue %ps.str {p1}, i64 {t_len}, 2")
                self.line(f"{ret} = insertvalue %ps.str {p2}, i32 1, 3")
                self.reg[dst] = ret
                return
            symbol = "ps_i64_str" if vtag == MIR_W else "ps_f64_str"
            piece = "i64" if vtag == MIR_W else "double"
            self._abi_decl(symbol, "void", [piece, "ptr"])
            self.reg[dst] = self._buf_out(symbol, [f"{piece} {value}"])
            return
        # name == "int"
        if vtag == MIR_S:
            self._abi_decl("ps_int_from_bytes", "i64", ["i64", "ptr"])
            length, ptr = self._buf_parts(value)
            reg = self.tmp_reg()
            self.line(f"{reg} = call i64 @ps_int_from_bytes(i64 {length}, ptr {ptr})")
            self.reg[dst] = reg
            return
        needed: set[str] = self.needed
        needed.add("math")
        self.needed = needed
        reg = self.tmp_reg()
        self.line(f"{reg} = call i64 @ps_float_to_int(double {value})")
        self.reg[dst] = reg

    def _emit_abi_call(self, fn: MirFunction, i: int, dst: int, tag: int, args_i: list[int], symbol: str) -> None:
        """A `builtins` module call's emission: the direct `_emit_abi` walk.

        Each argument renders its C pieces off the MIR tag (a str splits
        into length and data pointer through `_buf_parts`, the scalars
        spell directly); the return kind rides the call's result tag — a
        str return goes through `_buf_out`'s buffer shape, a scalar
        return through a plain `call`.
        """
        rendered: list[str] = []
        types: list[str] = []
        for arg_i in args_i:
            value = self.operand(fn, arg_i)
            vtag = self._operand_tag(fn, arg_i)
            if vtag == MIR_S:
                length, ptr = self._buf_parts(value)
                rendered.append(f"i64 {length}")
                rendered.append(f"ptr {ptr}")
                types.append("i64")
                types.append("ptr")
            elif vtag == MIR_D:
                rendered.append(f"double {value}")
                types.append("double")
            else:
                rendered.append(f"i64 {value}")
                types.append("i64")
        if tag == MIR_S:
            abi_params: list[str] = list(types)
            abi_params.append("ptr")
            self._abi_decl(symbol, "void", abi_params)
            self.reg[dst] = self._buf_out(symbol, rendered)
            return
        ret = "i64" if tag == MIR_W else "double"
        self._abi_decl(symbol, ret, types)
        reg = self.tmp_reg()
        self.line(f"{reg} = call {ret} @{symbol}({', '.join(rendered)})")
        self.reg[dst] = reg

    def _print_fmt(self, name: str, data: bytes, args: list[str] | None = None) -> None:
        """The direct `_print_fmt`'s stdout spelling (the slice prints to stdout only)."""
        fmt = self.format_global(name, data)
        call_args: list[str] = []
        if args is not None:
            call_args = list(args)
        parts: list[str] = [f"ptr {fmt}"]
        if data.startswith(b"%.*s"):
            self._abi_decl("ps_stream_write", "void", ["ptr", "ptr", "i64"])
            self._abi_decl("ps_stdout_stream", "ptr", [])
            stream = self.tmp_reg()
            self.line(f"{stream} = call ptr @ps_stdout_stream()")
            tail = data[4:]
            alen = ""
            for index in range(len(call_args)):
                arg = call_args[index]
                if index % 2 == 0:
                    alen = arg[4:]
                else:
                    self.line(f"call void @ps_stream_write(ptr {stream}, ptr {arg[4:]}, i64 {alen})")
            if tail:
                self._print_fmt("plain", tail)
            return
        for index in range(len(call_args)):
            parts.append(call_args[index])
        rendered = ", ".join(parts)
        self.line(f"call i32 (ptr, ...) @printf({rendered})")

    def format_global(self, name: str, data: bytes) -> str:
        """The direct emitter's format-global interning: same symbol, same dedup."""
        base = f"@ps.fmt.{name}"
        body = f'private unnamed_addr constant [{len(data) + 1} x i8] c"{_llvm_bytes(data)}"'
        defined = {g.split(" = ", 1)[0] for g in self.globals}
        symbol = base
        suffix = 0
        while symbol in defined and f"{symbol} = {body}" not in self.globals:
            suffix += 1
            symbol = f"{base}.{suffix}"
        if symbol not in defined:
            globals_: list[str] = self.globals
            globals_.append(f"{symbol} = {body}")
        return symbol

    def _buf_out(self, symbol: str, args: list[str]) -> str:
        """The direct `_buf_out`'s spelling: alloca, call, the four loads, the scan.

        The buffer alloca hoists into the entry block the way the direct
        emitter's `_alloca` does: an alloca inside a loop body would
        grow the stack per iteration, so the allocation happens once per
        call and the block reuses it.
        """
        out = self._mir_alloca("%ps.str")
        # The buffer pointer rides last, the direct `_buf_out`'s argument
        # order: the runtime writes the 16-byte prefix through it.
        call_args: list[str] = list(args)
        call_args.append(f"ptr {out}")
        self.line(f"call void @{symbol}({', '.join(call_args)})")
        count_ptr = self.tmp_reg()
        flags_ptr = self.tmp_reg()
        length = self.tmp_reg()
        raw_ptr = self.tmp_reg()
        data_ptr = self.tmp_reg()
        self.line(f"{length} = load i64, ptr {out}")
        self.line(f"{raw_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 1")
        self.line(f"{data_ptr} = load ptr, ptr {raw_ptr}")
        self.line(f"{count_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 2")
        self.line(f"{flags_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 3")
        self._abi_decl("ps_str_counts", "void", ["i64", "ptr", "ptr", "ptr"])
        self.line(f"call void @ps_str_counts(i64 {length}, ptr {data_ptr}, ptr {count_ptr}, ptr {flags_ptr})")
        reg = self.tmp_reg()
        self.line(f"{reg} = load %ps.str, ptr {out}")
        return reg

    def _buf_parts(self, value: str) -> tuple[str, str]:
        length = self.tmp_reg()
        ptr = self.tmp_reg()
        self.line(f"{length} = extractvalue %ps.str {value}, 0")
        self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
        return length, ptr

    def _abi_decl(self, symbol: str, ret: str, params: list[str]) -> None:
        self.abi_decls[symbol] = (ret, params)

    def _emit_rt_call(self, fn: MirFunction, i: int, dst: int, symbol: str, tag: int) -> None:
        """A runtime-ABI call: the direct emitter's `_emit_abi`/`_emit_math`
        shape, spelled from the operand tags.

        Two passes mirror the direct order: every argument's value
        renders first (a str const materializes here, in argument order),
        then each str argument's `(length, data)` extract pair, then the
        call. The result renders by the call's tag: `i64`/`double`/`ptr`
        calls take a fresh temp, a bool result is the `i32` call plus the
        `icmp ne` flag, and a void call renders bare.
        """
        args_i: list[int] = fn.call_args[i]
        values: list[str] = []
        tags: list[int] = []
        for position in range(len(args_i)):
            operand: int = args_i[position]
            otag: int = self._operand_tag(fn, operand)
            values.append(self.operand(fn, operand))
            tags.append(otag)
        parts: list[str] = []
        for position in range(len(args_i)):
            otag = tags[position]
            value = values[position]
            if otag == MIR_S:
                length: str = self.tmp_reg()
                ptr: str = self.tmp_reg()
                self.line(f"{length} = extractvalue %ps.str {value}, 0")
                self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
                parts.append(f"i64 {length}")
                parts.append(f"ptr {ptr}")
            elif otag == MIR_D:
                parts.append(f"double {value}")
            elif otag == MIR_P:
                parts.append(f"ptr {value}")
            elif otag == MIR_W:
                parts.append(f"i64 {value}")
            else:
                raise IRError(f"cannot pass MIR tag {otag} to a runtime call")
        rendered: str = ", ".join(parts)
        if tag == MIR_VOID:
            self.line(f"call void @{symbol}({rendered})")
            return
        if tag == MIR_B:
            raw: str = self.tmp_reg()
            self.line(f"{raw} = call i32 @{symbol}({rendered})")
            reg: str = self.tmp_reg()
            self.line(f"{reg} = icmp ne i32 {raw}, 0")
            self.reg[dst] = reg
            return
        llvm: str = _tag_llvm(tag)
        reg2: str = self.tmp_reg()
        self.line(f"{reg2} = call {llvm} @{symbol}({rendered})")
        self.reg[dst] = reg2

    def _mir_class(self, name: str) -> Dict[str, Any]:
        """The module's class record for `name`, or an `IRError`."""
        for cls in _items(self.module, "classes"):
            if _opt_str(cls, "name") == name:
                return cls
        raise IRError(f"the MIR lane has no class record for {name}")

    def _mir_field(self, cls_name: str, field_name: str) -> tuple[Dict[str, Any], int]:
        """One class field record and its struct index, the direct
        `_field`'s enumerate order."""
        cls: Dict[str, Any] = self._mir_class(cls_name)
        fields: list[Dict[str, Any]] = _class_fields(cls)
        for index in range(len(fields)):
            field: Dict[str, Any] = fields[index]
            if _field_name(field) == field_name:
                return field, index
        raise IRError(f"{cls_name}.{field_name} is not a field")

    def _class_field_key(self, fn: MirFunction, operand: int) -> tuple[str, str]:
        """The `<class>.<field>` key a class op's `MIR_S` constant carries."""
        const: MirConst = fn.consts[const_index(operand)]
        key: str = const.sval
        dot: int = key.find(".")
        if dot < 0:
            raise IRError(f"the MIR lane cannot read class key {key!r}")
        cls_name: str = key[:dot]
        field_name: str = key[dot + 1 :]
        return cls_name, field_name

    def _emit_newobj(self, fn: MirFunction, dst: int, a1: int) -> None:
        """A class constructor call: the direct `emit_constructor` shape.

        `ps_alloc` over the `%cls.<name>` struct size, three temporaries
        in the direct order (the size gep, the ptrtoint, the call). The
        synthesized `tuple_N_*` classes carry no methods, so no `__init__`
        call follows; a class that grew one is outside the slice and
        fails loudly here rather than half-emitting.
        """
        const: MirConst = fn.consts[const_index(a1)]
        name: str = const.sval
        cls: Dict[str, Any] = self._mir_class(name)
        for method in _items(cls, "methods"):
            if _opt_str(method, "name") == "__init__":
                raise IRError(f"the MIR leaf slice cannot lower {name}.__init__")
        needed: set[str] = self.needed
        needed.add("alloc")
        self.needed = needed
        size_ptr: str = self.tmp_reg()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        size: str = self.tmp_reg()
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        reg: str = self.tmp_reg()
        self.line(f"{reg} = call ptr @ps_alloc(i64 {size})")
        self.reg[dst] = reg

    def _emit_setattr(self, fn: MirFunction, a1: int, a2: int, a3: int) -> None:
        """A field store: object and value already evaluated, then the
        direct `emit_setattr`'s field gep and typed store."""
        obj: str = self.operand(fn, a1)
        value: str = self.operand(fn, a2)
        cls_name, field_name = self._class_field_key(fn, a3)
        field, index = self._mir_field(cls_name, field_name)
        field_type: Dict[str, Any] = _field_type(field)
        slot: str = self.tmp_reg()
        self.line(f"{slot} = getelementptr %cls.{cls_name}, ptr {obj}, i32 0, i32 {index}")
        self.line(f"store {_llvm_field_type(field_type)} {value}, ptr {slot}")

    def _emit_getattr(self, fn: MirFunction, dst: int, a1: int, a2: int) -> None:
        """A field load: the direct `emit_attr`'s field gep and `_load`,
        the result register the `MIR_GETATTR` dst names."""
        obj: str = self.operand(fn, a1)
        cls_name, field_name = self._class_field_key(fn, a2)
        field, index = self._mir_field(cls_name, field_name)
        field_type: Dict[str, Any] = _field_type(field)
        slot: str = self.tmp_reg()
        self.line(f"{slot} = getelementptr %cls.{cls_name}, ptr {obj}, i32 0, i32 {index}")
        reg: str = self.tmp_reg()
        self.line(f"{reg} = load {_llvm_type(field_type)}, ptr {slot}")
        self.reg[dst] = reg

    def _block_of(self, fn: MirFunction, i: int) -> MirBlock:
        for span_bid in range(len(fn.blocks)):
            if i in self._span(span_bid):
                return fn.blocks[span_bid]
        raise IRError(f"instr {i} belongs to no block")

    def _str_const(self, fn: MirFunction, const: MirConst) -> str:
        """Materialize a `MIR_S` constant as a `%ps.str` SSA value.

        The global already exists: the direct emitter interned the same
        literal into `@ps.str.N` while it built the text this lane
        splices into. `_str_seq` is that body's `@ps.str.N` references in
        emission order and `_str_n` counts this function's
        materializations, which is the direct path's `string_value`
        order. The four `insertvalue` lines mirror `string_value` byte
        for byte.
        """
        index = self._str_seq[self._str_n]
        self._str_n = self._str_n + 1
        text = const.sval
        data = text.encode("utf-8")
        count = len(text)
        ascii_flag = 1 if count == len(data) else 0
        parts = self.tmp_reg()
        first = self.tmp_reg()
        second = self.tmp_reg()
        third = self.tmp_reg()
        self.line(f"{parts} = insertvalue %ps.str poison, i64 {len(data)}, 0")
        self.line(f"{first} = insertvalue %ps.str {parts}, ptr @ps.str.{index}, 1")
        self.line(f"{second} = insertvalue %ps.str {first}, i64 {count}, 2")
        self.line(f"{third} = insertvalue %ps.str {second}, i32 {ascii_flag}, 3")
        return third

    def _store_str_slot(self, fn: MirFunction, dst: int, a1: int) -> None:
        """A store of a `%ps.str` value into a `%ps.buf` name slot.

        The direct path's `store` extracts the length and data pointer
        and writes the two `%ps.buf` fields; the count and flags are not
        stored (the cell is the 16-byte prefix only).
        """
        value = self.operand(fn, a1)
        slot = self.reg[dst]
        length = self.tmp_reg()
        ptr_val = self.tmp_reg()
        self.line(f"{length} = extractvalue %ps.str {value}, 0")
        self.line(f"{ptr_val} = extractvalue %ps.str {value}, 1")
        len_ptr = self.tmp_reg()
        data_ptr = self.tmp_reg()
        self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
        self.line(f"store i64 {length}, ptr {len_ptr}")
        self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
        self.line(f"store ptr {ptr_val}, ptr {data_ptr}")

    def _mir_alloca(self, llvm_type: str) -> str:
        """An alloca in the direct path's shape: inline in the entry
        block, hoisted to just before the entry terminator otherwise."""
        reg = self.tmp_reg()
        text = f"{reg} = alloca {llvm_type}"
        if self._in_entry:
            self.line(text)
        else:
            hoisted = self._hoisted
            hoisted.append(f"  {text}")
        return reg

    def _operand_tag(self, fn: MirFunction, operand: int) -> int:
        """The MIR tag of an operand the sequence stores stash."""
        if is_const_ref(operand):
            return fn.consts[const_index(operand)].typ
        current = self.fn
        if current is None:
            raise IRError("no function walk is active")
        ir_params: list[Dict[str, Any]] = self._ir_fn(current.name)["params"]
        if operand < len(ir_params):
            p_type: Dict[str, Any] = ir_params[operand]["type"]
            kind: str = p_type["kind"]
            if kind == "float":
                return MIR_D
            if kind == "bool":
                return MIR_B
            if kind == "str":
                return MIR_S
            if kind == "list" or kind == "class":
                return MIR_P
            return MIR_W
        if operand in self.slot_vid_set:
            return self._tag_of(operand)
        return self._def_tag(operand)

    def _stash_cell_tag(self, tag: int, value: str) -> str:
        """Stash one scalar/pointer value into a runtime cell, the direct
        path's `_stash_cell` shape for the non-str kinds this slice admits."""
        llvm: str = _tag_llvm(tag)
        slot: str = self._mir_alloca(llvm)
        self.line(f"store {llvm} {value}, ptr {slot}")
        return slot

    def _widen_str_slot(self, a1: int) -> str:
        """Read a str name slot: widen the `%ps.buf` cell to `%ps.str`.

        Mirrors the direct path's `_widen_cell` for `str`: load the
        length and data pointer out of the cell, rebuild the aggregate
        with a zero count and flag, stage it through a `%ps.str` alloca,
        and let `ps_str_counts` refill the count and flags from the
        bytes. The staging alloca hoists out of non-entry blocks exactly
        like the direct path's.
        """
        slot = self.reg[a1]
        length = self.tmp_reg()
        ptr = self.tmp_reg()
        self.line(f"{length} = load i64, ptr {slot}")
        data_ptr = self.tmp_reg()
        self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
        self.line(f"{ptr} = load ptr, ptr {data_ptr}")
        first = self.tmp_reg()
        second = self.tmp_reg()
        third = self.tmp_reg()
        reg = self.tmp_reg()
        self.line(f"{first} = insertvalue %ps.str poison, i64 {length}, 0")
        self.line(f"{second} = insertvalue %ps.str {first}, ptr {ptr}, 1")
        self.line(f"{third} = insertvalue %ps.str {second}, i64 0, 2")
        self.line(f"{reg} = insertvalue %ps.str {third}, i32 0, 3")
        slot_ptr = self._mir_alloca("%ps.str")
        count_ptr = self.tmp_reg()
        flags_ptr = self.tmp_reg()
        self.line(f"store %ps.str {reg}, ptr {slot_ptr}")
        self.line(f"{count_ptr} = getelementptr %ps.str, ptr {slot_ptr}, i32 0, i32 2")
        self.line(f"{flags_ptr} = getelementptr %ps.str, ptr {slot_ptr}, i32 0, i32 3")
        self.line(f"call void @ps_str_counts(i64 {length}, ptr {ptr}, ptr {count_ptr}, ptr {flags_ptr})")
        final = self.tmp_reg()
        self.line(f"{final} = load %ps.str, ptr {slot_ptr}")
        return final

    def _emit_str_cmp(self, fn: MirFunction, dst: int, a1: int, a2: int, helper: str) -> None:
        """A str `==`/`!=`: the direct path's `ps_str_cmp` sequence.

        Both operands evaluate first (a const materializes, a slot read
        widens), then the result register is taken before the extract
        temporaries — the same `tmp()` order the direct emitter's
        `emit_compare` produces — and the sequence is `_buf_cmp`'s four
        extracts, the `ps_str_cmp` call, and the `icmp eq`/`ne` against
        0.
        """
        left = self.operand(fn, a1)
        right = self.operand(fn, a2)
        reg = self.tmp_reg()
        left_len = self.tmp_reg()
        left_ptr = self.tmp_reg()
        self.line(f"{left_len} = extractvalue %ps.str {left}, 0")
        self.line(f"{left_ptr} = extractvalue %ps.str {left}, 1")
        right_len = self.tmp_reg()
        right_ptr = self.tmp_reg()
        self.line(f"{right_len} = extractvalue %ps.str {right}, 0")
        self.line(f"{right_ptr} = extractvalue %ps.str {right}, 1")
        cmp = self.tmp_reg()
        self.line(f"{cmp} = call i32 @ps_str_cmp(i64 {left_len}, ptr {left_ptr}, i64 {right_len}, ptr {right_ptr})")
        pred = helper.rsplit("_", 1)[1]
        self.line(f"{reg} = icmp {pred} i32 {cmp}, 0")
        self.reg[dst] = reg

    def _emit_str_contains(self, fn: MirFunction, dst: int, a1: int, a2: int, negated: bool) -> None:
        """A str `in`/`not in`: the direct path's `ps_str_find` sequence.

        `a1` is the haystack and `a2` the needle (the lowerer's operand
        order). The direct `emit_contains` evaluates the needle before
        the haystack — both already lowered — then emits the haystack's
        buffer parts, the needle's, the `ps_str_find` call and the
        `icmp sge`; `not in` adds the `xor i1` tail. The register order
        matches the direct `tmp()` sequence: found, flag, then the
        negation register.
        """
        needed: set[str] = self.needed
        needed.add("str")
        self.needed = needed
        hay = self.operand(fn, a1)
        needle = self.operand(fn, a2)
        hay_len, hay_ptr = self._buf_parts(hay)
        needle_len, needle_ptr = self._buf_parts(needle)
        found = self.tmp_reg()
        self.line(f"{found} = call i64 @ps_str_find(i64 {hay_len}, ptr {hay_ptr}, i64 {needle_len}, ptr {needle_ptr})")
        flag = self.tmp_reg()
        self.line(f"{flag} = icmp sge i64 {found}, 0")
        if negated:
            reg = self.tmp_reg()
            self.line(f"{reg} = xor i1 {flag}, true")
            self.reg[dst] = reg
            return
        self.reg[dst] = flag

    def operand(self, fn: MirFunction, operand: int) -> str:
        if is_const_ref(operand):
            const = fn.consts[const_index(operand)]
            # A bool const is the pool's own tag: the direct path spells it
            # `true`/`false`, and only a word const reads `ival` as-is. A
            # str const materializes as a `%ps.str` value.
            if const.typ == MIR_B:
                return "true" if const.ival else "false"
            if const.typ == MIR_S:
                return self._str_const(fn, const)
            return str(const.ival) if const.typ == MIR_W else _llvm_double(const.dval)
        if operand in self.reg:
            slot = self.reg[operand]
            if self._is_slot(operand):
                if self._tag_of(operand) == MIR_S:
                    return self._widen_str_slot(operand)
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


def _str_refs(lines: list[str]) -> list[int]:
    """The `@ps.str.N` indices a direct-emitted body references, in order.

    Every `string_value` materialization emits exactly one `ptr @ps.str.N`
    operand inside its `insertvalue` sequence, so the references in line
    order are the materializations in emission order — the sequence the
    MIR lane's str constants consume.
    """
    refs: list[int] = []
    marker = "@ps.str."
    for line in lines:
        pos = line.find(marker)
        if pos < 0:
            continue
        tail = line[pos + len(marker) :]
        digits = ""
        for ch in tail:
            if not ch.isdigit():
                break
            digits += ch
        refs.append(int(digits))
    return refs


def _tag_llvm(tag: int) -> str:
    """The LLVM spelling of a MIR tag: w -> i64, b -> i1, d -> double,
    s -> the `%ps.str` aggregate, p -> ptr."""
    if tag == MIR_D:
        return "double"
    if tag == MIR_B:
        return "i1"
    if tag == MIR_S:
        return "%ps.str"
    if tag == MIR_P:
        return "ptr"
    return "i64"


def _mem_llvm(tag: int) -> str:
    """The LLVM spelling of a MIR tag at rest in a name slot: a str narrows
    to the 16-byte `%ps.buf` prefix the runtime cells carry; every other
    tag is its SSA spelling."""
    if tag == MIR_S:
        return "%ps.buf"
    return _tag_llvm(tag)


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
    if op == MIR_SEQGET:
        return fn.arg3[i] if i < len(fn.arg3) else MIR_W
    if op == MIR_GETATTR:
        return fn.arg3[i] if i < len(fn.arg3) else MIR_W
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
