"""Lower spike IR to textual LLVM IR and a host binary.

`int` stays a Python int in the IR. This backend uses i64 only when the value
fits, and checked arithmetic traps on overflow instead of wrapping. A literal
that does not fit is refused: it is not truncated.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path
from typing import Any

from pyscriptc.builtins import EXCEPTIONS, USER_EXC_BASE
from pyscriptc.errors import FitError, IRError
from pyscriptc.ir import assert_same_cpython, fits_i64
from pyscriptc.stdlib_queue import MATH_FUNCTIONS
from pyscriptc.stdmore import COMPARE_CLASSES

_ICMP = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge", "==": "eq", "!=": "ne"}
_FOP = {"+": "fadd", "-": "fsub", "*": "fmul"}
_INT_HELPER = {"+": "iadd", "-": "isub", "*": "imul", "//": "floordiv"}


def emit_llvm(
    module: dict[str, Any],
    call: str | None = None,
    args: list[Any] | None = None,
    standalone: bool = False,
) -> str:
    """With `standalone` and no `call`, main does nothing: module level code is not lowered."""
    assert_same_cpython(module)
    if module["fences"]:
        raise IRError("refusing to emit LLVM while fences are present")
    if not module["functions"]:
        raise IRError("module has no functions to emit")
    return Emitter(module).emit(call, [] if args is None else args, standalone)


def link_binary(
    llvm_path: Path,
    output: Path,
    driver: str = "clang",
    target: str | None = None,
    record: list[list[str]] | None = None,
) -> None:
    """Link `.ll` text plus the C runtime into `output`.

    Two-stage: `.ll -> .o`, then objects + runtime `*.c` link. Required for
    `zig cc` on macOS (a `.ll` on the link line writes an empty binary there)
    and keeps per-target artifacts clean. `driver` is `clang` or `zig`
    (`zig cc`); `target` is a zig-style triple and requires the zig driver —
    it bundles the per-target sysroot that `clang --target` does not.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if target is not None and driver != "zig":
        raise IRError("--target requires --driver zig (bundled sysroot per target)")
    cc = "zig cc" if driver == "zig" else driver
    runtime = Path(__file__).resolve().parent / "runtime"
    text = llvm_path.read_text(encoding="utf-8")
    sources = [str(path) for path in sorted(runtime.glob("*.c"))]
    target_flags = ["-target", target] if target is not None else []
    tail = ["-lm", "-o", str(output)]
    if "ps_island_" in text:
        if target is not None:
            raise IRError(
                "island fallback embeds the host CPython; "
                "cross-compiling it needs a cross-built libpython (explain-only)"
            )
        import sysconfig

        include = sysconfig.get_path("include")
        libdir = sysconfig.get_config_var("LIBDIR")
        version = sysconfig.get_config_var("VERSION")
        target_flags.extend(["-I", include])
        tail = [f"-L{libdir}", f"-lpython{version}", f"-Wl,-rpath,{libdir}", *tail]
    else:
        sources = [source for source in sources if not source.endswith("ps_island.c")]
    # zig cannot compile C sources inside a link invocation for Mach-O, so
    # every unit goes to .o first and the link line carries objects only.
    units = [llvm_path, *(Path(source) for source in sources)]
    objects = [output.with_name(f"{output.name}.{unit.stem}.o") for unit in units]
    proc: subprocess.CompletedProcess[str] | None = None
    for unit, obj in zip(units, objects, strict=True):
        kind = ["-x", "ir"] if driver == "clang" and unit == llvm_path else []
        argv = [*cc.split(), "-O2", *kind, "-c", str(unit), "-o", str(obj), *target_flags]
        if record is not None:
            record.append([str(arg) for arg in argv])
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            break
    if proc is not None and proc.returncode == 0:
        # no -O on the link line: it is a compile flag and zig's Mach-O
        # driver crashes on it (0.16.0)
        argv = [*cc.split(), *objects, *target_flags, *tail]
        if record is not None:
            record.append([str(arg) for arg in argv])
        proc = subprocess.run(argv, capture_output=True, text=True)
    if proc is None or proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() if proc is not None else ""
        raise IRError(f"{cc} failed to assemble LLVM IR: {detail}")


