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
    MIR_EXCCODE,
    MIR_EXCCUR,
    MIR_EXCISA,
    MIR_EXTWD,
    MIR_FLOWLD,
    MIR_GETATTR,
    MIR_INITCALL,
    MIR_JMP,
    MIR_MAP_GET,
    MIR_MAP_HAS,
    MIR_MAP_LEN,
    MIR_MAP_PUT,
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
    MIR_RAISEEXC,
    MIR_RAISEMSG,
    MIR_RERAISE,
    MIR_RET,
    MIR_RETLOD,
    MIR_S,
    MIR_SEQGET,
    MIR_SEQLEN,
    MIR_SEQPUSH,
    MIR_SEQSET,
    MIR_SETATTR,
    MIR_SUBD,
    MIR_SUBW,
    MIR_TRYCALL,
    MIR_TRYICMP,
    MIR_TRYNE,
    MIR_TRYPACK,
    MIR_UNREACH,
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
    is_terminator,
    op_arity,
    op_result_type,
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


# The op spec's arity, result-type and terminator facets live in `mir.py`
# behind the `op_arity`/`op_result_type`/`is_terminator` accessors: the
# module-level tables there bind global slots, and slots do not ride the
# package export table a cross-module import resolves against, while
# functions do.


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
    # The return-slot and flow-slot allocas of the function being walked:
    # the try callsite ops read them (`MIR_RETLOD`, `MIR_FLOWLD`,
    # `MIR_TRYCALL`). None when the function has no return value.
    _ret_reg: str | None
    _flow_reg: str | None

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
        self._ret_reg = None
        self._flow_reg = None

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
            ret_reg = self.tmp_reg()
            self._ret_reg = ret_reg
            self.line(f"{ret_reg} = alloca {ret}")
        flow_reg = self.tmp_reg()
        self._flow_reg = flow_reg
        self.line(f"{flow_reg} = alloca i32")
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
        # A `cls.<name>.__init__` callee names a class method record: the
        # direct emitter flattens methods into `self.functions` under that
        # spelling, and the method record carries the same params and
        # returnType the call rendering reads.
        for cls in _items(self.module, "classes"):
            cls_name: str = _opt_str(cls, "name")
            for method in _items(cls, "methods"):
                if "cls." + cls_name + "." + _opt_str(method, "name") == name:
                    return method
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
            while cursor < n and not is_terminator(fn.ops[cursor]):
                cursor += 1
            cursor += 1
        start = cursor
        while cursor < n and not is_terminator(fn.ops[cursor]):
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
        if op == MIR_RAISEMSG:
            # `raise E("msg")`: the direct `emit_raise` message arm —
            # the str operand materializes, its buffer parts extract,
            # then the `ps_raise_msg` call.
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            code = self.operand(fn, a1)
            message = self.operand(fn, a2)
            length, ptr = self._buf_parts(message)
            self.line(f"call void @ps_raise_msg(i32 {code}, i64 {length}, ptr {ptr}) noreturn")
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
            out_slot: str = self._mir_alloca(_mem_llvm(tag))
            self.line(f"call void @ps_seq_get(ptr {self.operand(fn, a1)}, i64 {self.operand(fn, a2)}, ptr {out_slot})")
            if tag == MIR_S:
                # A str element lands in the cell as the 16-byte `%ps.buf`
                # prefix; the read widens it back the way `_widen_cell` does.
                self.reg[dst] = self._widen_str_cell(out_slot)
                return
            llvm: str = _tag_llvm(tag)
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
        if op == MIR_MAP_PUT:
            needed = self.needed
            needed.add("map")
            self.needed = needed
            key_tag = self._operand_tag(fn, a2)
            key_slot = self._stash_cell_tag(key_tag, self.operand(fn, a2))
            val_tag = self._operand_tag(fn, a3)
            val_slot = self._stash_cell_tag(val_tag, self.operand(fn, a3))
            self.line(f"call void @ps_map_put(ptr {self.operand(fn, a1)}, ptr {key_slot}, ptr {val_slot})")
            return
        if op == MIR_MAP_GET:
            self._emit_map_get(fn, dst, a1, a2, a3)
            return
        if op == MIR_MAP_HAS:
            self._emit_map_has(fn, dst, a1, a2)
            return
        if op == MIR_MAP_LEN:
            needed = self.needed
            needed.add("map")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call i64 @ps_map_len(ptr {self.operand(fn, a1)})")
            self.reg[dst] = reg
            return
        if op == MIR_CALL:
            self._emit_call(fn, i, dst, a1, a2)
            return
        if op == MIR_NEWOBJ:
            self._emit_newobj(fn, dst, a1)
            return
        if op == MIR_INITCALL:
            self._emit_initcall(fn, i, dst, a1)
            return
        if op == MIR_SETATTR:
            self._emit_setattr(fn, a1, a2, a3)
            return
        if op == MIR_GETATTR:
            self._emit_getattr(fn, dst, a1, a2)
            return
        if op == MIR_TRYPACK:
            self._emit_trypack(fn, dst)
            return
        if op == MIR_TRYCALL:
            self._emit_trycall(fn, dst, a1)
            return
        if op == MIR_FLOWLD:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            flow_reg = self._flow_reg
            if flow_reg is None:
                raise IRError("flow load outside a function with a flow slot")
            reg = self.tmp_reg()
            self.line(f"{reg} = load i32, ptr {flow_reg}")
            self.reg[dst] = reg
            return
        if op == MIR_TRYICMP:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = icmp eq i32 {self.operand(fn, a1)}, {self.operand(fn, a2)}")
            self.reg[dst] = reg
            return
        if op == MIR_TRYNE:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = icmp ne i32 {self.operand(fn, a1)}, 0")
            self.reg[dst] = reg
            return
        if op == MIR_EXCCODE:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call i32 @ps_exc_code()")
            self.reg[dst] = reg
            return
        if op == MIR_EXCISA:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call i32 @ps_exc_isa(i32 {self.operand(fn, a1)}, i32 {self.operand(fn, a2)})")
            self.reg[dst] = reg
            return
        if op == MIR_RETLOD:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            ret_reg = self._ret_reg
            if ret_reg is None:
                raise IRError("return-slot load in a void function")
            tag = fn.arg2[i]
            reg = self.tmp_reg()
            self.line(f"{reg} = load {_tag_llvm(tag)}, ptr {ret_reg}")
            self.reg[dst] = reg
            return
        if op == MIR_EXCCUR:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            reg = self.tmp_reg()
            self.line(f"{reg} = call ptr @ps_exc_current()")
            self.reg[dst] = reg
            return
        if op == MIR_RERAISE:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            self.line("call void @ps_reraise() noreturn")
            self.line("unreachable")
            return
        if op == MIR_RAISEEXC:
            needed = self.needed
            needed.add("exc")
            self.needed = needed
            self.line(f"call void @ps_raise_exc(ptr {self.operand(fn, a1)}) noreturn")
            self.line("unreachable")
            return
        if op == MIR_UNREACH:
            self.line("unreachable")
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
            if op_arity(op) == 2:
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
            reg = self.tmp_reg()
            if op == MIR_NEGD:
                # `fneg` is unary: the second operand slot carries the
                # unused-operand sentinel, not a value.
                self.line(f"{reg} = fneg double {left}")
            else:
                right = self.operand(fn, a2)
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

    def _emit_trypack(self, fn: MirFunction, dst: int) -> None:
        """`MIR_TRYPACK`: the `[N x ptr]` env array the outline binds.

        The slot names ride the helpers entry (`trypack n1 n2 ...`), in
        the sorted order the direct path's `_used_names` walk produced;
        the return slot trails them for a non-void function, the
        `_env_pointers` shape. An empty pointer list packs `null` with no
        lines, the direct `_pack_ptrs` early return.
        """
        needed = self.needed
        needed.add("exc")
        self.needed = needed
        entry = fn.helpers.get(self._instr_index(fn, dst))
        names: list[str] = []
        if entry is not None:
            names = entry.strip().split(" ")[1:]
        ptrs: list[str] = []
        for name in names:
            slot_vid = self.slot_vid.get(name)
            if slot_vid is None:
                raise IRError(f"try env names a slot the function does not have: {name}")
            ptrs.append(self.reg[slot_vid])
        ir_fn = self._ir_fn(fn.name)
        ret_type: Dict[str, Any] = ir_fn["returnType"]
        if ret_type["kind"] != "none":
            ret_reg = self._ret_reg
            if ret_reg is None:
                raise IRError("try env of a non-void function has no return slot")
            ptrs.append(ret_reg)
        if not ptrs:
            self.reg[dst] = "null"
            return
        env = self._mir_alloca(f"[{len(ptrs)} x ptr]")
        for index in range(len(ptrs)):
            ptr = ptrs[index]
            if index == 0:
                dest = env
            else:
                dest = self.tmp_reg()
                self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
            self.line(f"store ptr {ptr}, ptr {dest}")
        self.reg[dst] = env

    def _emit_trycall(self, fn: MirFunction, dst: int, env: int) -> None:
        """`MIR_TRYCALL`: `ps_try_call(outline, env, flow_ptr)`; the
        outline symbol rides the helpers entry (`trycall ps.try.N`)."""
        needed = self.needed
        needed.add("exc")
        self.needed = needed
        flow_reg = self._flow_reg
        if flow_reg is None:
            raise IRError("try call outside a function with a flow slot")
        index = self._instr_index(fn, dst)
        entry = fn.helpers.get(index)
        outline = "?"
        if entry is not None:
            outline = entry.split(" ")[1]
        reg = self.tmp_reg()
        self.line(f"{reg} = call i32 @ps_try_call(ptr @{outline}, ptr {self.operand(fn, env)}, ptr {flow_reg})")
        self.reg[dst] = reg

    def _instr_index(self, fn: MirFunction, dst: int) -> int:
        """The instruction index defining `dst` (the helpers key)."""
        for i in range(len(fn.ops)):
            if fn.dsts[i] == dst:
                return i
        raise IRError(f"no definition of v{dst}")

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
        if marker == "strget":
            self._emit_strget(fn, dst, fn.call_args[i])
            return
        if marker is not None and marker.startswith("method:"):
            self._emit_method_call(fn, i, dst, fn.call_args[i], marker[len("method:") :])
            return
        if marker is not None and marker.startswith("format:"):
            self._emit_format_call(fn, i, dst, fn.call_args[i], int(marker[len("format:") :]))
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
            elif vtag == MIR_P:
                # A heap pointer (the container repr calls' list/dict
                # argument): the direct `_c_pieces` spells `ptr`.
                rendered.append(f"ptr {value}")
                types.append("ptr")
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

    def _emit_strget(self, fn: MirFunction, dst: int, args_i: list[int]) -> None:
        """A str subscript: the direct `emit_subscript` str arm.

        Both operands render first (the string value, then the index —
        the direct walk's `emit_expr` order), then the string's buffer
        parts extract and the `ps_str_get` buffer-out call runs. The
        generic abi walk cannot spell this: it would extract the string
        parts before the index's own lines, reordering the emits.
        """
        needed: set[str] = self.needed
        needed.add("str")
        self.needed = needed
        seq: str = self.operand(fn, args_i[0])
        index: str = self.operand(fn, args_i[1])
        length, ptr = self._buf_parts(seq)
        self._abi_decl("ps_str_get", "void", ["i64", "ptr", "i64", "ptr"])
        self.reg[dst] = self._buf_out("ps_str_get", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])

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
        if symbol == "ps_map_new":
            # ps_map_new's first word is the i32 key-kind selector; the
            # remaining three stay i64.
            rendered = f"i32 {values[0]}, " + ", ".join(parts[1:])
        else:
            rendered = ", ".join(parts)
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

    def _emit_map_get(self, fn: MirFunction, dst: int, a1: int, a2: int, a3: int) -> None:
        """`MIR_MAP_GET`: the direct `emit_subscript` dict arm.

        The key stashes into its runtime cell first, then the result
        cell allocates, `ps_map_get` fills it, and the value reloads —
        a str result widens the `%ps.buf` cell through `_widen_str_cell`
        the way `_widen_cell` does. `a3` is the result tag.
        """
        needed: set[str] = self.needed
        needed.add("map")
        self.needed = needed
        key_tag: int = self._operand_tag(fn, a2)
        key_slot: str = self._stash_cell_tag(key_tag, self.operand(fn, a2))
        out_slot: str = self._mir_alloca(_mem_llvm(a3))
        self.line(f"call void @ps_map_get(ptr {self.operand(fn, a1)}, ptr {key_slot}, ptr {out_slot})")
        if a3 == MIR_S:
            self.reg[dst] = self._widen_str_cell(out_slot)
            return
        llvm: str = _tag_llvm(a3)
        reg: str = self.tmp_reg()
        self.line(f"{reg} = load {llvm}, ptr {out_slot}")
        self.reg[dst] = reg

    def _emit_map_has(self, fn: MirFunction, dst: int, a1: int, a2: int) -> None:
        """`MIR_MAP_HAS`: the direct `emit_contains` dict arm.

        The key stashes into its runtime cell, `ps_map_has` answers i32,
        and the flag is the `icmp ne` against zero.
        """
        needed: set[str] = self.needed
        needed.add("map")
        self.needed = needed
        key_tag: int = self._operand_tag(fn, a2)
        key_slot: str = self._stash_cell_tag(key_tag, self.operand(fn, a2))
        raw: str = self.tmp_reg()
        self.line(f"{raw} = call i32 @ps_map_has(ptr {self.operand(fn, a1)}, ptr {key_slot})")
        reg: str = self.tmp_reg()
        self.line(f"{reg} = icmp ne i32 {raw}, 0")
        self.reg[dst] = reg

    def _emit_method_call(self, fn: MirFunction, i: int, dst: int, args_i: list[int], name: str) -> None:
        """An admitted method call's emission: the direct `emit_method` arm.

        `join` is the str-join form; `split`/`rsplit` are the
        `ps_str_split_m` forms. Each spelling matches the direct
        emitter's `values[0]`/`values[1]` order.
        """
        if name == "join":
            needed: set[str] = self.needed
            needed.add("str")
            self.needed = needed
            sep: str = self.operand(fn, args_i[0])
            length, ptr = self._buf_parts(sep)
            seq: str = self.operand(fn, args_i[1])
            self._abi_decl("ps_str_join", "void", ["i64", "ptr", "ptr", "ptr"])
            self.reg[dst] = self._buf_out("ps_str_join", [f"i64 {length}", f"ptr {ptr}", f"ptr {seq}"])
            return
        if name == "split" or name == "rsplit":
            # The direct `emit_method` split arm: the receiver's buffer
            # parts first, then the optional separator's, then the
            # `ps_str_split_m` call. `args_i[0]` is the receiver, a
            # lone argument means the whitespace split (`mode` 1,
            # `maxsplit` -1), and `rsplit` rides the `right` flag.
            needed = self.needed
            needed.add("str")
            self.needed = needed
            recv: str = self.operand(fn, args_i[0])
            length, ptr = self._buf_parts(recv)
            if len(args_i) == 1:
                sep_len, sep_ptr, mode, maxsplit = "0", "null", "1", "-1"
            else:
                sep_len, sep_ptr = self._buf_parts(self.operand(fn, args_i[1]))
                mode = "0"
                maxsplit = "-1"
                if len(args_i) > 2:
                    maxsplit = self.operand(fn, args_i[2])
            right = "1" if name == "rsplit" else "0"
            self._abi_decl("ps_str_split_m", "ptr", ["i64", "ptr", "i64", "ptr", "i64", "i64", "i64"])
            reg = self.tmp_reg()
            self.line(
                f"{reg} = call ptr @ps_str_split_m("
                f"i64 {length}, ptr {ptr}, i64 {sep_len}, ptr {sep_ptr}, "
                f"i64 {mode}, i64 {maxsplit}, i64 {right})"
            )
            self.reg[dst] = reg
            return
        raise IRError(f"the MIR lane has no method emission for {name}")

    def _emit_format_call(self, fn: MirFunction, i: int, dst: int, args_i: list[int], style: int) -> None:
        """An admitted `builtins.format` call: the direct `_emit_abi` arm.

        `args_i[0]` is the format string; the rest marshal into a
        `[{count} x %ps.arg]` array through `_store_fmt_arg`, then the
        `ps_str_format` buffer-out call. The style code and the zero
        keyword count ride the marker (`format_kwnames` is always 0 in
        the admitted shape). The `%ps.arg` global itself comes from the
        direct pass's global table, which already rendered this call.
        """
        count: int = len(args_i) - 1
        if count:
            fmt_slot: str = self.tmp_reg()
            self.line(f"{fmt_slot} = alloca [{count} x %ps.arg]")
            for index in range(count):
                base: str = self.tmp_reg()
                self.line(f"{base} = getelementptr [{count} x %ps.arg], ptr {fmt_slot}, i64 0, i64 {index}")
                arg_i: int = args_i[index + 1]
                self._store_fmt_arg(base, self.operand(fn, arg_i), self._operand_tag(fn, arg_i))
            arg_ptr: str = self.tmp_reg()
            self.line(f"{arg_ptr} = getelementptr [{count} x %ps.arg], ptr {fmt_slot}, i64 0, i64 0")
        else:
            arg_ptr = "null"
        flen, fptr = self._buf_parts(self.operand(fn, args_i[0]))
        self._abi_decl("ps_str_format", "void", ["i64", "ptr", "ptr", "i64", "i64", "i64", "ptr"])
        self.reg[dst] = self._buf_out(
            "ps_str_format",
            [f"i64 {flen}", f"ptr {fptr}", f"ptr {arg_ptr}", f"i64 {count}", "i64 0", f"i64 {style}"],
        )

    def _store_fmt_arg(self, base: str, value: str, tag: int) -> None:
        """One `%ps.arg` store for the admitted int/float kinds: the kind
        code at field 0, the payload at field 2 (i64) or 3 (double), the
        direct `_store_ps_arg` spelling."""
        code: int = 1 if tag == MIR_W else 2
        kind_ptr: str = self.tmp_reg()
        self.line(f"{kind_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 0")
        self.line(f"store i32 {code}, ptr {kind_ptr}")
        slot: str = self.tmp_reg()
        field: int = 2 if tag == MIR_W else 3
        piece: str = "i64" if tag == MIR_W else "double"
        self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 {field}")
        self.line(f"store {piece} {value}, ptr {slot}")

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
        in the direct order (the size gep, the ptrtoint, the call). A
        class whose record carries `__init__` gets its init call as the
        next `MIR_CALL` (`cls.<name>.__init__`), the direct
        `emit_constructor`'s `init in self.functions` arm.
        """
        const: MirConst = fn.consts[const_index(a1)]
        name: str = const.sval
        self._mir_class(name)
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

    def _emit_initcall(self, fn: MirFunction, i: int, dst: int, a1: int) -> None:
        """A user-class constructor call: the direct `emit_constructor`.

        The same three `ps_alloc` temporaries `MIR_NEWOBJ` emits, then
        the `cls.<name>.__init__` call when the class defines one — the
        method record's params type each argument (`self` binds the
        fresh allocation) exactly as the direct `call_function` does.
        `arg1` is the `MIR_S` class-name constant; the constructor
        arguments ride `call_args`.
        """
        const: MirConst = fn.consts[const_index(a1)]
        name: str = const.sval
        cls: Dict[str, Any] = self._mir_class(name)
        init: Dict[str, Any] | None = None
        for method in _items(cls, "methods"):
            if _opt_str(method, "name") == "__init__":
                init = method
                break
        needed: set[str] = self.needed
        needed.add("alloc")
        self.needed = needed
        size_ptr: str = self.tmp_reg()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        size: str = self.tmp_reg()
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem: str = self.tmp_reg()
        self.line(f"{mem} = call ptr @ps_alloc(i64 {size})")
        if init is not None:
            params: list[Dict[str, Any]] = init["params"]
            parts: list[str] = []
            parts.append(f"{_llvm_type(params[0]['type'])} {mem}")
            args_i: list[int] = fn.call_args[i]
            for position in range(len(args_i)):
                param: Dict[str, Any] = params[position + 1]
                p_type: Dict[str, Any] = param["type"]
                parts.append(f"{_llvm_type(p_type)} {self.operand(fn, args_i[position])}")
            ret: str = _llvm_type(init["returnType"])
            symbol: str = f"cls.{name}.__init__"
            rendered: str = ", ".join(parts)
            if ret == "void":
                self.line(f"call void @{symbol}({rendered})")
            else:
                call_reg: str = self.tmp_reg()
                self.line(f"{call_reg} = call {ret} @{symbol}({rendered})")
        self.reg[dst] = mem

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
            if kind == "list" or kind == "dict" or kind == "class":
                return MIR_P
            return MIR_W
        if operand in self.slot_vid_set:
            return self._tag_of(operand)
        return self._def_tag(operand)

    def _stash_cell_tag(self, tag: int, value: str) -> str:
        """Stash one scalar/pointer value into a runtime cell, the direct
        path's `_stash_cell` shape for the non-str kinds this slice admits."""
        if tag == MIR_S:
            slot = self._mir_alloca("%ps.buf")
            length = self.tmp_reg()
            ptr = self.tmp_reg()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp_reg()
            data_ptr = self.tmp_reg()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
            return slot
        llvm: str = _tag_llvm(tag)
        slot: str = self._mir_alloca(llvm)
        self.line(f"store {llvm} {value}, ptr {slot}")
        return slot

    def _widen_str_slot(self, a1: int) -> str:
        """Read a str name slot: widen the `%ps.buf` cell to `%ps.str`."""
        return self._widen_str_cell(self.reg[a1])

    def _widen_str_cell(self, slot: str) -> str:
        """Widen one `%ps.buf` cell to a `%ps.str` value.

        Mirrors the direct path's `_widen_cell` for `str`: load the
        length and data pointer out of the cell, rebuild the aggregate
        with a zero count and flag, stage it through a `%ps.str` alloca,
        and let `ps_str_counts` refill the count and flags from the
        bytes. The staging alloca hoists out of non-entry blocks exactly
        like the direct path's.
        """
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
    if op == MIR_MAP_GET:
        return fn.arg3[i] if i < len(fn.arg3) else MIR_W
    if op == MIR_GETATTR:
        return fn.arg3[i] if i < len(fn.arg3) else MIR_W
    if op == MIR_RETLOD:
        # The propagated-return load's true tag rides arg2.
        return fn.arg2[i]
    tag: int = op_result_type(op)
    if tag < 0:
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
