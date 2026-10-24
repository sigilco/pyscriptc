"""The emitter core: the module state, the emit driver, and the low-level
primitives (slots, temporaries, lines, calls) the other areas share.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.dwarf import DwarfEmitter
from pyscriptc.emit._ir import _fn_symbol, _llvm_field_type, _llvm_type, _safe, standalone_entry
from pyscriptc.emit.llvm._runtime import _declarations, _helpers
from pyscriptc.errors import IRError


class CoreMixin:
    def __init__(self, module: dict[str, Any], dwarf: DwarfEmitter | None = None, wasm: bool = False) -> None:
        self.module = module
        # wasm32 targets: wasm-ld only resolves a C-style `main` through the
        # `__main_argc_argv` symbol; an IR-defined `@main` links as
        # undefined_weak and the wasi crt calls a null stub. This is a wasm-ABI
        # fact, not a zig workaround: it stays when the zig 0.18 flags go.
        self.entry_symbol = "__main_argc_argv" if wasm else "main"
        # Off by default: without `--debug-info` the emitted text is byte-identical
        # to the historical output, so the frozen gates and IR hashes do not move.
        self.dwarf = dwarf
        self.functions = {fn["name"]: fn for fn in module["functions"]}
        self.out: list[str] = []
        self.globals: list[str] = []
        self.needed: set[str] = set()
        self.string_data: list[bytes] = []
        self.tid = 0
        self.lid = 0
        self.slots: dict[str, tuple[str, dict[str, Any]]] = {}
        self.terminated = False
        self.try_depth = 0
        self.outline_depth = 0
        self.outline_n = 0
        self.outline_lines: list[str] = []
        self.slot_order: list[str] = []
        self.slot_types: dict[str, dict[str, Any]] = {}
        self.ret_ptr: str | None = None
        self.ret_llvm: str | None = None
        self.flow_ptr: str | None = None
        # Alloca hoisting: allocas emitted outside the entry block move into
        # the entry block at function end, so a loop body does not allocate
        # a new stack slot per iteration.
        self.in_entry = True
        self.hoisted_allocas: list[str] = []
        self.hoist_at: int | None = None
        self.class_index: dict[str, dict[str, Any]] = {}
        self.json_n = 0
        self.copy_n = 0
        self.atexit_n = 0
        self.abi_decls: dict[str, tuple[str, list[str]]] = {}
        # Function names that need a lazy env-first `name.clo` wrapper because
        # they are referenced as values.
        self.clo_wrappers: set[str] = set()
        # Synthesized env-first `functools.partial` shims: symbol -> spec,
        # deduplicated on the spec key.
        self.partials: dict[str, dict[str, Any]] = {}
        self.partial_keys: dict[str, str] = {}
        self.partial_n = 0
        # Synthesized env-first `to_thread` worker shims: symbol -> spec,
        # deduplicated on the spec key. The shim also stores the callable's
        # result into the thread record.
        self.threads: dict[str, dict[str, Any]] = {}
        self.thread_keys: dict[str, str] = {}
        self.thread_n = 0
        # `--debug-info` state: the span of the statement being emitted. Every
        # instruction the statement produces carries it as a `DILocation`.
        self.span: dict[str, Any] | None = None
        # `--emit lib` state: definitions are hidden and the module entry is
        # the exported `pyscriptc_main` instead of a process `main`.
        self.library = False
        # `--emit binding` state: definitions have default visibility and the
        # module entry is the exported `pyscriptc_main`.
        self.binding = False

    def emit(
        self,
        call: str | None,
        args: list[Any],
        standalone: bool = False,
        library: bool = False,
        binding: bool = False,
    ) -> str:
        self.class_index = {cls["name"]: cls for cls in self.module.get("classes", [])}
        self.library = library
        self.binding = binding
        for cls in self.module.get("classes", []):
            body = "i8" if not cls["fields"] else ", ".join(_llvm_field_type(field["type"]) for field in cls["fields"])
            self.globals.append(f"%cls.{cls['name']} = type {{ {body} }}")
            for method in cls["methods"]:
                symbol = f"cls.{cls['name']}.{method['name']}"
                self.functions[symbol] = {**method, "name": symbol}
        for fn in self.module["functions"]:
            self.emit_function(fn)
        for cls in self.module.get("classes", []):
            for method in cls["methods"]:
                self.emit_function(self.functions[f"cls.{cls['name']}.{method['name']}"])
        if call is not None:
            self.emit_main(call, args, library=library or binding)
        elif standalone:
            # A script's synthesized entry (E1 item 1) is the program: run it
            # in source order. A definition-only module keeps the no-op main.
            entry_fn = standalone_entry(self.module)
            if entry_fn is not None:
                self.emit_main(entry_fn["name"], [])
            else:
                self.out.append(f"define i32 @{self.entry_symbol}(i32 %argc, ptr %argv) {{")
                self._reset_function()
                self.block("entry")
                self._emit_exc_registrations()
                self.line("ret i32 0")
                self.out.extend(["}", ""])
        elif library or binding:
            # `--emit lib` / `--emit binding`: the module entry becomes an exported
            # pyscriptc_main instead of a process main. A definition-only
            # module still defines it: the host gets an entry that
            # initializes and returns 0, so the ABI does not depend on the
            # script having a body.
            entry_fn = standalone_entry(self.module)
            if entry_fn is not None:
                self.emit_main(entry_fn["name"], [], library=True)
            else:
                self.emit_noop_library_main()
        # Shims go last: a closure inside a module global initializer (emitted
        # in @main) can still register one.
        for name in sorted(self.clo_wrappers):
            self._emit_clo_wrapper(self.functions[name])
        for symbol in sorted(self.partials):
            self._emit_partial_stub(symbol, self.partials[symbol])
        for symbol in sorted(self.threads):
            self._emit_thread_stub(symbol, self.threads[symbol])
        self.out.extend(self.outline_lines)
        # %ps.str is { i64, ptr, i64, i32 }: byte length, data pointer,
        # code-point count, flags (bit 0 ASCII). Until the str model lands
        # (epic #472) the count mirrors the byte count and the flag stays 0,
        # so the shape is new but the behavior is the old one. %ps.buf is
        # the 16-byte memory shape the runtime reads and writes (class
        # fields, seq and map cells, %ps.arg): a str at rest carries only
        # the PsBuf prefix, and the emitter widens on load and narrows on
        # store.
        header = ["%ps.str = type { i64, ptr, i64, i32 }", "%ps.buf = type { i64, ptr }", ""]
        header.extend(self.globals)
        if self.globals:
            header.append("")
        decls = _declarations(self.needed)
        decls.extend(self._abi_lines())
        header.extend(decls)
        if decls:
            header.append("")
        # boxed:begin
        metadata = self._island_metadata()
        if metadata:
            header.extend(metadata)
            header.append("")
        # boxed:end
        header.extend(_helpers(self.needed))
        body = header + self.out
        if self.dwarf is not None:
            # The debug metadata goes last: the named-metadata block and the
            # `!DI*` records may reference anything the body defined.
            body = body + self.dwarf.render()
        return "\n".join(body).rstrip() + "\n"

    # boxed:begin
    def _island_metadata(self) -> list[str]:
        """The `requires libpython` marker, when the module calls the island.

        A private string constant retained through `@llvm.compiler.used`
        survives the optimizer and lands in the linked binary, so the label
        the coverage report prints is also readable from the artifact. A
        module with no `ps_island_*` call emits nothing, so every native
        binary keeps its historical text.
        """

        if not any(symbol.startswith("ps_island_") for symbol in self.abi_decls):
            return []
        text = b"pyscriptc:requires-libpython\0"
        return [
            f"@.pyscriptc.requires_libpython = private constant [{len(text)} x i8] "
            'c"pyscriptc:requires-libpython\\00", align 1',
            "@llvm.compiler.used = appending global [1 x ptr] "
            '[ptr @.pyscriptc.requires_libpython], section "llvm.metadata"',
        ]

    # boxed:end

    def _field(self, class_name: str, name: str) -> dict[str, Any]:
        for index, field in enumerate(self.class_index[class_name]["fields"]):
            if field["name"] == name:
                return {**field, "index": index}
        raise IRError(f"{class_name}.{name} is not a field")

    def _field_ptr(self, class_name: str, obj: str, field: dict[str, Any]) -> str:
        reg = self.tmp()
        self.line(f"{reg} = getelementptr %cls.{class_name}, ptr {obj}, i32 0, i32 {field['index']}")
        return reg

    def _stash(self, typ: dict[str, Any], value: str) -> str:
        slot = self._alloca(_llvm_type(typ))
        self.line(f"store {_llvm_type(typ)} {value}, ptr {slot}")
        return slot

    def _stash_cell(self, typ: dict[str, Any], value: str) -> str:
        """Stash a value into a runtime-visible cell (a seq or map slot, a
        parts array): a str narrows to the 16-byte %ps.buf prefix the
        runtime reads, everything else stores as is."""

        if typ["kind"] not in {"str", "bytes"}:
            return self._stash(typ, value)
        slot = self._alloca("%ps.buf")
        length = self.tmp()
        ptr = self.tmp()
        self.line(f"{length} = extractvalue %ps.str {value}, 0")
        self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
        len_ptr = self.tmp()
        data_ptr = self.tmp()
        self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
        self.line(f"store i64 {length}, ptr {len_ptr}")
        self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
        self.line(f"store ptr {ptr}, ptr {data_ptr}")
        return slot

    def _widen_cell(self, typ: dict[str, Any], slot: str) -> str:
        """Load a value out of a runtime-visible cell: a str widens from the
        16-byte %ps.buf prefix, and `ps_str_counts` refills the count and
        flag from the bytes (the cell stores only the prefix). None has no
        runtime value: the field slot is a placeholder byte and the read
        folds to the singleton."""

        if typ["kind"] == "none":
            return "0"
        if typ["kind"] not in {"str", "bytes"}:
            return self._load(_llvm_type(typ), slot)
        length = self.tmp()
        ptr = self.tmp()
        self.line(f"{length} = load i64, ptr {slot}")
        data_ptr = self.tmp()
        self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
        self.line(f"{ptr} = load ptr, ptr {data_ptr}")
        first = self.tmp()
        second = self.tmp()
        third = self.tmp()
        self.line(f"{first} = insertvalue %ps.str poison, i64 {length}, 0")
        self.line(f"{second} = insertvalue %ps.str {first}, ptr {ptr}, 1")
        self.line(f"{third} = insertvalue %ps.str {second}, i64 0, 2")
        reg = self.tmp()
        self.line(f"{reg} = insertvalue %ps.str {third}, i32 0, 3")
        if typ["kind"] == "str":
            # Bytes keep the zero count (their count is their byte length);
            # str recomputes so len() and the case family read true values.
            slot_ptr = self._alloca("%ps.str")
            count_ptr2 = self.tmp()
            flags_ptr2 = self.tmp()
            self.line(f"store %ps.str {reg}, ptr {slot_ptr}")
            self.line(f"{count_ptr2} = getelementptr %ps.str, ptr {slot_ptr}, i32 0, i32 2")
            self.line(f"{flags_ptr2} = getelementptr %ps.str, ptr {slot_ptr}, i32 0, i32 3")
            self._abi_decl("ps_str_counts", "void", ["i64", "ptr", "ptr", "ptr"])
            self.line(f"call void @ps_str_counts(i64 {length}, ptr {ptr}, ptr {count_ptr2}, ptr {flags_ptr2})")
            reg = self._load("%ps.str", slot_ptr)
        return reg

    def _alloca(self, llvm_type: str) -> str:
        slot = self.tmp()
        if self.in_entry:
            self.line(f"{slot} = alloca {llvm_type}")
        else:
            # An `alloca` in a nested block is a stack allocation on every
            # dynamic execution of that block, so a loop that runs a million
            # times overflows the stack. Move it to the entry block: the
            # allocation happens once per call, and the block reuses it.
            self.hoisted_allocas.append(f"  {slot} = alloca {llvm_type}")
        return slot

    def _flush_allocas(self) -> None:
        """Move the allocas from nested blocks into the entry block.

        The entry block is the first one of the function, so the hoisted
        allocas go at its end, before its terminator. An `alloca` there
        happens once per call instead of once per execution of the block that
        asked for it, which is what keeps a hot loop from growing the stack.
        """
        if not self.hoisted_allocas:
            return
        at = self.hoist_at if self.hoist_at is not None else self._entry_end()
        self.out[at:at] = self.hoisted_allocas
        self.hoisted_allocas = []
        self.hoist_at = None

    def _entry_end(self) -> int:
        """The index in the output just before the entry block's terminator.

        The entry block is the most recent `entry:` label in the output: its
        instructions run to the first terminator (`br`, `ret` or
        `unreachable`), and the hoisted allocas go right before it. The scan
        starts at the entry label and stops at the first terminator, so a
        terminator in a later block is never picked up.
        """
        start = 0
        for index in range(len(self.out) - 1, -1, -1):
            if self.out[index] == "entry:":
                start = index
                break
        for index in range(start + 1, len(self.out)):
            text = self.out[index]
            if text.startswith("  br ") or text.startswith("  ret ") or text == "  unreachable":
                return index
        return len(self.out)

    def _load(self, llvm_type: str, slot: str) -> str:
        reg = self.tmp()
        self.line(f"{reg} = load {llvm_type}, ptr {slot}")
        return reg

    def _call_ret(self, ret: str, name: str, args: list[str]) -> str:
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @{name}({', '.join(args)})")
        return reg

    def _buf_parts(self, value: str) -> tuple[str, str]:
        length = self.tmp()
        ptr = self.tmp()
        self.line(f"{length} = extractvalue %ps.str {value}, 0")
        self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
        return length, ptr

    def _buf_cell_parts(self, value: str) -> tuple[str, str]:
        """Extract (length, data) from a 16-byte %ps.buf memory value."""

        length = self.tmp()
        ptr = self.tmp()
        self.line(f"{length} = extractvalue %ps.buf {value}, 0")
        self.line(f"{ptr} = extractvalue %ps.buf {value}, 1")
        return length, ptr

    def _buf_cmp(self, left: str, right: str) -> str:
        self.needed.add("str")
        left_len, left_ptr = self._buf_parts(left)
        right_len, right_ptr = self._buf_parts(right)
        return self._call_ret(
            "i32", "ps_str_cmp", [f"i64 {left_len}", f"ptr {left_ptr}", f"i64 {right_len}", f"ptr {right_ptr}"]
        )

    def _buf_cmp_cells(self, left: str, right: str) -> str:
        """Compare two 16-byte %ps.buf memory values."""

        self.needed.add("str")
        left_len, left_ptr = self._buf_cell_parts(left)
        right_len, right_ptr = self._buf_cell_parts(right)
        return self._call_ret(
            "i32", "ps_str_cmp", [f"i64 {left_len}", f"ptr {left_ptr}", f"i64 {right_len}", f"ptr {right_ptr}"]
        )

    def _emit_clo_wrapper(self, fn: dict[str, Any]) -> None:
        """`name.clo(env, params...) -> name(params...)`: env-first shim for a
        plain function referenced as a value."""
        ret = _llvm_type(fn["returnType"])
        symbol = _fn_symbol(fn["name"])
        plist = ["ptr %env"] + [f"{_llvm_type(p['type'])} %{_safe(p['name'])}" for p in fn["params"]]
        args = ", ".join(f"{_llvm_type(p['type'])} %{_safe(p['name'])}" for p in fn["params"])
        self.out.append(f"define internal {ret} @{symbol}.clo({', '.join(plist)}) {{")
        self.out.append("entry:")
        if ret == "void":
            self.out.append(f"  call void @{symbol}({args})")
            self.out.append("  ret void")
        else:
            self.out.append(f"  %r = call {ret} @{symbol}({args})")
            self.out.append(f"  ret {ret} %r")
        self.out.append("}")
        self.out.append("")

    def store(self, name: str, value: str) -> None:
        ptr, typ = self.slots[name]
        if typ["kind"] == "none":
            # None is not a runtime value (see the local setup): the store is
            # a no-op, the singleton is the only value the type holds.
            return
        if typ["kind"] in {"str", "bytes"}:
            length = self.tmp()
            ptr_val = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr_val} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {ptr}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {ptr}, i32 0, i32 1")
            self.line(f"store ptr {ptr_val}, ptr {data_ptr}")
            return
        self.line(f"store {_llvm_type(typ)} {value}, ptr {ptr}")

    def load(self, name: str) -> str:
        ptr, typ = self.slots[name]
        if typ["kind"] == "none":
            # The local is the singleton: a read has nothing to load.
            return "null"
        if typ["kind"] in {"str", "bytes"}:
            return self._widen_cell(typ, ptr)
        reg = self.tmp()
        self.line(f"{reg} = load {_llvm_type(typ)}, ptr {ptr}")
        return reg

    def block(self, name: str) -> None:
        if name != "entry" and self.hoist_at is None:
            # The entry block ends here: the hoisted allocas go just before
            # its terminator, which is the last instruction of the block.
            self.hoist_at = self._entry_end()
        self.out.append(f"{name}:")
        self.terminated = False
        if name != "entry":
            # The entry block is closed: every later `alloca` belongs to a
            # nested block and must move to the entry block, or the stack
            # grows by one allocation per iteration of the enclosing loop.
            self.in_entry = False

    def fresh(self, hint: str) -> str:
        self.lid += 1
        return f"L{self.lid}.{hint}"

    def tmp(self) -> str:
        self.tid += 1
        return f"%t{self.tid}"

    def line(self, text: str) -> None:
        self.out.append(f"  {text}{self._dbg_suffix(text)}")

    def _dbg_suffix(self, text: str) -> str:
        """The `!dbg` attachment for an emitted instruction, or '' when off.

        Only instructions can carry a location: a label or a block header is not
        an instruction, and the terminator of an instruction that already ends
        in a comment keeps the comment last.
        """
        if self.dwarf is None or self.span is None:
            return ""
        if ";" in text:
            # `call void @ps_raise_code(i32 10) noreturn ; OverflowError`: the
            # metadata goes before the comment.
            head, _, tail = text.partition(";")
            return f"{self.dwarf.dbg(self.span)} ;{tail}"
        return self.dwarf.dbg(self.span)

    def _reset_function(self) -> None:
        self.tid = 0
        self.lid = 0
        self.slots = {}
        self.terminated = False
        self.try_depth = 0
        self.outline_depth = 0
        self.in_entry = True
        self.hoisted_allocas = []
        self.hoist_at = None