class Emitter:
    def __init__(self, module: dict[str, Any]) -> None:
        self.module = module
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
        self.class_index: dict[str, dict[str, Any]] = {}
        self.json_n = 0
        self.atexit_n = 0
        self.abi_decls: dict[str, tuple[str, list[str]]] = {}

    def emit(self, call: str | None, args: list[Any], standalone: bool = False) -> str:
        self.class_index = {cls["name"]: cls for cls in self.module.get("classes", [])}
        for cls in self.module.get("classes", []):
            body = "i8" if not cls["fields"] else ", ".join(_llvm_type(field["type"]) for field in cls["fields"])
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
            self.emit_main(call, args)
        elif standalone:
            # A script's synthesized entry (E1 item 1) is the program: run it
            # in source order. A definition-only module keeps the no-op main.
            entry = self.module.get("entry")
            entry_fn = next((fn for fn in self.module["functions"] if fn["name"] == entry), None)
            if entry_fn is not None and entry_fn["name"] == "main" and not entry_fn["params"]:
                self.emit_main("main", [])
            else:
                self.out.extend(["define i32 @main(i32 %argc, ptr %argv) {", "entry:", "  ret i32 0", "}", ""])
        self.out.extend(self.outline_lines)
        header = ["%ps.str = type { i64, ptr }", ""]
        header.extend(self.globals)
        if self.globals:
            header.append("")
        decls = _declarations(self.needed)
        decls.extend(self._abi_lines())
        header.extend(decls)
        if decls:
            header.append("")
        header.extend(_helpers(self.needed))
        return "\n".join(header + self.out).rstrip() + "\n"

    def emit_function(self, fn: dict[str, Any]) -> None:
        ret = _llvm_type(fn["returnType"])
        params = ", ".join(f"{_llvm_type(p['type'])} %{_safe(p['name'])}" for p in fn["params"])
        self.out.append(f"define {ret} @{_fn_symbol(fn['name'])}({params}) {{")
        self._reset_function()
        self.block("entry")
        self.slot_order = []
        self.slot_types = {}
        for local in fn["locals"]:
            if local["type"]["kind"] == "none":
                # None is not a runtime value. The local is the singleton, so it has no slot.
                self.slots[local["name"]] = ("", local["type"])
                continue
            ptr = self.tmp()
            self.line(f"{ptr} = alloca {_llvm_type(local['type'])}")
            self.slots[local["name"]] = (ptr, local["type"])
            self.slot_order.append(local["name"])
            self.slot_types[local["name"]] = local["type"]
        self.ret_llvm = None if fn["returnType"]["kind"] == "none" else _llvm_type(fn["returnType"])
        self.ret_ptr = None if self.ret_llvm is None else self._alloca(self.ret_llvm)
        self.flow_ptr = self._alloca("i32")
        for param in fn["params"]:
            self.store(param["name"], f"%{_safe(param['name'])}")
        self.emit_body(fn["body"])
        if not self.terminated:
            if fn["returnType"]["kind"] == "none":
                self.line("ret void")
            else:
                self.line("unreachable")
        self.out.append("}")
        self.out.append("")

    def emit_main(self, name: str, args: list[Any]) -> None:
        fn = self.functions.get(name)
        if fn is None:
            raise IRError(f"no function {name} to call")
        params = fn["params"]
        if len(args) > len(params):
            raise FitError(f"{name} expects {len(params)} argument(s), got {len(args)}")
        filled: list[Any] = list(args)
        for param in params[len(args) :]:
            default = param.get("default")
            if not isinstance(default, dict) or default.get("kind") != "const":
                raise FitError(f"{name} expects {len(params)} argument(s), got {len(args)}")
            filled.append(_ir_const_value(param["type"], default["value"]))
        prepared = [_prepare_arg(param, value) for param, value in zip(params, filled, strict=True)]
        self.out.append("define i32 @main(i32 %argc, ptr %argv) {")
        self._reset_function()
        self.block("entry")
        self._abi_decl("ps_argv_init", "void", ["i32", "ptr"])
        self._call_void("ps_argv_init", ["i32 %argc", "ptr %argv"])
        values = [self.materialize_arg(param["type"], value) for param, value in zip(params, prepared, strict=True)]
        result = self.call_function(fn, values)
        if fn["returnType"]["kind"] != "none":
            self.print_value(fn["returnType"], result)
        self.line("ret i32 0")
        self.out.append("}")
        self.out.append("")

    def emit_body(self, body: list[dict[str, Any]]) -> None:
        for stmt in body:
            if self.terminated:
                break
            self.emit_stmt(stmt)

    def emit_stmt(self, stmt: dict[str, Any]) -> None:
        kind = stmt["kind"]
        if kind == "assign":
            if stmt["value"]["type"]["kind"] == "none":
                if stmt["value"]["kind"] != "const":
                    self.emit_expr(stmt["value"])
                return
            self.store(stmt["target"], self.emit_expr(stmt["value"]))
        elif kind == "expr":
            if stmt["expr"]["kind"] == "const" and stmt["expr"]["type"]["kind"] == "none":
                return
            self.emit_expr(stmt["expr"])
        elif kind == "return":
            self._emit_return(stmt)
        elif kind == "pass":
            return
        elif kind == "setitem":
            self.emit_setitem(stmt)
        elif kind == "setattr":
            self.emit_setattr(stmt)
        elif kind == "raise":
            self.emit_raise(stmt)
            self.terminated = True
        elif kind == "try":
            self.emit_try(stmt)
        elif kind == "if":
            self.emit_if(stmt)
        elif kind == "while":
            self.emit_while(stmt)
        else:
            raise IRError(f"cannot emit statement {kind}")

    def emit_if(self, stmt: dict[str, Any]) -> None:
        cond = self.emit_expr(stmt["cond"])
        then_l = self.fresh("then")
        else_l = self.fresh("else")
        merge_l = self.fresh("merge")
        self.line(f"br i1 {cond}, label %{then_l}, label %{else_l}")
        self.terminated = True
        self.block(then_l)
        self.emit_body(stmt["then"])
        then_dead = self.terminated
        if not then_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        self.block(else_l)
        self.emit_body(stmt["else"])
        else_dead = self.terminated
        if not else_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        if then_dead and else_dead:
            self.terminated = True
            return
        self.block(merge_l)

    def emit_while(self, stmt: dict[str, Any]) -> None:
        cond_l = self.fresh("while.cond")
        body_l = self.fresh("while.body")
        end_l = self.fresh("while.end")
        self.line(f"br label %{cond_l}")
        self.terminated = True
        self.block(cond_l)
        cond = self.emit_expr(stmt["cond"])
        self.line(f"br i1 {cond}, label %{body_l}, label %{end_l}")
        self.terminated = True
        self.block(body_l)
        self.emit_body(stmt["body"])
        if not self.terminated:
            self.line(f"br label %{cond_l}")
            self.terminated = True
        self.block(end_l)

    def emit_try(self, stmt: dict[str, Any]) -> None:
        self.needed.add("exc")
        if stmt.get("finalbody"):
            # E2 desugar: body+handlers+else become an inner try inside one
            # outline; the callsite runs `finalbody` on every outcome.
            inner = {"kind": "try", "body": stmt["body"], "handlers": stmt["handlers"], "span": stmt["span"]}
            if stmt.get("orelse"):
                inner["orelse"] = stmt["orelse"]
                for flag in ["brk", "cont"]:
                    if stmt.get(flag) is not None:
                        inner[flag] = stmt[flag]
            outline = self._emit_outline([inner])
            env = self._pack_env()
            if self.flow_ptr is None:
                raise IRError("try has no flow slot")
            code = self._call_ret("i32", "ps_try_call", [f"ptr @{outline}", f"ptr {env}", f"ptr {self.flow_ptr}"])
            raised = self.tmp()
            self.line(f"{raised} = icmp ne i32 {code}, 0")
            raise_l = self.fresh("fin.raise")
            norm_l = self.fresh("fin.norm")
            merge_l = self.fresh("fin.merge")
            ret_l = self.fresh("fin.ret")
            self.line(f"br i1 {raised}, label %{raise_l}, label %{norm_l}")
            self.terminated = True
            self.block(raise_l)
            self._abi_decl("ps_exc_current", "ptr", [])
            self._abi_decl("ps_raise_exc", "void", ["ptr"])
            saved = self._call_ret("ptr", "ps_exc_current", [])
            self.emit_body(stmt["finalbody"])
            if not self.terminated:
                self.line(f"call void @ps_raise_exc(ptr {saved}) noreturn")
                self.line("unreachable")
                self.terminated = True
            self.block(norm_l)
            flow = self._load("i32", self.flow_ptr)
            leave = self.tmp()
            self.line(f"{leave} = icmp eq i32 {flow}, 1")
            cont_l = self.fresh("fin.cont")
            self.line(f"br i1 {leave}, label %{ret_l}, label %{cont_l}")
            self.terminated = True
            self.block(ret_l)
            self.emit_body(stmt["finalbody"])
            if not self.terminated:
                self._emit_propagated_return()
            self.block(cont_l)
            self.emit_body(stmt["finalbody"])
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
            self.block(merge_l)
            return
        outline = self._emit_outline(stmt["body"])
        env = self._pack_env()
        if self.flow_ptr is None:
            raise IRError("try has no flow slot")
        code = self._call_ret("i32", "ps_try_call", [f"ptr @{outline}", f"ptr {env}", f"ptr {self.flow_ptr}"])
        raised = self.tmp()
        self.line(f"{raised} = icmp ne i32 {code}, 0")
        hand_l = self.fresh("try.handler")
        cont_l = self.fresh("try.cont")
        self.line(f"br i1 {raised}, label %{hand_l}, label %{cont_l}")
        self.terminated = True
        self.block(hand_l)
        merge_l = self.fresh("try.merge")
        ret_l = self.fresh("try.ret")
        ecode = self._call_ret("i32", "ps_exc_code", [])
        for index, handler in enumerate(stmt["handlers"]):
            take_l = self.fresh(f"try.h{index}")
            miss_l = self.fresh("try.miss")
            cond = self._exc_match(ecode, handler)
            self.line(f"br i1 {cond}, label %{take_l}, label %{miss_l}")
            self.terminated = True
            self.block(take_l)
            slot = handler.get("excslot")
            if slot is not None:
                # `except E as e` binding (or a bare-raise temp slot).
                self._abi_decl("ps_exc_current", "ptr", [])
                current = self._call_ret("ptr", "ps_exc_current", [])
                self.line(f"store ptr {current}, ptr {self.slots[slot][0]}")
            self.emit_body(handler["body"])
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
            self.block(miss_l)
        self.line("call void @ps_reraise() noreturn")
        self.line("unreachable")
        self.terminated = True
        self.block(cont_l)
        flow = self._load("i32", self.flow_ptr)
        leave = self.tmp()
        self.line(f"{leave} = icmp eq i32 {flow}, 1")
        if stmt.get("orelse"):
            else_l = self.fresh("try.else")
            skip_l = self.fresh("try.skipelse")
            self.line(f"br i1 {leave}, label %{ret_l}, label %{skip_l}")
            self.terminated = True
            self.block(skip_l)
            # else runs iff the body fell through: no raise, return, or jump.
            jumped: str = "false"
            for flag in ["brk", "cont"]:
                fname = stmt.get(flag)
                if fname is not None:
                    reg = self._load("i1", self.slots[fname][0])
                    if jumped == "false":
                        jumped = reg
                    else:
                        nxt = self.tmp()
                        self.line(f"{nxt} = or i1 {jumped}, {reg}")
                        jumped = nxt
            self.line(f"br i1 {jumped}, label %{merge_l}, label %{else_l}")
            self.terminated = True
            self.block(else_l)
            self.emit_body(stmt["orelse"])
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
        else:
            self.line(f"br i1 {leave}, label %{ret_l}, label %{merge_l}")
            self.terminated = True
        self.block(ret_l)
        self._emit_propagated_return()
        self.block(merge_l)

    def _emit_return(self, stmt: dict[str, Any]) -> None:
        kind = stmt["value"]["type"]["kind"]
        if self.outline_depth:
            if kind != "none":
                value = self.emit_expr(stmt["value"])
                if self.ret_ptr is None or self.ret_llvm is None:
                    raise IRError("return inside try has no slot")
                self.line(f"store {self.ret_llvm} {value}, ptr {self.ret_ptr}")
            elif stmt["value"]["kind"] != "const":
                self.emit_expr(stmt["value"])
            if self.terminated:
                return
            self.line("ret i32 1")
        elif kind == "none":
            if stmt["value"]["kind"] != "const":
                self.emit_expr(stmt["value"])
            if self.terminated:
                return
            self.line("ret void")
        else:
            value = self.emit_expr(stmt["value"])
            self.line(f"ret {_llvm_type(stmt['value']['type'])} {value}")
        self.terminated = True

    def _emit_propagated_return(self) -> None:
        if self.outline_depth:
            self.line("ret i32 1")
        elif self.ret_llvm is None:
            self.line("ret void")
        else:
            if self.ret_ptr is None:
                raise IRError("return has no slot")
            value = self._load(self.ret_llvm, self.ret_ptr)
            self.line(f"ret {self.ret_llvm} {value}")
        self.terminated = True

    def _emit_outline(self, body: list[dict[str, Any]]) -> str:
        name = f"ps.try.{self.outline_n}"
        self.outline_n += 1
        saved_out = self.out
        saved_slots = self.slots
        saved_flow = self.flow_ptr
        saved_ret = self.ret_ptr
        saved_term = self.terminated
        saved_lid = self.lid
        saved_tid = self.tid
        saved_depth = self.outline_depth
        self.out = []
        self.out.append(f"define internal i32 @{name}(ptr %env) {{")
        self.tid = 0
        self.lid = 0
        self.terminated = False
        self.outline_depth = saved_depth + 1
        self.block("entry")
        self._bind_env("%env")
        self.flow_ptr = self._alloca("i32")
        self.emit_body(body)
        if not self.terminated:
            self.line("ret i32 0")
        self.out.append("}")
        self.out.append("")
        self.outline_lines.extend(self.out)
        self.out = saved_out
        self.slots = saved_slots
        self.flow_ptr = saved_flow
        self.ret_ptr = saved_ret
        self.terminated = saved_term
        self.lid = saved_lid
        self.tid = saved_tid
        self.outline_depth = saved_depth
        return name

    def _env_pointers(self) -> list[str]:
        ptrs = [self.slots[name][0] for name in self.slot_order]
        if self.ret_ptr is not None:
            ptrs.append(self.ret_ptr)
        return ptrs

    def _pack_env(self) -> str:
        ptrs = self._env_pointers()
        if not ptrs:
            return "null"
        env = self._alloca(f"[{len(ptrs)} x ptr]")
        for index, ptr in enumerate(ptrs):
            dest = env if index == 0 else self.tmp()
            if index != 0:
                self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
            self.line(f"store ptr {ptr}, ptr {dest}")
        return env

    def _bind_env(self, env: str) -> None:
        self.slots = {}
        for index, name in enumerate(self.slot_order):
            loaded = self._load_env_ptr(env, index)
            self.slots[name] = (loaded, self.slot_types[name])
        if self.ret_llvm is not None:
            self.ret_ptr = self._load_env_ptr(env, len(self.slot_order))

    def _load_env_ptr(self, env: str, index: int) -> str:
        if index == 0:
            return self._load("ptr", env)
        gep = self.tmp()
        self.line(f"{gep} = getelementptr ptr, ptr {env}, i64 {index}")
        return self._load("ptr", gep)

    def emit_raise(self, stmt: dict[str, Any]) -> None:
        self.needed.add("exc")
        code = stmt["code"] if "code" in stmt else EXCEPTIONS[stmt["exc"]]
        if stmt.get("reraise"):
            slot = stmt.get("excslot")
            if slot is not None:
                value = self.load(slot)
                self._abi_decl("ps_raise_exc", "void", ["ptr"])
                self.line(f"call void @ps_raise_exc(ptr {value}) noreturn")
            else:
                self.line("call void @ps_reraise() noreturn")
        elif stmt.get("cause") is not None:
            # `raise E(args) from cause`: build the record, link the cause
            # (an excmake or bound `except ... as e` local), then raise.
            self._abi_decl("ps_exc_make", "ptr", ["i32", "i64", "ptr"])
            self._abi_decl("ps_exc_set_cause", "void", ["ptr", "ptr"])
            self._abi_decl("ps_raise_exc", "void", ["ptr"])
            made = self._call_ret("ptr", "ps_exc_make", [f"i32 {code}", "i64 0", "ptr null"])
            if stmt.get("message") is not None:
                msg = self.emit_expr(stmt["message"])
                mlen, mptr = self._buf_parts(msg)
                made = self._call_ret("ptr", "ps_exc_make", [f"i32 {code}", f"i64 {mlen}", f"ptr {mptr}"])
            cause = stmt["cause"]
            if cause["kind"] == "excmake":
                ccode = cause["code"] if "code" in cause else EXCEPTIONS[cause["exc"]]
                cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", "i64 0", "ptr null"])
                if cause.get("message") is not None:
                    cmsg = self.emit_expr(cause["message"])
                    clen, cptr = self._buf_parts(cmsg)
                    cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", f"i64 {clen}", f"ptr {cptr}"])
            elif cause["kind"] == "name":
                cause_value = self.load(cause["id"])
            else:
                raise IRError("raise cause must be a name or excmake")
            self._call_void("ps_exc_set_cause", [f"ptr {made}", f"ptr {cause_value}"])
            self.line(f"call void @ps_raise_exc(ptr {made}) noreturn")
        elif stmt.get("message") is None:
            self.line(f"call void @ps_raise_code(i32 {code}) noreturn")
        else:
            message = self.emit_expr(stmt["message"])
            length, ptr = self._buf_parts(message)
            self.line(f"call void @ps_raise_msg(i32 {code}, i64 {length}, ptr {ptr}) noreturn")
        self.line("unreachable")

    def emit_setitem(self, stmt: dict[str, Any]) -> None:
        container = self.emit_expr(stmt["container"])
        index = self.emit_expr(stmt["index"])
        value = self.emit_expr(stmt["value"])
        kind = stmt["container"]["type"]["kind"]
        if kind == "list":
            self.needed.add("seq")
            slot = self._stash(stmt["value"]["type"], value)
            self.line(f"call void @ps_seq_set(ptr {container}, i64 {index}, ptr {slot})")
            return
        if kind == "dict":
            self.needed.add("map")
            key = self._stash(stmt["index"]["type"], index)
            slot = self._stash(stmt["value"]["type"], value)
            self.line(f"call void @ps_map_put(ptr {container}, ptr {key}, ptr {slot})")
            return
        raise IRError(f"cannot assign into {kind}")

    def emit_setattr(self, stmt: dict[str, Any]) -> None:
        obj = self.emit_expr(stmt["object"])
        value = self.emit_expr(stmt["value"])
        field = self._field(stmt["object"]["type"]["name"], stmt["name"])
        slot = self._field_ptr(stmt["object"]["type"]["name"], obj, field)
        self.line(f"store {_llvm_type(field['type'])} {value}, ptr {slot}")

    def _exc_match(self, code: str, handler: dict[str, Any] | list[str]) -> str:
        if isinstance(handler, dict) and handler.get("bare"):
            return "true"
        names = handler if isinstance(handler, list) else handler["exc"]
        checks: list[str] = []
        for name in names:
            if name not in EXCEPTIONS:
                continue
            wanted = EXCEPTIONS[name]
            call = self._call_ret("i32", "ps_exc_isa", [f"i32 {code}", f"i32 {wanted}"])
            checks.append(self._flag(call))
            if name == "Exception":
                # User codes have no runtime parent chain yet; every user
                # exception inherits a lowered builtin, so all of them are
                # Exception descendants.
                reg = self.tmp()
                self.line(f"{reg} = icmp sge i32 {code}, {USER_EXC_BASE}")
                checks.append(reg)
        numbers = handler.get("codes") if isinstance(handler, dict) else None
        if not numbers:
            numbers = [EXCEPTIONS[name] for name in names if name in EXCEPTIONS]
        for number in numbers:
            if number < USER_EXC_BASE:
                continue
            reg = self.tmp()
            self.line(f"{reg} = icmp eq i32 {code}, {number}")
            checks.append(reg)
        acc: str | None = None
        for reg in checks:
            if acc is None:
                acc = reg
                continue
            nxt = self.tmp()
            self.line(f"{nxt} = or i1 {acc}, {reg}")
            acc = nxt
        if acc is None:
            raise IRError("except handler has no exception name")
        return acc

    def emit_expr(self, expr: dict[str, Any]) -> str:
        kind = expr["kind"]
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            return self.load(expr["id"])
        if kind == "unary":
            operand = self.emit_expr(expr["operand"])
            if expr["op"] == "-" and expr["type"]["kind"] == "int":
                return self.helper_call("ineg", "i64", [operand])
            if expr["op"] == "-" and expr["type"]["kind"] == "float":
                reg = self.tmp()
                self.line(f"{reg} = fneg double {operand}")
                return reg
            if expr["op"] == "-" and expr["type"]["kind"] == "class":
                mem = self._alloc_class("TimeDelta")
                self._abi_decl("ps_delta_neg", "void", ["ptr", "ptr"])
                self._call_void("ps_delta_neg", [f"ptr {operand}", f"ptr {mem}"])
                return mem
            if expr["op"] == "not":
                reg = self.tmp()
                self.line(f"{reg} = xor i1 {operand}, true")
                return reg
            raise IRError(f"cannot emit unary {expr['op']}")
        if kind == "binary":
            return self.emit_binary(expr, self.emit_expr(expr["left"]), self.emit_expr(expr["right"]))
        if kind == "compare":
            return self.emit_compare(expr)
        if kind == "call":
            return self.emit_call(expr)
        if kind == "list":
            return self.emit_list(expr)
        if kind == "dict":
            return self.emit_dict(expr)
        if kind == "subscript":
            return self.emit_subscript(expr)
        if kind == "attr":
            return self.emit_attr(expr)
        if kind == "cast":
            value = self.emit_expr(expr["value"])
            reg = self.tmp()
            self.line(f"{reg} = sitofp i64 {value} to double")
            return reg
        if kind == "contains":
            return self.emit_contains(expr)
        raise IRError(f"cannot emit expression {kind}")

    def emit_const(self, expr: dict[str, Any]) -> str:
        kind = expr["type"]["kind"]
        value = expr["value"]
        if kind == "int":
            number = int(value)
            if not fits_i64(number):
                raise FitError(f"integer literal {value} does not fit in i64; refusing to truncate")
            return str(number)
        if kind == "bool":
            return "true" if value else "false"
        if kind == "float":
            return _llvm_double(float(value))
        if kind == "str":
            return self.string_value(value)
        if kind == "bytes":
            return self.string_value(value, binary=True)
        if kind == "none":
            raise IRError("None is not a runtime value")
        raise IRError(f"cannot emit constant of type {kind}")

    def emit_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        kind = expr["type"]["kind"]
        op = expr["op"]
        if kind == "int":
            return self.helper_call(_INT_HELPER[op], "i64", [left, right])
        if kind == "float":
            reg = self.tmp()
            self.line(f"{reg} = {_FOP[op]} double {left}, {right}")
            return reg
        if kind in {"str", "bytes"} and op == "+":
            return self.helper_call("strconcat", "%ps.str", [left, right], arg_type="%ps.str")
        if expr["left"]["type"]["kind"] == "class":
            return self._emit_class_binary(expr, left, right)
        raise IRError(f"cannot emit {op} on {kind}")

    def emit_compare(self, expr: dict[str, Any]) -> str:
        left_kind = expr["left"]["type"]["kind"]
        right_kind = expr["right"]["type"]["kind"]
        op = expr["op"]
        if left_kind == "none" or right_kind == "none":
            if op not in {"==", "!="}:
                raise IRError(f"cannot compare None with {op}")
            self._emit_none_side(expr["left"])
            self._emit_none_side(expr["right"])
            # None equals only None. A str, int, or other value is never None.
            same = left_kind == "none" and right_kind == "none"
            truth = same if op == "==" else not same
            reg = self.tmp()
            bit = "true" if truth else "false"
            self.line(f"{reg} = icmp eq i1 {bit}, true")
            return reg
        left = self.emit_expr(expr["left"])
        right = self.emit_expr(expr["right"])
        kind = left_kind
        if op in {"is", "is not"}:
            return self._emit_identity(op, kind, left, right)
        reg = self.tmp()
        if kind in {"int", "bool"}:
            self.line(f"{reg} = icmp {_ICMP[op]} {'i64' if kind == 'int' else 'i1'} {left}, {right}")
            return reg
        if kind == "float":
            pred = {"==": "oeq", "!=": "une", "<": "olt", "<=": "ole", ">": "ogt", ">=": "oge"}[op]
            self.line(f"{reg} = fcmp {pred} double {left}, {right}")
            return reg
        if kind in {"str", "bytes"}:
            cmp = self._buf_cmp(left, right)
            pred = {"==": "eq", "!=": "ne", "<": "slt", "<=": "sle", ">": "sgt", ">=": "sge"}[op]
            self.line(f"{reg} = icmp {pred} i32 {cmp}, 0")
            return reg
        if kind == "class":
            return self._emit_class_cmp(expr, left, right)
        raise IRError(f"cannot compare {kind}")

    def _emit_identity(self, op: str, kind: str, left: str, right: str) -> str:
        """`is`/`is not`: pointer identity for heap values, backing-pointer
        identity for str/bytes. Scalar kinds never reach here (the lowerer
        emits plain ==/!= for them)."""
        pred = "eq" if op == "is" else "ne"
        reg = self.tmp()
        if kind in {"str", "bytes"}:
            left_ptr = self.tmp()
            right_ptr = self.tmp()
            self.line(f"{left_ptr} = extractvalue %ps.str {left}, 1")
            self.line(f"{right_ptr} = extractvalue %ps.str {right}, 1")
            self.line(f"{reg} = icmp {pred} ptr {left_ptr}, {right_ptr}")
            return reg
        if kind in {"list", "dict", "class", "exc"}:
            self.line(f"{reg} = icmp {pred} ptr {left}, {right}")
            return reg
        raise IRError(f"cannot compare {kind} identity")

    def _emit_none_side(self, expr: dict[str, Any]) -> None:
        """Emit a call that returns None. A None constant or a None local is not a register."""
        if expr["type"]["kind"] != "none":
            self.emit_expr(expr)
            return
        if expr["kind"] == "const" or expr["kind"] == "name":
            return
        self.emit_expr(expr)

    def emit_call(self, expr: dict[str, Any]) -> str:
        callee = expr["callee"]
        values = [self.emit_expr(arg) for arg in expr["args"]]
        if callee["kind"] == "builtin":
            return self.emit_builtin(callee["name"], values, [arg["type"] for arg in expr["args"]], expr)
        if callee["kind"] == "module":
            return self.emit_module_call(expr)
        if callee["kind"] == "method":
            return self.emit_method(expr, values)
        if callee["kind"] == "class":
            return self.emit_constructor(callee["name"], values)
        return self.call_function(self.functions[callee["name"]], values)

    def emit_builtin(self, name: str, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> str:
        if name == "print":
            self._emit_print(values, arg_types, expr)
            return ""
        if name == "len" and arg_types[0]["kind"] in {"str", "bytes"}:
            reg = self.tmp()
            self.line(f"{reg} = extractvalue %ps.str {values[0]}, 0")
            return reg
        if name == "len" and arg_types[0]["kind"] == "list":
            self.needed.add("seq")
            return self._call_ret("i64", "ps_seq_len", [f"ptr {values[0]}"])
        if name == "len" and arg_types[0]["kind"] == "dict":
            self.needed.add("map")
            return self._call_ret("i64", "ps_map_len", [f"ptr {values[0]}"])
        if name == "len" and arg_types[0]["kind"] == "class" and arg_types[0].get("name") == "Deque":
            self._abi_decl("ps_deque_len", "i64", ["ptr"])
            return self._call_ret("i64", "ps_deque_len", [f"ptr {values[0]}"])
        if name == "id":
            # CPython's id() is the object's address; same here for heap values.
            # str/bytes carry their buffer in field 1 — the stable identity.
            reg = self.tmp()
            if arg_types[0]["kind"] in {"str", "bytes"}:
                ptr = self.tmp()
                self.line(f"{ptr} = extractvalue %ps.str {values[0]}, 1")
                self.line(f"{reg} = ptrtoint ptr {ptr} to i64")
                return reg
            self.line(f"{reg} = ptrtoint ptr {values[0]} to i64")
            return reg
        if name in {"min", "max"}:
            return self._emit_minmax(name, values, arg_types)
        if name == "int":
            if arg_types[0]["kind"] in {"bytes", "str"}:
                self._abi_decl("ps_int_from_bytes", "i64", ["i64", "ptr"])
                length, ptr = self._buf_parts(values[0])
                return self._call_ret("i64", "ps_int_from_bytes", [f"i64 {length}", f"ptr {ptr}"])
            self.needed.add("math")
            return self._call_ret("i64", "ps_float_to_int", [f"double {values[0]}"])
        if name == "float" and arg_types[0]["kind"] == "str":
            self._abi_decl("ps_float_from_str", "double", ["i64", "ptr"])
            length, ptr = self._buf_parts(values[0])
            return self._call_ret("double", "ps_float_from_str", [f"i64 {length}", f"ptr {ptr}"])
        if name == "str":
            kind = arg_types[0]["kind"]
            if kind == "str":
                return values[0]
            if kind == "int":
                self._abi_decl("ps_i64_str", "void", ["i64", "ptr"])
                return self._buf_out("ps_i64_str", [f"i64 {values[0]}"])
            if kind == "float":
                self._abi_decl("ps_f64_str", "void", ["double", "ptr"])
                return self._buf_out("ps_f64_str", [f"double {values[0]}"])
            if kind == "bool":
                t_len = self.tmp()
                t_ptr = self.tmp()
                self.line(f"{t_len} = select i1 {values[0]}, i64 4, i64 5")
                self.line(
                    f"{t_ptr} = select i1 {values[0]}, ptr {self.format_global('boolT', b'True')}, "
                    f"ptr {self.format_global('boolF', b'False')}"
                )
                agg = self.tmp()
                self.line(f"{agg} = insertvalue %ps.str poison, i64 {t_len}, 0")
                ret = self.tmp()
                self.line(f"{ret} = insertvalue %ps.str {agg}, ptr {t_ptr}, 1")
                return ret
            if kind == "bytes":
                self._abi_decl("ps_repr_bytes", "void", ["i64", "ptr", "ptr"])
                length, ptr = self._buf_parts(values[0])
                return self._buf_out("ps_repr_bytes", [f"i64 {length}", f"ptr {ptr}"])
            if kind == "exc":
                self._abi_decl("ps_exc_str", "%ps.str", ["ptr"])
                return self._call_ret("%ps.str", "ps_exc_str", [f"ptr {values[0]}"])
            if kind == "class" and arg_types[0].get("name") == "Path":
                self._abi_decl("ps_path_posix", "void", ["ptr", "ptr"])
                return self._buf_out("ps_path_posix", [f"ptr {values[0]}"])
        raise IRError(f"no LLVM lowering for builtin {name}")

    def emit_module_call(self, expr: dict[str, Any]) -> str:
        callee = expr["callee"]
        module = callee["module"]
        name = callee["name"]
        args = expr["args"]
        values = [self.emit_expr(arg) for arg in args]
        if expr.get("fn"):
            return self._emit_reduce(expr)
        if name == "exit":
            return self._emit_exit(values[0])
        if module == "atexit" and name == "register":
            return self._emit_atexit(expr)
        if expr.get("symbol") == "ps_path_join":
            return self._emit_join(values)
        if expr.get("symbol") in {"ps_deque_append", "ps_deque_appendleft"}:
            return self._emit_deque_store(expr, values)
        if expr.get("symbol") in {"ps_deque_pop", "ps_deque_popleft"}:
            return self._emit_deque_load(expr, values)
        if expr.get("symbol") == "ps_list_push":
            return self._emit_list_push(expr, values)
        if expr.get("symbol") == "ps_map_del":
            self.needed.add("map")
            key = self._stash(expr["args"][1]["type"], values[1])
            self._abi_decl("ps_map_del", "void", ["ptr", "ptr"])
            self._call_void("ps_map_del", [f"ptr {values[0]}", f"ptr {key}"])
            return ""
        if expr.get("symbol") == "ps_dict_items":
            # The element is a synthetic tuple_N class; the C side packs key and
            # value cells at byte offsets read off the class layout.
            tname = expr["type"]["elem"]["name"]
            size_ptr = self.tmp()
            size = self.tmp()
            self.line(f"{size_ptr} = getelementptr %cls.{tname}, ptr null, i64 1")
            self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
            voff_ptr = self.tmp()
            voff = self.tmp()
            self.line(f"{voff_ptr} = getelementptr %cls.{tname}, ptr null, i32 0, i32 1")
            self.line(f"{voff} = ptrtoint ptr {voff_ptr} to i64")
            self._abi_decl("ps_dict_items", "ptr", ["ptr", "i64", "i64"])
            return self._call_ret("ptr", "ps_dict_items", [f"ptr {values[0]}", f"i64 {voff}", f"i64 {size}"])
        if expr.get("symbol") == "ps_str_format":
            # args[0] is the format string; the rest marshal into %ps.arg.
            count = len(values) - 1
            nkw = expr.get("format_kwnames", 0)
            style = {"percent": 0, "dot": 1, "map": 2}[expr.get("format_style", "percent")]
            if count:
                self._ensure_arg_type()
                slot = self.tmp()
                self.line(f"{slot} = alloca [{count} x %ps.arg]")
                for index in range(count):
                    base = self.tmp()
                    self.line(f"{base} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 {index}")
                    self._store_ps_arg(base, values[index + 1], {**args[index + 1]["type"], "fmt": 1})
                arg_ptr = self.tmp()
                self.line(f"{arg_ptr} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 0")
            else:
                arg_ptr = "null"
            flen, fptr = self._buf_parts(values[0])
            self._abi_decl("ps_str_format", "void", ["i64", "ptr", "ptr", "i64", "i64", "i64", "ptr"])
            return self._buf_out(
                "ps_str_format",
                [
                    f"i64 {flen}",
                    f"ptr {fptr}",
                    f"ptr {arg_ptr}",
                    f"i64 {count}",
                    f"i64 {nkw}",
                    f"i64 {style}",
                ],
            )
        if expr.get("symbol"):
            return self._emit_abi(expr, values)
        if module == "math":
            return self._emit_math(name, values)
        if module == "re":
            self.needed.add("re")
            return self._emit_re(name, values)
        if module == "json":
            self.needed.add("json")
            if name == "loads":
                return self._emit_json_loads(values[0], expr["type"])
            return self._emit_json_dumps(values[0], args[0]["type"])
        if module == "urllib.parse":
            self.needed.add("url")
            return self._emit_url(name, values)
        if module == "base64":
            self.needed.add("b64")
            return self._emit_b64(name, values)
        if module == "hashlib":
            self.needed.add("hash")
            return self._emit_hash(name, values)
        if module == "struct":
            self.needed.add("struct")
            return self._emit_struct(name, values, args, expr["type"])
        raise IRError(f"no LLVM lowering for {module}.{name}")

    def _emit_math(self, name: str, values: list[str]) -> str:
        spec = MATH_FUNCTIONS[name]
        if name == "isnan":
            reg = self.tmp()
            self.line(f"{reg} = fcmp uno double {values[0]}, 0.0")
            return reg
        if name == "isinf":
            abs_reg = self.tmp()
            self.line(f"{abs_reg} = call double @llvm.fabs.f64(double {values[0]})")
            self.needed.add("fabs")
            reg = self.tmp()
            self.line(f"{reg} = fcmp oeq double {abs_reg}, 0x7FF0000000000000")
            return reg
        self.needed.add("math")
        return self._call_ret(spec["llvm"], spec["symbol"], [f"double {value}" for value in values])

    def _emit_re(self, name: str, values: list[str]) -> str:
        if name == "compile":
            return self._emit_pattern(values[0])
        if name == "escape":
            length, ptr = self._buf_parts(values[0])
            return self._buf_out("ps_re_escape", [f"i64 {length}", f"ptr {ptr}"])
        if name == "findall":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            text_len, text_ptr = self._buf_parts(values[1])
            return self._call_ret(
                "ptr",
                "ps_re_findall",
                [f"i64 {pattern_len}", f"ptr {pattern_ptr}", f"i64 {text_len}", f"ptr {text_ptr}"],
            )
        if name == "split":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            text_len, text_ptr = self._buf_parts(values[1])
            return self._call_ret(
                "ptr",
                "ps_re_split",
                [f"i64 {pattern_len}", f"ptr {pattern_ptr}", f"i64 {text_len}", f"ptr {text_ptr}"],
            )
        if name == "sub":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            repl_len, repl_ptr = self._buf_parts(values[1])
            text_len, text_ptr = self._buf_parts(values[2])
            return self._buf_out(
                "ps_re_sub",
                [
                    f"i64 {pattern_len}",
                    f"ptr {pattern_ptr}",
                    f"i64 {repl_len}",
                    f"ptr {repl_ptr}",
                    f"i64 {text_len}",
                    f"ptr {text_ptr}",
                ],
            )
        raise IRError(f"no LLVM lowering for re.{name}")

    def _emit_pattern(self, pattern: str) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.Pattern, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        slot = self.tmp()
        self.line(f"{slot} = getelementptr %cls.Pattern, ptr {mem}, i32 0, i32 0")
        self.line(f"store %ps.str {pattern}, ptr {slot}")
        return mem

    def _emit_b64(self, name: str, values: list[str]) -> str:
        symbols = {
            "b64encode": "ps_b64_std_encode",
            "standard_b64encode": "ps_b64_std_encode",
            "urlsafe_b64encode": "ps_b64_url_encode",
            "b64decode": "ps_b64_std_decode",
            "standard_b64decode": "ps_b64_std_decode",
            "urlsafe_b64decode": "ps_b64_url_decode",
        }
        length, ptr = self._buf_parts(values[0])
        return self._buf_out(symbols[name], [f"i64 {length}", f"ptr {ptr}"])

    def _emit_hash(self, name: str, values: list[str]) -> str:
        symbols = {
            "md5": "ps_hash_md5",
            "sha1": "ps_hash_sha1",
            "sha256": "ps_hash_sha256",
            "sha384": "ps_hash_sha384",
            "sha512": "ps_hash_sha512",
        }
        if name in symbols:
            length, ptr = self._buf_parts(values[0])
            return self._call_ret("ptr", symbols[name], [f"i64 {length}", f"ptr {ptr}"])
        if name == "hexdigest":
            return self._buf_out("ps_hash_hex", [f"ptr {values[0]}"])
        if name == "digest":
            return self._buf_out("ps_hash_digest", [f"ptr {values[0]}"])
        raise IRError(f"no LLVM lowering for hashlib.{name}")

    def _emit_url(self, name: str, values: list[str]) -> str:
        if name in {"quote", "quote_plus"}:
            text_len, text_ptr = self._buf_parts(values[0])
            safe_len, safe_ptr = self._buf_parts(values[1])
            symbol = "ps_url_quote" if name == "quote" else "ps_url_quote_plus"
            return self._buf_out(symbol, [f"i64 {text_len}", f"ptr {text_ptr}", f"i64 {safe_len}", f"ptr {safe_ptr}"])
        if name in {"unquote", "unquote_plus"}:
            length, ptr = self._buf_parts(values[0])
            symbol = "ps_url_unquote" if name == "unquote" else "ps_url_unquote_plus"
            return self._buf_out(symbol, [f"i64 {length}", f"ptr {ptr}"])
        if name == "urlencode":
            return self._buf_out("ps_url_encode", [f"ptr {values[0]}"])
        if name == "parse_qs":
            length, ptr = self._buf_parts(values[0])
            return self._call_ret("ptr", "ps_url_parse_qs", [f"i64 {length}", f"ptr {ptr}"])
        if name == "urlsplit":
            return self._emit_urlsplit(values[0])
        raise IRError(f"no LLVM lowering for urllib.parse.{name}")

    def _emit_urlsplit(self, value: str) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.SplitResult, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        length, ptr = self._buf_parts(value)
        self.line(f"call void @ps_url_split(i64 {length}, ptr {ptr}, ptr {mem})")
        return mem

    def _emit_struct(self, name: str, values: list[str], args: list[dict[str, Any]], typ: dict[str, Any]) -> str:
        if name == "pack":
            return self._emit_struct_pack(values, args)
        elem = typ["elem"]["kind"]
        want = {"int": 1, "float": 2, "bytes": 3}[elem]
        fmt_len, fmt_ptr = self._buf_parts(values[0])
        data_len, data_ptr = self._buf_parts(values[1])
        return self._call_ret(
            "ptr",
            "ps_struct_unpack",
            [f"i64 {fmt_len}", f"ptr {fmt_ptr}", f"i64 {data_len}", f"ptr {data_ptr}", f"i32 {want}"],
        )

    def _emit_struct_pack(self, values: list[str], args: list[dict[str, Any]]) -> str:
        fmt_len, fmt_ptr = self._buf_parts(values[0])
        payload = list(zip(values[1:], args[1:], strict=True))
        if not payload:
            arg_ptr = "null"
        else:
            self._ensure_arg_type()
            count = len(payload)
            slot = self.tmp()
            self.line(f"{slot} = alloca [{count} x %ps.arg]")
            for index, (value, arg) in enumerate(payload):
                base = self.tmp()
                self.line(f"{base} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 {index}")
                self._store_ps_arg(base, value, arg["type"])
            arg_ptr = self.tmp()
            self.line(f"{arg_ptr} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 0")
        return self._buf_out(
            "ps_struct_pack", [f"i64 {fmt_len}", f"ptr {fmt_ptr}", f"ptr {arg_ptr}", f"i64 {len(payload)}"]
        )

    def _ensure_arg_type(self) -> None:
        line = "%ps.arg = type { i32, i32, i64, double, %ps.str }"
        if line not in self.globals:
            self.globals.append(line)

    def _store_ps_arg(self, base: str, value: str, typ: dict[str, Any]) -> None:
        kind = typ["kind"]
        code = {"int": 1, "float": 2}.get(kind)
        if code is None:
            code = {"str": 3, "bool": 4, "bytes": 6, "dict": 7}.get(kind, 3) if typ.get("fmt") else 3
        kind_ptr = self.tmp()
        self.line(f"{kind_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 0")
        self.line(f"store i32 {code}, ptr {kind_ptr}")
        if kind == "int":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            self.line(f"store i64 {value}, ptr {slot}")
            return
        if kind == "float":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 3")
            self.line(f"store double {value}, ptr {slot}")
            return
        if kind == "bool":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            wide = self.tmp()
            self.line(f"{wide} = zext i1 {value} to i64")
            self.line(f"store i64 {wide}, ptr {slot}")
            return
        if kind == "dict":
            vkind = {"int": 1, "float": 2, "str": 3, "bytes": 6}[typ["value"]["kind"]]
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            self.line(f"store i64 {vkind}, ptr {slot}")
            b_ptr = self.tmp()
            self.line(f"{b_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 4")
            data = self.tmp()
            self.line(f"{data} = getelementptr %ps.str, ptr {b_ptr}, i32 0, i32 1")
            self.line(f"store ptr {value}, ptr {data}")
            return
        slot = self.tmp()
        self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 4")
        self.line(f"store %ps.str {value}, ptr {slot}")

    def _emit_json_loads(self, text: str, typ: dict[str, Any]) -> str:
        symbol, root = self._json_global(typ)
        length, ptr = self._buf_parts(text)
        slot = self._json_alloca(typ)
        self.line(f"call void @ps_json_loads(i64 {length}, ptr {ptr}, ptr {symbol}, i32 {root}, ptr {slot})")
        return self._json_read(typ, slot)

    def _emit_json_dumps(self, value: str, typ: dict[str, Any]) -> str:
        symbol, root = self._json_global(typ)
        slot = self._json_write(typ, value)
        return self._buf_out("ps_json_dumps", [f"ptr {symbol}", f"i32 {root}", f"ptr {slot}"])

    def _json_global(self, typ: dict[str, Any]) -> tuple[str, int]:
        entries: list[tuple[int, int] | None] = []

        def add(node: dict[str, Any]) -> int:
            idx = len(entries)
            entries.append(None)
            kind = node["kind"]
            if kind == "int":
                entries[idx] = (1, 0)
            elif kind == "bool":
                entries[idx] = (2, 0)
            elif kind == "str":
                entries[idx] = (3, 0)
            elif kind == "list":
                child = add(node["elem"])
                entries[idx] = (4, child)
            elif kind == "dict":
                child = add(node["value"])
                entries[idx] = (5, child)
            else:
                raise IRError(f"no JSON type for {kind}")
            return idx

        root = add(typ)
        typed = ", ".join(f"%ps.json.ty {{ i32 {kind}, i32 {child} }}" for kind, child in entries if kind is not None)
        symbol = f"@ps.json.{self.json_n}"
        self.json_n += 1
        if "%ps.json.ty = type { i32, i32 }" not in self.globals:
            self.globals.append("%ps.json.ty = type { i32, i32 }")
        self.globals.append(f"{symbol} = private constant [{len(entries)} x %ps.json.ty] [{typed}]")
        return symbol, root

    def _json_alloca(self, typ: dict[str, Any]) -> str:
        kind = typ["kind"]
        if kind == "bool":
            return self._alloca("i32")
        if kind in {"list", "dict"}:
            return self._alloca("ptr")
        return self._alloca(_llvm_type(typ))

    def _json_read(self, typ: dict[str, Any], slot: str) -> str:
        kind = typ["kind"]
        if kind == "bool":
            raw = self._load("i32", slot)
            reg = self.tmp()
            self.line(f"{reg} = trunc i32 {raw} to i1")
            return reg
        if kind in {"list", "dict"}:
            return self._load("ptr", slot)
        return self._load(_llvm_type(typ), slot)

    def _json_write(self, typ: dict[str, Any], value: str) -> str:
        slot = self._json_alloca(typ)
        kind = typ["kind"]
        if kind == "bool":
            wide = self.tmp()
            self.line(f"{wide} = zext i1 {value} to i32")
            self.line(f"store i32 {wide}, ptr {slot}")
            return slot
        if kind in {"list", "dict"}:
            self.line(f"store ptr {value}, ptr {slot}")
            return slot
        self.line(f"store {_llvm_type(typ)} {value}, ptr {slot}")
        return slot

    def _buf_out(self, symbol: str, args: list[str]) -> str:
        out = self._alloca("%ps.str")
        self.line(f"call void @{symbol}({', '.join([*args, f'ptr {out}'])})")
        return self._load("%ps.str", out)

    def _abi_decl(self, symbol: str, ret: str, params: list[str]) -> None:
        self.abi_decls[symbol] = (ret, params)

    def _abi_lines(self) -> list[str]:
        lines = []
        for symbol, (ret, params) in self.abi_decls.items():
            if symbol == "exit":
                lines.append("declare void @exit(i32) noreturn")
                continue
            rendered = ", ".join(params)
            lines.append(f"declare {ret} @{symbol}({rendered})")
        return lines

    def _call_void(self, name: str, args: list[str]) -> None:
        self.line(f"call void @{name}({', '.join(args)})")

    def _flag(self, raw: str) -> str:
        reg = self.tmp()
        self.line(f"{reg} = icmp ne i32 {raw}, 0")
        return reg

    def _alloc_class(self, name: str) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        return self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])

    def _c_pieces(self, value: str, typ: dict[str, Any]) -> list[tuple[str, str]]:
        kind = typ["kind"]
        if kind == "int":
            return [(f"i64 {value}", "i64")]
        if kind == "float":
            return [(f"double {value}", "double")]
        if kind in {"str", "bytes"}:
            length, ptr = self._buf_parts(value)
            return [(f"i64 {length}", "i64"), (f"ptr {ptr}", "ptr")]
        if kind in {"list", "dict", "class", "exc"}:
            return [(f"ptr {value}", "ptr")]
        raise IRError(f"cannot pass {kind} to a runtime call")

    def _emit_abi(self, expr: dict[str, Any], values: list[str]) -> str:
        symbol = expr["symbol"]
        rendered: list[str] = []
        types: list[str] = []
        for value, arg in zip(values, expr["args"], strict=True):
            for piece, typ in self._c_pieces(value, arg["type"]):
                rendered.append(piece)
                types.append(typ)
        ret = expr["type"]
        kind = ret["kind"]
        if kind in {"str", "bytes"}:
            self._abi_decl(symbol, "void", [*types, "ptr"])
            return self._buf_out(symbol, rendered)
        if kind == "int":
            self._abi_decl(symbol, "i64", types)
            return self._call_ret("i64", symbol, rendered)
        if kind == "float":
            self._abi_decl(symbol, "double", types)
            return self._call_ret("double", symbol, rendered)
        if kind == "bool":
            self._abi_decl(symbol, "i32", types)
            return self._flag(self._call_ret("i32", symbol, rendered))
        if kind == "none":
            self._abi_decl(symbol, "void", types)
            self._call_void(symbol, rendered)
            return ""
        if kind == "list" or (kind == "class" and ret.get("name") in {"Deque", "TextIO", "BytesIO"}):
            self._abi_decl(symbol, "ptr", types)
            return self._call_ret("ptr", symbol, rendered)
        if kind == "class":
            self._abi_decl(symbol, "void", [*types, "ptr"])
            mem = self._alloc_class(ret["name"])
            self._call_void(symbol, [*rendered, f"ptr {mem}"])
            return mem
        raise IRError(f"cannot emit {symbol}")

    def _emit_list_push(self, expr: dict[str, Any], values: list[str]) -> str:
        seq, elem = values
        slot = self._stash(expr["args"][1]["type"], elem)
        self._abi_decl("ps_seq_push", "void", ["ptr", "ptr"])
        self._call_void("ps_seq_push", [f"ptr {seq}", f"ptr {slot}"])
        return ""

    def _emit_atexit(self, expr: dict[str, Any]) -> str:
        name = expr["args"][0]["value"]
        fn = self.functions.get(name)
        if fn is None:
            raise IRError(f"atexit.register target {name} was not lowered")
        symbol = _fn_symbol(name)
        wrap = f"ps.atexit.{self.atexit_n}"
        self.atexit_n += 1
        ret = fn["returnType"]
        if ret["kind"] == "none":
            body = f"  call void @{symbol}()\n  ret void\n"
        else:
            body = f"  call {_llvm_type(ret)} @{symbol}()\n  ret void\n"
        self.outline_lines.append(f"define internal void @{wrap}() {{\nentry:\n{body}}}")
        self._abi_decl("atexit", "i32", ["ptr"])
        reg = self.tmp()
        self.line(f"{reg} = call i32 @atexit(ptr @{wrap})")
        return ""

    def _emit_exit(self, value: str) -> str:
        self._abi_decl("exit", "void", ["i32"])
        narrow = self.tmp()
        self.line(f"{narrow} = trunc i64 {value} to i32")
        self._call_void("exit", [f"i32 {narrow}"])
        self.line("unreachable")
        self.terminated = True
        return ""

    def _emit_join(self, values: list[str]) -> str:
        count = len(values)
        slot = self.tmp()
        self.line(f"{slot} = alloca [{count} x %ps.str]")
        for index, value in enumerate(values):
            dest = self.tmp()
            self.line(f"{dest} = getelementptr [{count} x %ps.str], ptr {slot}, i64 0, i64 {index}")
            self.line(f"store %ps.str {value}, ptr {dest}")
        first = self.tmp()
        self.line(f"{first} = getelementptr [{count} x %ps.str], ptr {slot}, i64 0, i64 0")
        self._abi_decl("ps_path_join", "void", ["ptr", "i64", "ptr"])
        return self._buf_out("ps_path_join", [f"ptr {first}", f"i64 {count}"])

    def _emit_deque_store(self, expr: dict[str, Any], values: list[str]) -> str:
        slot = self._stash(expr["args"][1]["type"], values[1])
        symbol = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {slot}"])
        return ""

    def _emit_deque_load(self, expr: dict[str, Any], values: list[str]) -> str:
        out = self._alloca(_llvm_type(expr["type"]))
        symbol = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {out}"])
        return self._load(_llvm_type(expr["type"]), out)

    def _emit_class_cmp(self, expr: dict[str, Any], left: str, right: str) -> str:
        name = expr["left"]["type"]["name"]
        op = expr["op"]
        if name == "Path":
            left_ptr = self.tmp()
            right_ptr = self.tmp()
            self.line(f"{left_ptr} = getelementptr %cls.Path, ptr {left}, i32 0, i32 0")
            self.line(f"{right_ptr} = getelementptr %cls.Path, ptr {right}, i32 0, i32 0")
            cmp = self._buf_cmp(self._load("%ps.str", left_ptr), self._load("%ps.str", right_ptr))
        elif name in COMPARE_CLASSES:
            count = len(self.class_index[name]["fields"])
            self._abi_decl("ps_i64_lex", "i32", ["ptr", "ptr", "i64"])
            cmp = self._call_ret("i32", "ps_i64_lex", [f"ptr {left}", f"ptr {right}", f"i64 {count}"])
        else:
            # A user class without __eq__ compares by identity in CPython.
            if op not in {"==", "!="}:
                raise IRError(f"cannot order {name}")
            pred = "eq" if op == "==" else "ne"
            reg = self.tmp()
            self.line(f"{reg} = icmp {pred} ptr {left}, {right}")
            return reg
        pred = {"==": "eq", "!=": "ne", "<": "slt", "<=": "sle", ">": "sgt", ">=": "sge"}[op]
        reg = self.tmp()
        self.line(f"{reg} = icmp {pred} i32 {cmp}, 0")
        return reg

    def _emit_class_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        left_name = expr["left"]["type"]["name"]
        right_name = expr["right"]["type"]["name"]
        out_name = expr["type"]["name"]
        mem = self._alloc_class(out_name)
        sign = "1" if expr["op"] == "+" else "-1"
        if left_name in {"Date", "DateTime"} and right_name == "TimeDelta":
            symbol = "ps_date_add" if left_name == "Date" else "ps_dt_add"
            self._abi_decl(symbol, "void", ["ptr", "ptr", "i32", "ptr"])
            self._call_void(symbol, [f"ptr {left}", f"ptr {right}", f"i32 {sign}", f"ptr {mem}"])
            return mem
        if left_name == "TimeDelta" and right_name == "TimeDelta":
            self._abi_decl("ps_delta_add", "void", ["ptr", "ptr", "i32", "ptr"])
            self._call_void("ps_delta_add", [f"ptr {left}", f"ptr {right}", f"i32 {sign}", f"ptr {mem}"])
            return mem
        if expr["op"] == "-" and left_name == right_name and left_name in {"Date", "DateTime"}:
            symbol = "ps_date_diff" if left_name == "Date" else "ps_dt_diff"
            self._abi_decl(symbol, "void", ["ptr", "ptr", "ptr"])
            self._call_void(symbol, [f"ptr {left}", f"ptr {right}", f"ptr {mem}"])
            return mem
        raise IRError(f"cannot emit {expr['op']} on {left_name}")

    def _emit_minmax(self, name: str, values: list[str], arg_types: list[dict[str, Any]]) -> str:
        kind = arg_types[0]["kind"]
        left, right = values
        if kind == "int":
            pred = "sle" if name == "min" else "sge"
            cmp = self.tmp()
            self.line(f"{cmp} = icmp {pred} i64 {left}, {right}")
            reg = self.tmp()
            self.line(f"{reg} = select i1 {cmp}, i64 {left}, i64 {right}")
            return reg
        if kind == "float":
            pred = "ole" if name == "min" else "oge"
            cmp = self.tmp()
            self.line(f"{cmp} = fcmp {pred} double {left}, {right}")
            reg = self.tmp()
            self.line(f"{reg} = select i1 {cmp}, double {left}, double {right}")
            return reg
        cmp = self._buf_cmp(left, right)
        pred = "sle" if name == "min" else "sge"
        flag = self.tmp()
        self.line(f"{flag} = icmp {pred} i32 {cmp}, 0")
        reg = self.tmp()
        self.line(f"{reg} = select i1 {flag}, %ps.str {left}, %ps.str {right}")
        return reg

    def _emit_reduce(self, expr: dict[str, Any]) -> str:
        seq = self.emit_expr(expr["args"][0])
        elem = expr["args"][0]["type"]["elem"]
        llvm_t = _llvm_type(elem)
        self.needed.add("seq")
        length = self._call_ret("i64", "ps_seq_len", [f"ptr {seq}"])
        acc_slot = self._alloca(llvm_t)
        idx_slot = self._alloca("i64")
        if len(expr["args"]) == 2:
            init = self.emit_expr(expr["args"][1])
            self.line(f"store {llvm_t} {init}, ptr {acc_slot}")
            self.line(f"store i64 0, ptr {idx_slot}")
        else:
            empty = self.tmp()
            self.line(f"{empty} = icmp eq i64 {length}, 0")
            bad = self.fresh("red.empty")
            ok = self.fresh("red.start")
            self.line(f"br i1 {empty}, label %{bad}, label %{ok}")
            self.terminated = True
            self.block(bad)
            self.needed.add("exc")
            self.line("call void @ps_raise_code(i32 1) noreturn")
            self.line("unreachable")
            self.terminated = True
            self.block(ok)
            first = self._seq_at(seq, "0", elem)
            self.line(f"store {llvm_t} {first}, ptr {acc_slot}")
            self.line(f"store i64 1, ptr {idx_slot}")
        cond_l = self.fresh("red.cond")
        body_l = self.fresh("red.body")
        end_l = self.fresh("red.end")
        self.line(f"br label %{cond_l}")
        self.terminated = True
        self.block(cond_l)
        idx = self._load("i64", idx_slot)
        cmp = self.tmp()
        self.line(f"{cmp} = icmp slt i64 {idx}, {length}")
        self.line(f"br i1 {cmp}, label %{body_l}, label %{end_l}")
        self.terminated = True
        self.block(body_l)
        current = self._load(llvm_t, acc_slot)
        item = self._seq_at(seq, idx, elem)
        nxt = self.call_function(self.functions[expr["fn"]], [current, item])
        self.line(f"store {llvm_t} {nxt}, ptr {acc_slot}")
        bumped = self.tmp()
        self.line(f"{bumped} = add i64 {idx}, 1")
        self.line(f"store i64 {bumped}, ptr {idx_slot}")
        self.line(f"br label %{cond_l}")
        self.terminated = True
        self.block(end_l)
        return self._load(llvm_t, acc_slot)

    def _seq_at(self, seq: str, index: str, elem: dict[str, Any]) -> str:
        self.needed.add("seq")
        out = self._alloca(_llvm_type(elem))
        self.line(f"call void @ps_seq_get(ptr {seq}, i64 {index}, ptr {out})")
        return self._load(_llvm_type(elem), out)

    def emit_method(self, expr: dict[str, Any], values: list[str]) -> str:
        callee = expr["callee"]
        name = callee["name"]
        kind = str(callee.get("receiver", "str"))
        self.needed.add("str")
        length, ptr = self._buf_parts(values[0])
        if name in {"strip", "lstrip", "rstrip", "upper", "lower"}:
            out = self._alloca("%ps.str")
            self.line(f"call void @ps_str_{name}(i64 {length}, ptr {ptr}, ptr {out})")
            return self._load("%ps.str", out)
        if name in {"title", "capitalize", "casefold", "swapcase"}:
            self._abi_decl(f"ps_str_{name}", "void", ["i64", "ptr", "ptr"])
            return self._buf_out(f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}"])
        if name in {"isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower"}:
            self._abi_decl(f"ps_str_{name}", "i32", ["i64", "ptr"])
            return self._flag(self._call_ret("i32", f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}"]))
        if name == "encode":
            self._abi_decl("ps_utf8_encode", "void", ["i64", "ptr", "ptr"])
            return self._buf_out("ps_utf8_encode", [f"i64 {length}", f"ptr {ptr}"])
        if name == "decode":
            self._abi_decl("ps_utf8_decode", "void", ["i64", "ptr", "ptr"])
            return self._buf_out("ps_utf8_decode", [f"i64 {length}", f"ptr {ptr}"])
        if name == "splitlines":
            self._abi_decl("ps_str_splitlines", "ptr", ["i64", "ptr", "i64"])
            return self._call_ret("ptr", "ps_str_splitlines", [f"i64 {length}", f"ptr {ptr}", "i64 0"])
        if name == "expandtabs":
            tabsize = values[1] if len(values) > 1 else "8"
            self._abi_decl("ps_str_expandtabs", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_expandtabs", [f"i64 {length}", f"ptr {ptr}", f"i64 {tabsize}"])
        if name == "zfill":
            self._abi_decl("ps_str_zfill", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_zfill", [f"i64 {length}", f"ptr {ptr}", f"i64 {values[1]}"])
        if name in {"ljust", "rjust", "center"}:
            side = {"ljust": "0", "rjust": "1", "center": "2"}[name]
            if len(values) > 2:
                fill_len, fill_ptr = self._buf_parts(values[2])
            else:
                fill_len, fill_ptr = "1", self.format_global("fill", b" ")
            self._abi_decl("ps_str_justify", "void", ["i64", "ptr", "i64", "i64", "ptr", "i64", "ptr"])
            return self._buf_out(
                "ps_str_justify",
                [
                    f"i64 {length}",
                    f"ptr {ptr}",
                    f"i64 {values[1]}",
                    f"i64 {fill_len}",
                    f"ptr {fill_ptr}",
                    f"i64 {side}",
                ],
            )
        if name in {"partition", "rpartition"}:
            sep_len, sep_ptr = self._buf_parts(values[1])
            tname = callee["tuple"]
            mem = self._alloc_class(tname)
            right = "1" if name == "rpartition" else "0"
            self._abi_decl("ps_str_partition", "void", ["i64", "ptr", "i64", "ptr", "i64", "ptr"])
            self._call_void(
                "ps_str_partition",
                [
                    f"i64 {length}",
                    f"ptr {ptr}",
                    f"i64 {sep_len}",
                    f"ptr {sep_ptr}",
                    f"i64 {right}",
                    f"ptr {mem}",
                ],
            )
            return mem
        if name in {"split", "rsplit"}:
            if len(values) == 1:
                sep_len, sep_ptr = "0", "null"
                mode = "2" if kind == "bytes" else "1"
                maxsplit = "-1"
            else:
                sep_len, sep_ptr = self._buf_parts(values[1])
                mode = "0"
                maxsplit = values[2] if len(values) > 2 else "-1"
            right = "1" if name == "rsplit" else "0"
            self._abi_decl("ps_str_split_m", "ptr", ["i64", "ptr", "i64", "ptr", "i64", "i64", "i64"])
            return self._call_ret(
                "ptr",
                "ps_str_split_m",
                [
                    f"i64 {length}",
                    f"ptr {ptr}",
                    f"i64 {sep_len}",
                    f"ptr {sep_ptr}",
                    f"i64 {mode}",
                    f"i64 {maxsplit}",
                    f"i64 {right}",
                ],
            )
        if name == "join":
            self._abi_decl("ps_str_join", "void", ["i64", "ptr", "ptr", "ptr"])
            return self._buf_out("ps_str_join", [f"i64 {length}", f"ptr {ptr}", f"ptr {values[1]}"])
        arg_len, arg_ptr = self._buf_parts(values[1])
        if name in {"removeprefix", "removesuffix"}:
            self._abi_decl(f"ps_str_{name}", "void", ["i64", "ptr", "i64", "ptr", "ptr"])
            return self._buf_out(f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}"])
        if name == "replace":
            new_len, new_ptr = self._buf_parts(values[2])
            out = self._alloca("%ps.str")
            self.line(
                "call void @ps_str_replace("
                f"i64 {length}, ptr {ptr}, i64 {arg_len}, ptr {arg_ptr}, i64 {new_len}, ptr {new_ptr}, ptr {out})"
            )
            return self._load("%ps.str", out)
        if name in {"find", "count", "index", "rfind"}:
            symbol = {"find": "ps_str_find", "count": "ps_str_count", "index": "ps_str_index", "rfind": "ps_str_rfind"}[
                name
            ]
            # ps_str_find is already declared by the "str" runtime block.
            if name != "find":
                self._abi_decl(symbol, "i64", ["i64", "ptr", "i64", "ptr"])
            return self._call_ret("i64", symbol, [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}"])
        if name in {"startswith", "endswith"}:
            flag = self._call_ret(
                "i32", f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}"]
            )
            reg = self.tmp()
            self.line(f"{reg} = icmp ne i32 {flag}, 0")
            return reg
        raise IRError(f"no LLVM lowering for method {name}")

    def emit_constructor(self, name: str, values: list[str]) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        init = f"cls.{name}.__init__"
        if init in self.functions:
            fn = self.functions[init]
            self.call_function(fn, [mem, *values])
        return mem

    def emit_list(self, expr: dict[str, Any]) -> str:
        self.needed.add("seq")
        elem = expr["type"]["elem"]
        values = [self.emit_expr(item) for item in expr["elems"]]
        reg = self._call_ret("ptr", "ps_seq_new", [f"i64 {_elem_size(elem)}", f"i64 {len(values)}"])
        for index, value in enumerate(values):
            slot = self._stash(elem, value)
            self.line(f"call void @ps_seq_set(ptr {reg}, i64 {index}, ptr {slot})")
        return reg

    def emit_dict(self, expr: dict[str, Any]) -> str:
        self.needed.add("map")
        key_t = expr["type"]["key"]
        val_t = expr["type"]["value"]
        key_kind = 1 if key_t["kind"] == "int" else 2
        stored = [(self.emit_expr(pair["key"]), self.emit_expr(pair["value"])) for pair in expr["pairs"]]
        reg = self._call_ret(
            "ptr",
            "ps_map_new",
            [
                f"i32 {key_kind}",
                f"i64 {_elem_size(key_t)}",
                f"i64 {_elem_size(val_t)}",
                f"i64 {len(stored)}",
            ],
        )
        for key_v, val_v in stored:
            key = self._stash(key_t, key_v)
            value = self._stash(val_t, val_v)
            self.line(f"call void @ps_map_put(ptr {reg}, ptr {key}, ptr {value})")
        return reg

    def emit_subscript(self, expr: dict[str, Any]) -> str:
        value = self.emit_expr(expr["value"])
        index = self.emit_expr(expr["index"])
        kind = expr["value"]["type"]["kind"]
        if kind == "list":
            self.needed.add("seq")
            out = self._alloca(_llvm_type(expr["type"]))
            self.line(f"call void @ps_seq_get(ptr {value}, i64 {index}, ptr {out})")
            return self._load(_llvm_type(expr["type"]), out)
        if kind == "dict":
            self.needed.add("map")
            key = self._stash(expr["index"]["type"], index)
            out = self._alloca(_llvm_type(expr["type"]))
            self.line(f"call void @ps_map_get(ptr {value}, ptr {key}, ptr {out})")
            return self._load(_llvm_type(expr["type"]), out)
        if kind == "str":
            self.needed.add("str")
            length, ptr = self._buf_parts(value)
            out = self._alloca("%ps.str")
            self.line(f"call void @ps_str_get(i64 {length}, ptr {ptr}, i64 {index}, ptr {out})")
            return self._load("%ps.str", out)
        if kind == "bytes":
            self.needed.add("str")
            length, ptr = self._buf_parts(value)
            return self._call_ret("i64", "ps_buf_at", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])
        if kind == "class" and expr["value"]["type"].get("name") == "Deque":
            out = self._alloca(_llvm_type(expr["type"]))
            self._abi_decl("ps_deque_get", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_deque_get", [f"ptr {value}", f"i64 {index}", f"ptr {out}"])
            return self._load(_llvm_type(expr["type"]), out)
        raise IRError(f"cannot index {kind}")

    def emit_attr(self, expr: dict[str, Any]) -> str:
        obj = self.emit_expr(expr["value"])
        field = self._field(expr["value"]["type"]["name"], expr["name"])
        slot = self._field_ptr(expr["value"]["type"]["name"], obj, field)
        return self._load(_llvm_type(field["type"]), slot)

    def emit_contains(self, expr: dict[str, Any]) -> str:
        needle = self.emit_expr(expr["needle"])
        hay = self.emit_expr(expr["hay"])
        hay_kind = expr["hay"]["type"]["kind"]
        if hay_kind in {"str", "bytes"}:
            self.needed.add("str")
            hay_len, hay_ptr = self._buf_parts(hay)
            needle_len, needle_ptr = self._buf_parts(needle)
            found = self._call_ret(
                "i64", "ps_str_find", [f"i64 {hay_len}", f"ptr {hay_ptr}", f"i64 {needle_len}", f"ptr {needle_ptr}"]
            )
            flag = self.tmp()
            self.line(f"{flag} = icmp sge i64 {found}, 0")
        elif hay_kind == "list":
            self.needed.add("seq")
            elem = expr["hay"]["type"]["elem"]["kind"]
            if elem == "int":
                flag = self._call_ret("i32", "ps_seq_has_i64", [f"ptr {hay}", f"i64 {needle}"])
            elif elem == "float":
                flag = self._call_ret("i32", "ps_seq_has_f64", [f"ptr {hay}", f"double {needle}"])
            else:
                length, ptr = self._buf_parts(needle)
                flag = self._call_ret("i32", "ps_seq_has_buf", [f"ptr {hay}", f"i64 {length}", f"ptr {ptr}"])
            bit = self.tmp()
            self.line(f"{bit} = icmp ne i32 {flag}, 0")
            flag = bit
        elif hay_kind == "dict":
            self.needed.add("map")
            key = self._stash(expr["needle"]["type"], needle)
            raw = self._call_ret("i32", "ps_map_has", [f"ptr {hay}", f"ptr {key}"])
            flag = self.tmp()
            self.line(f"{flag} = icmp ne i32 {raw}, 0")
        else:
            raise IRError(f"cannot emit containment for {hay_kind}")
        if not expr["negated"]:
            return flag
        reg = self.tmp()
        self.line(f"{reg} = xor i1 {flag}, true")
        return reg

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

    def _alloca(self, llvm_type: str) -> str:
        slot = self.tmp()
        self.line(f"{slot} = alloca {llvm_type}")
        return slot

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

    def _buf_cmp(self, left: str, right: str) -> str:
        self.needed.add("str")
        left_len, left_ptr = self._buf_parts(left)
        right_len, right_ptr = self._buf_parts(right)
        return self._call_ret(
            "i32", "ps_str_cmp", [f"i64 {left_len}", f"ptr {left_ptr}", f"i64 {right_len}", f"ptr {right_ptr}"]
        )

    def call_function(self, fn: dict[str, Any], values: list[str]) -> str:
        ret = _llvm_type(fn["returnType"])
        rendered = ", ".join(
            f"{_llvm_type(param['type'])} {value}" for param, value in zip(fn["params"], values, strict=True)
        )
        symbol = _fn_symbol(fn["name"])
        if ret == "void":
            self.line(f"call void @{symbol}({rendered})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @{symbol}({rendered})")
        return reg

    def helper_call(self, name: str, ret: str, args: list[str], arg_type: str = "i64") -> str:
        self.needed.add(name)
        rendered = ", ".join(f"{arg_type} {arg}" for arg in args)
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @ps.{name}({rendered})")
        return reg

    def string_value(self, text: str, binary: bool = False) -> str:
        data = text.encode("latin1")
        idx = len(self.string_data)
        self.string_data.append(data)
        symbol = f"@ps.str.{idx}"
        self.globals.append(f'{symbol} = private unnamed_addr constant [{len(data) + 1} x i8] c"{_llvm_bytes(data)}"')
        first = self.tmp()
        second = self.tmp()
        self.line(f"{first} = insertvalue %ps.str poison, i64 {len(data)}, 0")
        self.line(f"{second} = insertvalue %ps.str {first}, ptr {symbol}, 1")
        return second

    def materialize_arg(self, typ: dict[str, Any], value: Any) -> str:
        kind = typ["kind"]
        if kind == "int":
            return str(value)
        if kind == "float":
            return _llvm_double(value)
        if kind == "bool":
            return "true" if value else "false"
        if kind == "str":
            return self.string_value(value)
        raise FitError(f"cannot pass a {kind} argument into main")

    def print_value(self, typ: dict[str, Any], value: str) -> None:
        kind = typ["kind"]
        self.needed.add("printf")
        if kind == "int":
            fmt = self.format_global("int", b"%lld\n")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, i64 {value})")
            return
        if kind == "float":
            fmt = self.format_global("float", b"%.17g\n")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, double {value})")
            return
        if kind == "str":
            fmt = self.format_global("str", b"%.*s\n")
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, i64 {length}, ptr {ptr})")
            return
        raise IRError(f"cannot print {kind}")

    def _emit_print(self, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> None:
        """`print` with CPython defaults: space separator, newline terminator.

        `sep=`/`end=` str arguments ride on the call dict; when absent the
        constants hold. Each value writes through `printf`, mirroring
        `print_value`'s int and str pieces. Floats go through `ps_f64_str` so
        the text is the CPython `str(float)` spelling (shortest round-trip,
        `.0`, `1e+16`), which a raw `%g` conversion cannot reproduce. Bools
        select between the `True` and `False` constants, not the runtime's
        lowercase literals. Bytes print as their `b'...'` repr.
        """
        self.needed.add("printf")
        err = expr.get("file") == "stderr"
        sep = self.emit_expr(expr["sep"]) if expr.get("sep") else None
        end = self.emit_expr(expr["end"]) if expr.get("end") else None
        for index, (value, typ) in enumerate(zip(values, arg_types, strict=True)):
            if index:
                if sep is None:
                    self._print_fmt("space", b" ", err=err)
                else:
                    slen, sptr = self._buf_parts(sep)
                    self._print_fmt("sep", b"%.*s", [f"i64 {slen}", f"ptr {sptr}"], err=err)
            kind = typ["kind"]
            if kind == "int":
                self._print_fmt("int", b"%lld", [f"i64 {value}"], err=err)
            elif kind == "float":
                out = self._buf_out("ps_f64_str", [f"double {value}"])
                length, ptr = self._buf_parts(out)
                self._abi_decl("ps_f64_str", "void", ["double", "ptr"])
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
            elif kind == "bool":
                picked = self.tmp()
                self.line(
                    f"{picked} = select i1 {value}, ptr {self.format_global('boolT', b'True')}, "
                    f"ptr {self.format_global('boolF', b'False')}"
                )
                self._print_fmt("s", b"%s", [f"ptr {picked}"], err=err)
            elif kind == "bytes":
                self._abi_decl("ps_repr_bytes", "void", ["i64", "ptr", "ptr"])
                rlen, rptr = self._buf_parts(value)
                out = self._buf_out("ps_repr_bytes", [f"i64 {rlen}", f"ptr {rptr}"])
                length, ptr = self._buf_parts(out)
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
            elif kind == "exc":
                self._abi_decl("ps_exc_str", "%ps.str", ["ptr"])
                out = self._call_ret("%ps.str", "ps_exc_str", [f"ptr {value}"])
                length, ptr = self._buf_parts(out)
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
            else:
                length, ptr = self._buf_parts(value)
                self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
        if end is None:
            self._print_fmt("nl", b"\n", err=err)
        else:
            elen, eptr = self._buf_parts(end)
            self._print_fmt("end", b"%.*s", [f"i64 {elen}", f"ptr {eptr}"], err=err)

    def _print_fmt(self, name: str, data: bytes, args: list[str] | None = None, err: bool = False) -> None:
        fmt = self.format_global(name, data)
        if err:
            self.needed.add("fprintf")
            fd = self.tmp()
            self.line(f"{fd} = load ptr, ptr @stderr")
            rendered = ", ".join([f"ptr {fd}", f"ptr {fmt}", *(args or [])])
            self.line(f"call i32 (ptr, ptr, ...) @fprintf({rendered})")
            return
        rendered = ", ".join([f"ptr {fmt}", *(args or [])])
        self.line(f"call i32 (ptr, ...) @printf({rendered})")

    def format_global(self, name: str, data: bytes) -> str:
        base = f"@ps.fmt.{name}"
        body = f'private unnamed_addr constant [{len(data) + 1} x i8] c"{_llvm_bytes(data)}"'
        defined = {g.split(" = ", 1)[0] for g in self.globals}
        symbol = base
        suffix = 0
        while symbol in defined and f"{symbol} = {body}" not in self.globals:
            suffix += 1
            symbol = f"{base}.{suffix}"
        if symbol not in defined:
            self.globals.append(f"{symbol} = {body}")
        return symbol

    def store(self, name: str, value: str) -> None:
        ptr, typ = self.slots[name]
        self.line(f"store {_llvm_type(typ)} {value}, ptr {ptr}")

    def load(self, name: str) -> str:
        ptr, typ = self.slots[name]
        reg = self.tmp()
        self.line(f"{reg} = load {_llvm_type(typ)}, ptr {ptr}")
        return reg

    def block(self, name: str) -> None:
        self.out.append(f"{name}:")
        self.terminated = False

    def fresh(self, hint: str) -> str:
        self.lid += 1
        return f"L{self.lid}.{hint}"

    def tmp(self) -> str:
        self.tid += 1
        return f"%t{self.tid}"

    def line(self, text: str) -> None:
        self.out.append(f"  {text}")

    def _reset_function(self) -> None:
        self.tid = 0
        self.lid = 0
        self.slots = {}
        self.terminated = False
        self.try_depth = 0
        self.outline_depth = 0


def _fn_symbol(name: str) -> str:
    """The process entry point is ``@main``. A user function of that name gets another symbol."""
    if name == "main":
        return "ps.user.main"
    return name


def _ir_const_value(typ: dict[str, Any], value: Any) -> Any:
    kind = typ["kind"]
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    return value


def _prepare_arg(param: dict[str, Any], value: Any) -> Any:
    kind = param["type"]["kind"]
    name = param["name"]
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise FitError(f"argument {name} must be an int")
        if not fits_i64(value):
            raise FitError(f"argument {name} does not fit in i64; refusing to truncate")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, float):
            raise FitError(f"argument {name} must be a float")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise FitError(f"argument {name} must be a bool")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise FitError(f"argument {name} must be a str")
        if any(ord(ch) > 255 for ch in value):
            raise FitError(f"argument {name} is outside Latin-1 and is not lowered")
        return value
    raise FitError(f"argument {name} has type {kind}, which main cannot pass")


