"""Function and entry-point emission: the function prologue, the locals
frame, the `main` entry and the exception registrations.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import (
    _const_bool,
    _const_text,
    _elem_size,
    _entry_span,
    _flag_of,
    _fn_symbol,
    _has,
    _ir_const_value,
    _items,
    _kind,
    _llvm_type,
    _memory_type,
    _name_list,
    _node_kind,
    _node_value_node,
    _none_type,
    _opt_default,
    _opt_int,
    _opt_span,
    _opt_str,
    _opt_str_or_none,
    _prepare_arg,
    _return_type,
    _safe,
    _span_of,
    _str_map,
    _type_of,
)
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import FitError, IRError


class FuncsMixin(EmitterBase):
    def emit_function(self, fn: dict[str, Any]) -> None:
        return_type: dict[str, Any] = _return_type(fn)
        ret: str = _llvm_type(return_type)
        # Env-first functions (nested defs and lambdas) take the caller's env
        # cell array as a leading `ptr %env` and bind those slots by pointer.
        params: list[dict[str, Any]] = _items(fn, "params")
        plist: list[str] = []
        for p in params:
            ptype: dict[str, Any] = _type_of(p)
            pname: str = _opt_str(p, "name")
            plist.append(f"{_llvm_type(ptype)} %{_safe(pname)}")
        if _has(fn, "env"):
            plist.insert(0, "ptr %env")
        fn_name: str = _opt_str(fn, "name")
        symbol: str = _fn_symbol(fn_name)
        # In library mode only pyscriptc_main is exported; every other
        # definition is hidden, so an app link does not inherit the whole
        # module namespace. In binding mode, definition symbols remain visible.
        visibility: str = "hidden " if (self.library and not self.binding) else ""
        define: str = f"define {visibility}{ret} @{symbol}({', '.join(plist)})"
        dwarf = self.dwarf
        if dwarf is not None:
            fn_span: dict[str, Any] = _span_of(fn)
            dwarf.enter_function(fn_name, symbol, fn_span, params, return_type, env=_has(fn, "env"))
            define += f" !dbg !{dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        self._declare_params(fn)
        self.slot_order = []
        self.slot_types = {}
        boxed: set[str] = set(_name_list(fn, "boxed"))
        for local in _items(fn, "locals"):
            local_name: str = _opt_str(local, "name")
            local_type: dict[str, Any] = _type_of(local)
            if _kind(local_type) == "none":
                # None is not a runtime value. The local is the singleton, so it has no slot.
                self.slots[local_name] = ("", local_type)
                continue
            if local_name in boxed:
                # Captured by a nested callable: the env stores a pointer to
                # this storage, so it must outlive the frame.
                self.needed.add("alloc")
                ptr: str = self._call_ret("ptr", "ps_alloc", [f"i64 {_elem_size(local_type)}"])
            else:
                ptr = self.tmp()
                self.line(f"{ptr} = alloca {_memory_type(local_type)}")
                # boxed:begin
                if _flag_of(local_type, "island") or _flag_of(local_type, "handle"):
                    self.line(f"store ptr null, ptr {ptr}")
                # boxed:end
            self.slots[local_name] = (ptr, local_type)
            self.slot_order.append(local_name)
            self.slot_types[local_name] = local_type
        env_items: list[dict[str, Any]] = _items(fn, "env")
        for index in range(len(env_items)):
            item: dict[str, Any] = env_items[index]
            ptr = self._load_env_ptr("%env", index)
            item_name: str = _opt_str(item, "name")
            item_type: dict[str, Any] = _type_of(item)
            self.slots[item_name] = (ptr, item_type)
            self.slot_order.append(item_name)
            self.slot_types[item_name] = item_type
        self.ret_llvm = None if _kind(return_type) == "none" else _llvm_type(return_type)
        self.ret_ptr = None if self.ret_llvm is None else self._alloca(self.ret_llvm)
        self.flow_ptr = self._alloca("i32")
        for param in params:
            param_name: str = _opt_str(param, "name")
            self.store(param_name, f"%{_safe(param_name)}")
        self.emit_body(_items(fn, "body"))
        if not self.terminated:
            if _kind(return_type) == "none":
                self.line("ret void")
            else:
                self.line("unreachable")
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        if dwarf is not None:
            dwarf.exit_function()

    def emit_main(self, name: str, args: list[Any], library: bool = False) -> None:
        functions: dict[str, dict[str, Any]] = self.functions
        fn: dict[str, Any] | None = functions.get(name)
        if fn is None:
            raise IRError(f"no function {name} to call")
        params: list[dict[str, Any]] = _items(fn, "params")
        if len(args) > len(params):
            raise FitError(f"{name} expects {len(params)} argument(s), got {len(args)}")
        filled: list[Any] = list(args)
        for param in params[len(args) :]:
            default: dict[str, Any] | None = _opt_default(param)
            if default is None or _kind(default) != "const":
                raise FitError(f"{name} expects {len(params)} argument(s), got {len(args)}")
            default_type: dict[str, Any] = _type_of(default)
            if _kind(default_type) == "bool":
                filled.append(_ir_const_value(_type_of(param), _const_bool(default)))
            else:
                filled.append(_ir_const_value(_type_of(param), _const_text(default)))
        prepared: list[Any] = []
        for index in range(len(params)):
            param: dict[str, Any] = params[index]
            value: Any = filled[index]
            prepared.append(_prepare_arg(param, value))
        dwarf = self.dwarf
        module: dict[str, Any] = self.module
        if library:
            # The library entry: no argv, an explicit runtime init call, the
            # exception table, the module globals, then the entry function.
            # The return value is not printed: a library has no console.
            define: str = "define i32 @pyscriptc_main()"
            if dwarf is not None:
                entry_span: dict[str, Any] = _entry_span(module)
                dwarf.enter_function("pyscriptc_main", "pyscriptc_main", entry_span, [], _none_type())
                define += f" !dbg !{dwarf.current_subprogram}"
            self.out.append(f"{define} {{")
            self._reset_function()
            self.block("entry")
            fn_span: dict[str, Any] | None = _opt_span(fn)
            self.span = fn_span if dwarf is not None else None
            self._abi_decl("pyscriptc_init", "i32", [])
            self._call_void("pyscriptc_init", [])
            self._emit_exc_registrations()
            # boxed:begin
            self._emit_island_seed_global()
            # boxed:end
            for init in _items(module, "globals"):
                self.emit_expr(init)
            self._emit_module_inits()
            values: list[str] = []
            for index in range(len(params)):
                param = params[index]
                value = prepared[index]
                values.append(self.materialize_arg(_type_of(param), value))
            self.call_function(fn, values)
            self.line("ret i32 0")
            self.out.append("}")
            self.out.append("")
            self.span = None
            if dwarf is not None:
                dwarf.exit_function()
            return
        define = f"define i32 @{self.entry_symbol}(i32 %argc, ptr %argv)"
        if dwarf is not None:
            # The process entry point is not a Python function, so it gets a
            # synthetic subprogram named `main` at the source file's line 1.
            entry_span = _entry_span(module)
            dwarf.enter_function("main", "main", entry_span, [], _none_type())
            define += f" !dbg !{dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        # The entry point's own instructions point at the entry function's
        # definition, so a frame in `main` names the Python line that started
        # the program rather than nothing at all.
        fn_span = _opt_span(fn)
        self.span = fn_span if dwarf is not None else None
        self._abi_decl("ps_argv_init", "void", ["i32", "ptr"])
        self._call_void("ps_argv_init", ["i32 %argc", "ptr %argv"])
        self._emit_exc_registrations()
        # boxed:begin
        self._emit_island_seed_global()
        # boxed:end
        for init in _items(module, "globals"):
            self.emit_expr(init)
        self._emit_module_inits()
        values = []
        for index in range(len(params)):
            param = params[index]
            value = prepared[index]
            values.append(self.materialize_arg(_type_of(param), value))
        result: str = self.call_function(fn, values)
        return_type: dict[str, Any] = _return_type(fn)
        if _kind(return_type) != "none":
            self.print_value(return_type, result)
        self.line("ret i32 0")
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        self.span = None
        if dwarf is not None:
            dwarf.exit_function()

    def _emit_module_inits(self) -> None:
        """The module init calls a `--call` entry skips.

        The program entry carries its own injected init calls at the import
        sites. A called function bypasses the entry, so the module bodies
        that bind global slots run here first, in the import order the
        merge recorded.
        """

        for name in self.module.get("moduleInits", []):
            fn = self.functions.get(name)
            if fn is None:
                continue
            self.call_function(fn, [])

    def emit_noop_library_main(self) -> None:
        """The `pyscriptc_main` of a definition-only module.

        The host still gets its entry: runtime init, the exception table and
        the globals run, then it returns 0. Keeping the symbol unconditional
        means the ABI does not depend on the script having a body.
        """
        define = "define i32 @pyscriptc_main()"
        dwarf = self.dwarf
        module: dict[str, Any] = self.module
        if dwarf is not None:
            entry_span: dict[str, Any] = _entry_span(module)
            dwarf.enter_function("pyscriptc_main", "pyscriptc_main", entry_span, [], _none_type())
            define += f" !dbg !{dwarf.current_subprogram}"
        self.out.append(f"{define} {{")
        self._reset_function()
        self.block("entry")
        self._abi_decl("pyscriptc_init", "i32", [])
        self._call_void("pyscriptc_init", [])
        self._emit_exc_registrations()
        for init in _items(module, "globals"):
            self.emit_expr(init)
        self.line("ret i32 0")
        self.out.append("}")
        self.out.append("")
        if dwarf is not None:
            dwarf.exit_function()

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
        dwarf = self.dwarf
        module: dict[str, Any] = self.module
        if dwarf is not None:
            entry_span: dict[str, Any] = _entry_span(module)
            dwarf.enter_function("main", "main", entry_span, [], _none_type())
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
        """The once-per-binary top-level seed: the module's own source text.

        Issue #665: the bootstrap rebases the origins and the entry
        `__file__` onto the staged data tree at run time.

        Issue #712: a program-wide island carries its module bodies as
        marshalled bytecode in two blobs beside the bootstrap. The runtime
        hands them to the bootstrap as the `__pyscriptc_seed__` module, and
        the bootstrap's finder inflates one module body per import.
        """

        source: str = _opt_str(self.module, "islandSource")
        if not source:
            raise IRError("an island top level carries no islandSource")
        data: bytes = source.encode("utf-8")
        size: int = len(data)
        symbol: str = self.format_global("island.seed", data)
        path: str = _opt_str(self.module, "sourceFile")
        path_data: bytes = path.encode("utf-8")
        path_symbol: str | None = self.format_global("island.seed.path", path_data) if path_data else None
        index_b64: str | None = _opt_str_or_none(self.module, "islandSeedIndex")
        body_b64: str | None = _opt_str_or_none(self.module, "islandSeedBody")
        if index_b64 is None or body_b64 is None:
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
            return
        from base64 import b64decode

        index_blob: bytes = b64decode(index_b64)
        body_blob: bytes = b64decode(body_b64)
        index_symbol: str = self.format_global("island.seed.index", index_blob)
        body_symbol: str = self.format_global("island.seed.body", body_blob)
        self._abi_decl("ps_island_top_seed_payload", "void", ["i64", "ptr", "i64", "ptr", "i64", "ptr", "i64", "ptr"])
        self._call_void(
            "ps_island_top_seed_payload",
            [
                f"i64 {size}",
                f"ptr {symbol}",
                f"i64 {len(path_data)}",
                f"ptr {path_symbol}" if path_symbol else "ptr null",
                f"i64 {len(index_blob)}",
                f"ptr {index_symbol}",
                f"i64 {len(body_blob)}",
                f"ptr {body_symbol}",
            ],
        )

    def _emit_island_top_globals(self) -> None:
        """One unbox call per annotated scalar global, in IR order."""

        globals_map: dict[str, str] = _str_map(self.module, "islandGlobals")
        if not globals_map:
            return
        self._ensure_arg_type()
        for name, spec in globals_map.items():
            want, _, pad = str(spec).partition(":")
            data: bytes = name.encode("utf-8")
            symbol: str = self.format_global(f"island.global.{name}", data)
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

        classes: list[dict[str, Any]] = []
        for cls in _items(self.module, "classes"):
            if _flag_of(cls, "exception"):
                classes.append(cls)
        if not classes:
            return
        self.needed.add("exc")
        self._abi_decl("ps_exc_register", "void", ["i32", "i32", "ptr"])
        for cls in classes:
            value: str = self.string_value(_opt_str(cls, "name"))
            _, ptr = self._buf_parts(value)
            self._call_void(
                "ps_exc_register",
                [
                    f"i32 {_opt_int(cls, 'exc_code', 0)}",
                    f"i32 {_opt_int(cls, 'exc_parent', 0)}",
                    f"ptr {ptr}",
                ],
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
        dwarf = self.dwarf
        fn_span: dict[str, Any] = _span_of(fn)
        params: list[dict[str, Any]] = _items(fn, "params")
        for index in range(len(params)):
            param: dict[str, Any] = params[index]
            param_type: dict[str, Any] = _type_of(param)
            param_kind: str = _kind(param_type)
            if param_kind == "none":
                continue
            param_name: str = _opt_str(param, "name")
            var: int = dwarf.variable(param_name, fn_span, param_kind, index + 1)
            loc: int = dwarf.location(fn_span)
            value: str = f"{_llvm_type(param_type)} %{_safe(param_name)}"
            self.out.append(
                f"  call void @llvm.dbg.value(metadata {value}, metadata !{var}, metadata !DIExpression()), !dbg !{loc}"
            )

    def emit_body(self, body: list[dict[str, Any]]) -> None:
        dwarf = self.dwarf
        for stmt in body:
            if self.terminated:
                break
            saved: dict[str, Any] | None = self.span
            stmt_span: dict[str, Any] | None = _opt_span(stmt)
            self.span = stmt_span if dwarf is not None else None
            self.emit_stmt(stmt)
            self.span = saved

    def _emit_return(self, stmt: dict[str, Any]) -> None:
        expr: dict[str, Any] = _node_value_node(stmt)
        expr_type: dict[str, Any] = _type_of(expr)
        kind: str = _kind(expr_type)
        if self.outline_depth:
            if kind != "none":
                value: str = self.emit_expr(expr)
                if self.ret_ptr is None or self.ret_llvm is None:
                    raise IRError("return inside try has no slot")
                self.line(f"store {self.ret_llvm} {value}, ptr {self.ret_ptr}")
            elif _node_kind(expr) != "const":
                self.emit_expr(expr)
            if self.terminated:
                return
            # Flow 1 is a return out of the try body; flow 2 is a suspension
            # (a yield inside the try). The try callsite tells them apart: a
            # suspension skips the finally, a return runs it.
            self.line(f"ret i32 {2 if _flag_of(stmt, 'suspend') else 1}")
        elif kind == "none":
            if _node_kind(expr) != "const":
                self.emit_expr(expr)
            if self.terminated:
                return
            self.line("ret void")
        else:
            value = self.emit_expr(expr)
            self.line(f"ret {_llvm_type(expr_type)} {value}")
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
