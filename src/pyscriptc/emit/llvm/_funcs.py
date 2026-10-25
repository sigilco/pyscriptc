"""Function and entry-point emission: the function prologue, the locals
frame, the `main` entry and the exception registrations.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _elem_size, _fn_symbol, _ir_const_value, _llvm_type, _memory_type, _prepare_arg, _safe
from pyscriptc.errors import FitError, IRError


class FuncsMixin:
    def emit_function(self, fn: dict[str, Any]) -> None:
        ret = _llvm_type(fn["returnType"])
        # Env-first functions (nested defs and lambdas) take the caller's env
        # cell array as a leading `ptr %env` and bind those slots by pointer.
        plist = [f"{_llvm_type(p['type'])} %{_safe(p['name'])}" for p in fn["params"]]
        if "env" in fn:
            plist.insert(0, "ptr %env")
        symbol = _fn_symbol(fn["name"])
        # In library mode only pyscriptc_main is exported; every other
        # definition is hidden, so an app link does not inherit the whole
        # module namespace. In binding mode, definition symbols remain visible.
        visibility = "hidden " if (self.library and not self.binding) else ""
        define = f"define {visibility}{ret} @{symbol}({', '.join(plist)})"
        if self.dwarf is not None:
            self.dwarf.enter_function(fn["name"], symbol, fn["span"], fn["params"], fn["returnType"], env="env" in fn)
            define += f" !dbg !{self.dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        self._declare_params(fn)
        self.slot_order = []
        self.slot_types = {}
        boxed = set(fn.get("boxed") or [])
        for local in fn["locals"]:
            if local["type"]["kind"] == "none":
                # None is not a runtime value. The local is the singleton, so it has no slot.
                self.slots[local["name"]] = ("", local["type"])
                continue
            if local["name"] in boxed:
                # Captured by a nested callable: the env stores a pointer to
                # this storage, so it must outlive the frame.
                self.needed.add("alloc")
                ptr = self._call_ret("ptr", "ps_alloc", [f"i64 {_elem_size(local['type'])}"])
            else:
                ptr = self.tmp()
                self.line(f"{ptr} = alloca {_memory_type(local['type'])}")
                # boxed:begin
                if local["type"].get("island") or local["type"].get("handle"):
                    self.line(f"store ptr null, ptr {ptr}")
                # boxed:end
            self.slots[local["name"]] = (ptr, local["type"])
            self.slot_order.append(local["name"])
            self.slot_types[local["name"]] = local["type"]
        for index, item in enumerate(fn.get("env", [])):
            ptr = self._load_env_ptr("%env", index)
            self.slots[item["name"]] = (ptr, item["type"])
            self.slot_order.append(item["name"])
            self.slot_types[item["name"]] = item["type"]
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
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        if self.dwarf is not None:
            self.dwarf.exit_function()

    def emit_main(self, name: str, args: list[Any], library: bool = False) -> None:
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
        if library:
            # The library entry: no argv, an explicit runtime init call, the
            # exception table, the module globals, then the entry function.
            # The return value is not printed: a library has no console.
            define = "define i32 @pyscriptc_main()"
            if self.dwarf is not None:
                entry_span = {"file": self.module["sourceFile"], "line": 1}
                self.dwarf.enter_function("pyscriptc_main", "pyscriptc_main", entry_span, [], {"kind": "none"})
                define += f" !dbg !{self.dwarf.current_subprogram}"
            self.out.append(f"{define} {{")
            self._reset_function()
            self.block("entry")
            self.span = fn.get("span") if self.dwarf is not None else None
            self._abi_decl("pyscriptc_init", "i32", [])
            self._call_void("pyscriptc_init", [])
            self._emit_exc_registrations()
            # boxed:begin
            self._emit_island_seed_global()
            # boxed:end
            for init in self.module.get("globals", []):
                self.emit_expr(init)
            values = [self.materialize_arg(param["type"], value) for param, value in zip(params, prepared, strict=True)]
            self.call_function(fn, values)
            self.line("ret i32 0")
            self.out.append("}")
            self.out.append("")
            self.span = None
            if self.dwarf is not None:
                self.dwarf.exit_function()
            return
        define = f"define i32 @{self.entry_symbol}(i32 %argc, ptr %argv)"
        if self.dwarf is not None:
            # The process entry point is not a Python function, so it gets a
            # synthetic subprogram named `main` at the source file's line 1.
            entry_span = {"file": self.module["sourceFile"], "line": 1}
            self.dwarf.enter_function("main", "main", entry_span, [], {"kind": "none"})
            define += f" !dbg !{self.dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        # The entry point's own instructions point at the entry function's
        # definition, so a frame in `main` names the Python line that started
        # the program rather than nothing at all.
        self.span = fn.get("span") if self.dwarf is not None else None
        self._abi_decl("ps_argv_init", "void", ["i32", "ptr"])
        self._call_void("ps_argv_init", ["i32 %argc", "ptr %argv"])
        self._emit_exc_registrations()
        # boxed:begin
        self._emit_island_seed_global()
        # boxed:end
        for init in self.module.get("globals", []):
            self.emit_expr(init)
        values = [self.materialize_arg(param["type"], value) for param, value in zip(params, prepared, strict=True)]
        result = self.call_function(fn, values)
        if fn["returnType"]["kind"] != "none":
            self.print_value(fn["returnType"], result)
        self.line("ret i32 0")
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        self.span = None
        if self.dwarf is not None:
            self.dwarf.exit_function()

    def emit_noop_library_main(self) -> None:
        """The `pyscriptc_main` of a definition-only module.

        The host still gets its entry: runtime init, the exception table and
        the globals run, then it returns 0. Keeping the symbol unconditional
        means the ABI does not depend on the script having a body.
        """
        define = "define i32 @pyscriptc_main()"
        if self.dwarf is not None:
            entry_span = {"file": self.module["sourceFile"], "line": 1}
            self.dwarf.enter_function("pyscriptc_main", "pyscriptc_main", entry_span, [], {"kind": "none"})
            define += f" !dbg !{self.dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        self._abi_decl("pyscriptc_init", "i32", [])
        self._call_void("pyscriptc_init", [])
        self._emit_exc_registrations()
        for init in self.module.get("globals", []):
            self.emit_expr(init)
        self.line("ret i32 0")
        self.out.append("}")
        self.out.append("")
        if self.dwarf is not None:
            self.dwarf.exit_function()

    # boxed:begin
    def emit_top_island(self) -> str:
        """The whole-program main of an island top level (issue #613).

        The binary carries no native user code: `main` initializes the
        runtime and the exception table, unboxes the annotated scalar
        globals after the seed, and hands the process to the island. The
        seed runs the whole module body with `__name__ = "__main__"`, so the
        script's own main guard fires island-side and `raise SystemExit(...)`
        delivers the process status through `ps_island_fail`. On `--run` the
        program directory joins `sys.path` before the seed, which
        `ps_island_init` already does from `ps_argv_get`.
        """
        if self.dwarf is not None:
            entry_span = {"file": self.module["sourceFile"], "line": 1}
            self.dwarf.enter_function("main", "main", entry_span, [], {"kind": "none"})
        self.out.append(f"define i32 @{self.entry_symbol}(i32 %argc, ptr %argv) {{")
        self._reset_function()
        self.block("entry")
        self._abi_decl("ps_argv_init", "void", ["i32", "ptr"])
        # `pyscriptc_init` seeds an empty argv (the library-mode default);
        # the process argv overwrites it after, so the island top level sees
        # the real one (`len(sys.argv) != 2` and friends behave as under
        # CPython).
        self._abi_decl("pyscriptc_init", "i32", [])
        self._call_void("pyscriptc_init", [])
        self._call_void("ps_argv_init", ["i32 %argc", "ptr %argv"])
        self._emit_exc_registrations()
        self._emit_island_top_seed()
        self._emit_island_top_globals()
        self.line("ret i32 0")
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        if self.dwarf is not None:
            self.dwarf.exit_function()
        return self._render()

    def _emit_island_top_seed(self) -> None:
        """The once-per-binary top-level seed: the module's own source text."""

        source = self.module.get("islandSource") or ""
        if not source:
            raise IRError("an island top level carries no islandSource")
        data = source.encode("utf-8")
        size = len(data)
        symbol = self.format_global("island.seed", data)
        path = self.module.get("sourceFile") or ""
        path_data = path.encode("utf-8")
        path_symbol = self.format_global("island.seed.path", path_data) if path_data else None
        self._abi_decl("ps_island_top_seed", "void", ["i64", "ptr", "i64", "ptr"])
        self._call_void(
            "ps_island_top_seed",
            [
                f"i64 {size}",
                f"ptr {symbol}",
                f"i64 {len(path_data)}",
                f"ptr {path_symbol}" if path_symbol else "ptr null",
            ],
        )

    def _emit_island_top_globals(self) -> None:
        """One unbox call per annotated scalar global, in IR order."""

        globals_map = self.module.get("islandGlobals") or {}
        if not globals_map:
            return
        self._ensure_arg_type()
        for name, spec in globals_map.items():
            want, _, pad = str(spec).partition(":")
            data = name.encode("utf-8")
            symbol = self.format_global(f"island.global.{name}", data)
            self._abi_decl("ps_island_top_global", "void", ["i64", "ptr", "i32", "i32"])
            self._call_void(
                "ps_island_top_global",
                [f"i64 {len(data)}", f"ptr {symbol}", f"i32 {want}", f"i32 {pad or 0}"],
            )

    # boxed:end

    def _emit_exc_registrations(self) -> None:
        """Register the module's user exception classes with the runtime.

        The runtime keeps the parent chain, so a user class matches
        `except Exception` and `except <its builtin base>` the same way a
        builtin does. One call per class, before any user code runs.
        """

        classes = [cls for cls in self.module.get("classes", []) if cls.get("exception")]
        if not classes:
            return
        self.needed.add("exc")
        self._abi_decl("ps_exc_register", "void", ["i32", "i32", "ptr"])
        for cls in classes:
            value = self.string_value(cls["name"])
            _, ptr = self._buf_parts(value)
            self._call_void(
                "ps_exc_register",
                [f"i32 {cls['exc_code']}", f"i32 {cls.get('exc_parent', 0)}", f"ptr {ptr}"],
            )

    def _declare_params(self, fn: dict[str, Any]) -> None:
        """`llvm.dbg.value` for each parameter, at its IR name.

        Parameters are SSA values, not stack slots, so a parameter needs
        `dbg.value` rather than `dbg.declare` (and a formal-parameter DIE with
        its 1-based `arg:` index).
        """
        if self.dwarf is None:
            return
        self.needed.add("dbg")
        for index, param in enumerate(fn["params"], start=1):
            if param["type"]["kind"] == "none":
                continue
            var = self.dwarf.variable(param["name"], fn["span"], param["type"]["kind"], index)
            loc = self.dwarf.location(fn["span"])
            value = f"{_llvm_type(param['type'])} %{_safe(param['name'])}"
            self.out.append(
                f"  call void @llvm.dbg.value(metadata {value}, metadata !{var}, metadata !DIExpression()), !dbg !{loc}"
            )

    def emit_body(self, body: list[dict[str, Any]]) -> None:
        for stmt in body:
            if self.terminated:
                break
            saved = self.span
            self.span = stmt.get("span") if self.dwarf is not None else None
            self.emit_stmt(stmt)
            self.span = saved

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
            # Flow 1 is a return out of the try body; flow 2 is a suspension
            # (a yield inside the try). The try callsite tells them apart: a
            # suspension skips the finally, a return runs it.
            self.line(f"ret i32 {2 if stmt.get('suspend') else 1}")
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

    def _emit_propagated_suspend(self) -> None:
        """Propagate a suspension out of a try body.

        Inside an outline the flow code says what happened: 1 is a return, 2 a
        suspension. The value travels in the return slot either way, so the
        two differ only in the flow code the try callsite reads.
        """
        if self.outline_depth:
            self.line("ret i32 2")
        elif self.ret_llvm is None:
            self.line("ret void")
        else:
            if self.ret_ptr is None:
                raise IRError("suspend inside try has no slot")
            value = self._load(self.ret_llvm, self.ret_ptr)
            self.line(f"ret {self.ret_llvm} {value}")
        self.terminated = True