def _llvm_double(value: float) -> str:
    """IEEE-754 bits. Clang 18 rejects the 0x1.0p-1 spelling in call arguments."""

    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    return f"0x{bits:016X}"


def _llvm_type(typ: dict[str, Any]) -> str:
    kind = typ["kind"]
    if kind == "int":
        return "i64"
    if kind == "bool":
        return "i1"
    if kind == "float":
        return "double"
    if kind in {"str", "bytes"}:
        return "%ps.str"
    if kind in {"list", "dict", "class", "exc"}:
        return "ptr"
    if kind == "none":
        return "void"
    raise IRError(f"no LLVM type for {kind}")


def _elem_size(typ: dict[str, Any]) -> int:
    """Bytes copied into a list or dict slot. Matches the LLVM layout on this ABI."""

    kind = typ["kind"]
    if kind in {"int", "float"}:
        return 8
    if kind in {"str", "bytes"}:
        return 16
    if kind in {"list", "dict", "class"}:
        return 8
    raise IRError(f"no element size for {kind}")


def _safe(name: str) -> str:
    return name.replace(".", "_")


def _llvm_bytes(data: bytes) -> str:
    """LLVM ``c"..."`` escapes. A quote is ``\\22``; ``\\"`` ends the literal early."""

    parts: list[str] = []
    for byte in data:
        if byte == ord("\\"):
            parts.append("\\\\")
        elif 32 <= byte < 127 and byte != ord('"'):
            parts.append(chr(byte))
        else:
            parts.append(f"\\{byte:02X}")
    parts.append("\\00")
    return "".join(parts)


def _declarations(needed: set[str]) -> list[str]:
    lines: list[str] = []
    if "printf" in needed:
        lines.append("declare i32 @printf(ptr, ...)")
    if "fprintf" in needed:
        lines.append("declare i32 @fprintf(ptr, ptr, ...)")
        lines.append("@stderr = external global ptr")
    overflow = needed & {"iadd", "isub", "imul", "ineg", "strconcat"}
    if overflow or "floordiv" in needed:
        lines.append("declare void @llvm.trap() noreturn nounwind")
    if "iadd" in needed or "strconcat" in needed:
        lines.append("declare { i64, i1 } @llvm.sadd.with.overflow.i64(i64, i64)")
    if "isub" in needed or "ineg" in needed:
        lines.append("declare { i64, i1 } @llvm.ssub.with.overflow.i64(i64, i64)")
    if "imul" in needed:
        lines.append("declare { i64, i1 } @llvm.smul.with.overflow.i64(i64, i64)")
    if "strconcat" in needed:
        lines.append("declare ptr @malloc(i64)")
        lines.append("declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)")
    lines.extend(_runtime_declarations(needed))
    return lines


def _runtime_declarations(needed: set[str]) -> list[str]:
    lines: list[str] = []
    if "exc" in needed:
        lines.append("declare i32 @ps_try_call(ptr, ptr, ptr)")
        lines.append("declare void @ps_raise(i32) noreturn")
        lines.append("declare void @ps_raise_code(i32) noreturn")
        lines.append("declare void @ps_raise_msg(i32, i64, ptr) noreturn")
        lines.append("declare void @ps_reraise() noreturn")
        lines.append("declare i32 @ps_exc_code()")
        lines.append("declare i32 @ps_exc_isa(i32, i32)")
    if "alloc" in needed:
        lines.append("declare ptr @ps_alloc(i64)")
    if "seq" in needed:
        lines.extend(
            [
                "declare ptr @ps_seq_new(i64, i64)",
                "declare i64 @ps_seq_len(ptr)",
                "declare void @ps_seq_get(ptr, i64, ptr)",
                "declare void @ps_seq_set(ptr, i64, ptr)",
                "declare i32 @ps_seq_has_i64(ptr, i64)",
                "declare i32 @ps_seq_has_f64(ptr, double)",
                "declare i32 @ps_seq_has_buf(ptr, i64, ptr)",
            ]
        )
    if "map" in needed:
        lines.extend(
            [
                "declare ptr @ps_map_new(i32, i64, i64, i64)",
                "declare i64 @ps_map_len(ptr)",
                "declare void @ps_map_put(ptr, ptr, ptr)",
                "declare void @ps_map_get(ptr, ptr, ptr)",
                "declare i32 @ps_map_has(ptr, ptr)",
            ]
        )
    if "str" in needed:
        lines.extend(
            [
                "declare void @ps_str_get(i64, ptr, i64, ptr)",
                "declare i64 @ps_buf_at(i64, ptr, i64)",
                "declare void @ps_str_strip(i64, ptr, ptr)",
                "declare void @ps_str_lstrip(i64, ptr, ptr)",
                "declare void @ps_str_rstrip(i64, ptr, ptr)",
                "declare i32 @ps_str_startswith(i64, ptr, i64, ptr)",
                "declare i32 @ps_str_endswith(i64, ptr, i64, ptr)",
                "declare i64 @ps_str_find(i64, ptr, i64, ptr)",
                "declare i32 @ps_str_cmp(i64, ptr, i64, ptr)",
                "declare void @ps_str_upper(i64, ptr, ptr)",
                "declare void @ps_str_lower(i64, ptr, ptr)",
                "declare void @ps_str_replace(i64, ptr, i64, ptr, i64, ptr, ptr)",
            ]
        )
    if "math" in needed:
        lines.append("declare i64 @ps_float_to_int(double)")
        for spec in MATH_FUNCTIONS.values():
            symbol = spec["symbol"]
            if not symbol:
                continue
            params = ", ".join(["double"] * len(spec["params"]))
            lines.append(f"declare {spec['llvm']} @{symbol}({params})")
    if "fabs" in needed:
        lines.append("declare double @llvm.fabs.f64(double)")
    if "re" in needed:
        lines.extend(
            [
                "declare ptr @ps_re_findall(i64, ptr, i64, ptr)",
                "declare void @ps_re_sub(i64, ptr, i64, ptr, i64, ptr, ptr)",
                "declare ptr @ps_re_split(i64, ptr, i64, ptr)",
                "declare void @ps_re_escape(i64, ptr, ptr)",
            ]
        )
    if "json" in needed:
        lines.extend(
            [
                "declare void @ps_json_loads(i64, ptr, ptr, i32, ptr)",
                "declare void @ps_json_dumps(ptr, i32, ptr, ptr)",
            ]
        )
    if "url" in needed:
        lines.extend(
            [
                "declare void @ps_url_quote(i64, ptr, i64, ptr, ptr)",
                "declare void @ps_url_quote_plus(i64, ptr, i64, ptr, ptr)",
                "declare void @ps_url_unquote(i64, ptr, ptr)",
                "declare void @ps_url_unquote_plus(i64, ptr, ptr)",
                "declare void @ps_url_encode(ptr, ptr)",
                "declare ptr @ps_url_parse_qs(i64, ptr)",
                "declare void @ps_url_split(i64, ptr, ptr)",
            ]
        )
    if "b64" in needed:
        lines.extend(
            [
                "declare void @ps_b64_std_encode(i64, ptr, ptr)",
                "declare void @ps_b64_std_decode(i64, ptr, ptr)",
                "declare void @ps_b64_url_encode(i64, ptr, ptr)",
                "declare void @ps_b64_url_decode(i64, ptr, ptr)",
            ]
        )
    if "hash" in needed:
        lines.extend(
            [
                "declare ptr @ps_hash_md5(i64, ptr)",
                "declare ptr @ps_hash_sha1(i64, ptr)",
                "declare ptr @ps_hash_sha256(i64, ptr)",
                "declare ptr @ps_hash_sha384(i64, ptr)",
                "declare ptr @ps_hash_sha512(i64, ptr)",
                "declare void @ps_hash_hex(ptr, ptr)",
                "declare void @ps_hash_digest(ptr, ptr)",
            ]
        )
    if "struct" in needed:
        lines.extend(
            [
                "declare void @ps_struct_pack(i64, ptr, ptr, i64, ptr)",
                "declare ptr @ps_struct_unpack(i64, ptr, i64, ptr, i32)",
            ]
        )
    return lines


def _helpers(needed: set[str]) -> list[str]:
    chunks: list[str] = []
    if "iadd" in needed:
        chunks.append(_overflow_helper("iadd", "sadd"))
    if "isub" in needed:
        chunks.append(_overflow_helper("isub", "ssub"))
    if "imul" in needed:
        chunks.append(_overflow_helper("imul", "smul"))
    if "ineg" in needed:
        chunks.append(
            """define internal i64 @ps.ineg(i64 %a) {
entry:
  %r = call { i64, i1 } @llvm.ssub.with.overflow.i64(i64 0, i64 %a)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @llvm.trap()
  unreachable
}
"""
        )
    if "floordiv" in needed:
        chunks.append(
            """define internal i64 @ps.floordiv(i64 %a, i64 %b) {
entry:
  %zero = icmp eq i64 %b, 0
  br i1 %zero, label %trap, label %nonzero
nonzero:
  %min = icmp eq i64 %a, -9223372036854775808
  %neg1 = icmp eq i64 %b, -1
  %bad = and i1 %min, %neg1
  br i1 %bad, label %trap, label %divide
divide:
  %q = sdiv i64 %a, %b
  %r = srem i64 %a, %b
  %rnz = icmp ne i64 %r, 0
  %aneg = icmp slt i64 %a, 0
  %bneg = icmp slt i64 %b, 0
  %diff = icmp ne i1 %aneg, %bneg
  %adjust = and i1 %rnz, %diff
  br i1 %adjust, label %dec, label %keep
dec:
  %q2 = sub i64 %q, 1
  ret i64 %q2
keep:
  ret i64 %q
trap:
  call void @llvm.trap()
  unreachable
}
"""
        )
    if "strconcat" in needed:
        chunks.append(
            """define internal %ps.str @ps.strconcat(%ps.str %a, %ps.str %b) {
entry:
  %na = extractvalue %ps.str %a, 0
  %pa = extractvalue %ps.str %a, 1
  %nb = extractvalue %ps.str %b, 0
  %pb = extractvalue %ps.str %b, 1
  %sum = call { i64, i1 } @llvm.sadd.with.overflow.i64(i64 %na, i64 %nb)
  %ov = extractvalue { i64, i1 } %sum, 1
  br i1 %ov, label %trap, label %sized
sized:
  %n = extractvalue { i64, i1 } %sum, 0
  %atmax = icmp eq i64 %n, 9223372036854775807
  br i1 %atmax, label %trap, label %alloc
alloc:
  %n1 = add i64 %n, 1
  %mem = call ptr @malloc(i64 %n1)
  %null = icmp eq ptr %mem, null
  br i1 %null, label %trap, label %copy
copy:
  call void @llvm.memcpy.p0.p0.i64(ptr %mem, ptr %pa, i64 %na, i1 false)
  %dst2 = getelementptr i8, ptr %mem, i64 %na
  call void @llvm.memcpy.p0.p0.i64(ptr %dst2, ptr %pb, i64 %nb, i1 false)
  %end = getelementptr i8, ptr %mem, i64 %n
  store i8 0, ptr %end
  %s0 = insertvalue %ps.str poison, i64 %n, 0
  %s1 = insertvalue %ps.str %s0, ptr %mem, 1
  ret %ps.str %s1
trap:
  call void @llvm.trap()
  unreachable
}
"""
        )
    return chunks


def _overflow_helper(name: str, intrinsic: str) -> str:
    return f"""define internal i64 @ps.{name}(i64 %a, i64 %b) {{
entry:
  %r = call {{ i64, i1 }} @llvm.{intrinsic}.with.overflow.i64(i64 %a, i64 %b)
  %ov = extractvalue {{ i64, i1 }} %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue {{ i64, i1 }} %r, 0
  ret i64 %v
trap:
  call void @llvm.trap()
  unreachable
}}
"""
