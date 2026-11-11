"""Call emission: builtins, module calls, method calls and constructors."""

from __future__ import annotations

from typing import Any

from pyscriptc.boxed import DEF_HANDLE_SYMBOL, INVOKE_SYMBOL, MOD_DEF_HANDLE_SYMBOL
from pyscriptc.emit._ir import (
    _arg_type,
    _callee_kind,
    _callee_module,
    _callee_name,
    _callee_receiver,
    _callee_tuple,
    _elem_of,
    _elem_size,
    _mark_fmt,
    _memory_type,
    _node_args,
    _node_callee,
    _node_symbol,
    _opt_int,
    _opt_str_or_none,
    _type_of,
)
from pyscriptc.emit._ir import (
    _has as _ir_has,
)

# boxed:begin
from pyscriptc.emit.llvm._base import EmitterBase

# boxed:end
from pyscriptc.errors import IRError
from pyscriptc.tirserde import dict_to_type


def _has(expr: dict[str, Any], key: str) -> bool:
    """Whether an expression record carries a key at all."""
    return _ir_has(expr, key)


class CallsMixin(EmitterBase):
    def emit_call(self, expr: dict[str, Any]) -> str:
        callee: dict[str, Any] = _node_callee(expr)
        args: list[dict[str, Any]] = _node_args(expr)
        values: list[str] = [self.emit_expr(arg) for arg in args]
        kind: str = _callee_kind(callee)
        if kind == "builtin":
            arg_types: list[dict[str, Any]] = [_arg_type(arg) for arg in args]
            return self.emit_builtin(_callee_name(callee), values, arg_types, expr)
        if kind == "module":
            return self.emit_module_call(expr, values)
        if kind == "method":
            return self.emit_method(expr, values)
        if kind == "class":
            return self.emit_constructor(_callee_name(callee), values)
        if kind == "value":
            return self.emit_value_call(expr, values)
        fn: dict[str, Any] | None = self.functions.get(_callee_name(callee))
        if fn is None:
            # Refs #641: the defining module planned the callee for the
            # island and the merged module emits no body for it. A crash
            # here is a planning hole; name the callee instead.
            raise IRError(
                f"call to {_callee_name(callee)} has no lowered body (its module planned it for the island floor)"
            )
        return self.call_function(fn, values)

    def emit_builtin(self, name: str, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> str:
        if name == "print":
            self._emit_print(values, arg_types, expr)
            return ""
        # The kind of the first argument, read only for the builtins that
        # dispatch on it.
        at0: dict[str, Any] = arg_types[0]
        arg_kind: str = dict_to_type(at0).kind
        if name == "len" and arg_kind == "str":
            # The code-point count: field 2 (field 0 is the byte length).
            reg = self.tmp()
            self.line(f"{reg} = extractvalue %ps.str {values[0]}, 2")
            return reg
        if name == "len" and arg_kind == "bytes":
            reg = self.tmp()
            self.line(f"{reg} = extractvalue %ps.str {values[0]}, 0")
            return reg
        if name == "len" and arg_kind == "list":
            needed: set[str] = self.needed
            needed.add("seq")
            return self._call_ret("i64", "ps_seq_len", [f"ptr {values[0]}"])
        if name == "len" and arg_kind == "dict":
            needed = self.needed
            needed.add("map")
            return self._call_ret("i64", "ps_map_len", [f"ptr {values[0]}"])
        if name == "len" and arg_kind == "class" and dict_to_type(at0).name == "Deque":
            self._abi_decl("ps_deque_len", "i64", ["ptr"])
            return self._call_ret("i64", "ps_deque_len", [f"ptr {values[0]}"])
        if name == "id":
            # CPython's id() is the object's address; same here for heap values.
            # str/bytes carry their buffer in field 1 — the stable identity.
            reg = self.tmp()
            if arg_kind in {"str", "bytes"}:
                ptr = self.tmp()
                self.line(f"{ptr} = extractvalue %ps.str {values[0]}, 1")
                self.line(f"{reg} = ptrtoint ptr {ptr} to i64")
                return reg
            self.line(f"{reg} = ptrtoint ptr {values[0]} to i64")
            return reg
        if name in {"min", "max"}:
            return self._emit_minmax(name, values, arg_types)
        if name == "int":
            if arg_kind in {"bytes", "str"}:
                self._abi_decl("ps_int_from_bytes", "i64", ["i64", "ptr"])
                parts1: tuple[str, str] = self._buf_parts(values[0])
                length: str = parts1[0]
                ptr: str = parts1[1]
                return self._call_ret("i64", "ps_int_from_bytes", [f"i64 {length}", f"ptr {ptr}"])
            needed = self.needed
            needed.add("math")
            return self._call_ret("i64", "ps_float_to_int", [f"double {values[0]}"])
        if name == "float" and arg_kind == "str":
            self._abi_decl("ps_float_from_str", "double", ["i64", "ptr"])
            parts2: tuple[str, str] = self._buf_parts(values[0])
            length: str = parts2[0]
            ptr: str = parts2[1]
            return self._call_ret("double", "ps_float_from_str", [f"i64 {length}", f"ptr {ptr}"])
        if name == "str":
            kind: str = arg_kind
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
                # "True"/"False" are ASCII: the count is the byte count and
                # the flag is set.
                agg = self.tmp()
                p1 = self.tmp()
                p2 = self.tmp()
                ret = self.tmp()
                self.line(f"{agg} = insertvalue %ps.str poison, i64 {t_len}, 0")
                self.line(f"{p1} = insertvalue %ps.str {agg}, ptr {t_ptr}, 1")
                self.line(f"{p2} = insertvalue %ps.str {p1}, i64 {t_len}, 2")
                self.line(f"{ret} = insertvalue %ps.str {p2}, i32 1, 3")
                return ret
            if kind == "bytes":
                self._abi_decl("ps_repr_bytes", "void", ["i64", "ptr", "ptr"])
                parts3: tuple[str, str] = self._buf_parts(values[0])
                length: str = parts3[0]
                ptr: str = parts3[1]
                return self._buf_out("ps_repr_bytes", [f"i64 {length}", f"ptr {ptr}"])
            if kind == "exc":
                self._abi_decl("ps_exc_str", "void", ["ptr", "ptr"])
                return self._buf_out("ps_exc_str", [f"ptr {values[0]}"])
            if kind == "class" and dict_to_type(at0).name == "Path":
                self._abi_decl("ps_path_posix", "void", ["ptr", "ptr"])
                return self._buf_out("ps_path_posix", [f"ptr {values[0]}"])
        raise IRError(f"no LLVM lowering for builtin {name}")

    def emit_module_call(self, expr: dict[str, Any], values: list[str]) -> str:
        callee: dict[str, Any] = _node_callee(expr)
        module: str = _callee_module(callee)
        name: str = _callee_name(callee)
        args: list[dict[str, Any]] = _node_args(expr)
        typ: dict[str, Any] = _type_of(expr)
        if _has(expr, "fn"):
            return self._emit_reduce(expr)
        if name == "exit":
            if _has(expr, "exitMessage"):
                return self._emit_exit_message(values[0])
            return self._emit_exit(values[0])
        if module == "atexit" and name == "register":
            return self._emit_atexit(expr)
        if _has(expr, "symbol"):
            symbol: str = _node_symbol(expr)
            if symbol == "ps_path_join":
                return self._emit_join(values)
            if symbol in {"ps_deque_append", "ps_deque_appendleft"}:
                return self._emit_deque_store(expr, values)
            if symbol in {"ps_deque_pop", "ps_deque_popleft"}:
                return self._emit_deque_load(expr, values)
            if symbol == "ps_task_result":
                return self._emit_task_result(expr, values)
            if symbol == "ps_queue_put":
                # The item travels as a pointer to a stashed slot, the shape
                # `deque.append` uses: the queue stores element-sized slots.
                stash_args: list[dict[str, Any]] = _node_args(expr)
                stash_arg: dict[str, Any] = stash_args[2]
                stash_typ: dict[str, Any] = _arg_type(stash_arg)
                slot = self._stash_cell(stash_typ, values[2])
                self._abi_decl("ps_queue_put", "void", ["ptr", "i64", "ptr"])
                self._call_void("ps_queue_put", [f"ptr {values[0]}", f"i64 {values[1]}", f"ptr {slot}"])
                return ""
            if symbol == "ps_queue_get":
                out = self._alloca(_memory_type(typ))
                self._abi_decl("ps_queue_get", "void", ["ptr", "i64", "ptr"])
                self._call_void("ps_queue_get", [f"ptr {values[0]}", f"i64 {values[1]}", f"ptr {out}"])
                return self._widen_cell(typ, out)
            if symbol == "ps_aqueue_put":
                # The item travels as a pointer to a stashed slot, the shape
                # `deque.append` uses: the queue stores element-sized slots.
                aq_args: list[dict[str, Any]] = _node_args(expr)
                aq_arg: dict[str, Any] = aq_args[1]
                aq_typ: dict[str, Any] = _arg_type(aq_arg)
                slot = self._stash_cell(aq_typ, values[1])
                self._abi_decl("ps_aqueue_put", "i64", ["ptr", "ptr"])
                return self._call_ret("i64", "ps_aqueue_put", [f"ptr {values[0]}", f"ptr {slot}"])
            if symbol == "ps_aqueue_taken":
                # The element the take removed rides out through an out pointer.
                elem: dict[str, Any] = typ
                out = self._alloca(_memory_type(elem))
                self._abi_decl("ps_aqueue_taken", "void", ["ptr", "i64", "ptr"])
                self._call_void("ps_aqueue_taken", [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
                return self._widen_cell(elem, out)
            if symbol == "ps_to_thread_result":
                elem = typ
                out = self._alloca(_memory_type(elem))
                self._abi_decl("ps_to_thread_result", "void", ["ptr", "i64", "ptr"])
                self._call_void("ps_to_thread_result", [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
                return self._widen_cell(elem, out)
            if symbol == "ps_to_thread_new":
                return self._emit_to_thread_new(expr, values)
            if symbol == "ps_to_thread_start":
                return self._emit_to_thread_start(expr, values)
            if symbol == "ps_list_push":
                return self._emit_list_push(expr, values)
            if symbol.startswith("ps_exc_isa_value:"):
                # isinstance(e, Exc): the wanted code rides in the symbol name;
                # the runtime walks the parent chain off the caught value.
                needed: set[str] = self.needed
                needed.add("exc")
                code: str = symbol.split(":", 1)[1]
                self._abi_decl("ps_exc_isa_value", "i32", ["ptr", "i32"])
                raw = self._call_ret("i32", "ps_exc_isa_value", [f"ptr {values[0]}", f"i32 {code}"])
                return self._flag(raw)
            if symbol == "ps_map_del":
                needed = self.needed
                needed.add("map")
                del_args: list[dict[str, Any]] = _node_args(expr)
                del_arg: dict[str, Any] = del_args[1]
                del_typ: dict[str, Any] = _arg_type(del_arg)
                key = self._stash_cell(del_typ, values[1])
                self._abi_decl("ps_map_del", "void", ["ptr", "ptr"])
                self._call_void("ps_map_del", [f"ptr {values[0]}", f"ptr {key}"])
                return ""
            if symbol == "ps_copy_deep":
                return self._emit_copy_deep(expr, values)
            if symbol == "ps_dict_items":
                # The element is a synthetic tuple_N class; the C side packs key
                # and value cells at byte offsets read off the class layout.
                tname: str = dict_to_type(_elem_of(typ)).name
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
            if symbol == "ps_os_walk":
                # The element is the synthetic (str, list[str], list[str]) tuple
                # class; the C side builds one heap record per walk row at the
                # class layout (path cell, dirs cell, files cell).
                needed = self.needed
                needed.add("seq")
                tname = dict_to_type(_elem_of(typ)).name
                size_ptr = self.tmp()
                size = self.tmp()
                self.line(f"{size_ptr} = getelementptr %cls.{tname}, ptr null, i64 1")
                self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
                self._abi_decl("ps_os_walk", "ptr", ["i64", "ptr", "i64"])
                parts4: tuple[str, str] = self._buf_parts(values[0])
                length: str = parts4[0]
                ptr: str = parts4[1]
                return self._call_ret("ptr", "ps_os_walk", [f"i64 {length}", f"ptr {ptr}", f"i64 {size}"])
            if symbol == "ps_str_format":
                # args[0] is the format string; the rest marshal into %ps.arg.
                count = len(values) - 1
                nkw: int = 0
                nkw = _opt_int(expr, "format_kwnames", 0)
                style_name: str | None = _opt_str_or_none(expr, "format_style")
                if style_name is None:
                    style_name = "percent"
                style: int = {"percent": 0, "dot": 1, "map": 2}[style_name]
                if count:
                    self._ensure_arg_type()
                    fmt_slot = self.tmp()
                    self.line(f"{fmt_slot} = alloca [{count} x %ps.arg]")
                    for index in range(count):
                        base = self.tmp()
                        self.line(f"{base} = getelementptr [{count} x %ps.arg], ptr {fmt_slot}, i64 0, i64 {index}")
                        fmt_arg: dict[str, Any] = args[index + 1]
                        fmt_typ: dict[str, Any] = _arg_type(fmt_arg)
                        self._store_ps_arg(base, values[index + 1], _mark_fmt(fmt_typ))
                    arg_ptr = self.tmp()
                    self.line(f"{arg_ptr} = getelementptr [{count} x %ps.arg], ptr {fmt_slot}, i64 0, i64 0")
                else:
                    arg_ptr = "null"
                parts5: tuple[str, str] = self._buf_parts(values[0])
                flen: str = parts5[0]
                fptr: str = parts5[1]
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
            if symbol == "ps_thread_new":
                return self._emit_thread_new(expr)
            if symbol == "ps_cond_init":
                # `Condition()`: the runtime owns the record; the value is its
                # pointer, allocated here and initialized by ps_cond_init.
                needed = self.needed
                needed.add("alloc")
                cond_slot = self._call_ret("ptr", "ps_alloc", ["i64 96"])
                self._abi_decl("ps_cond_init", "void", ["ptr"])
                self._call_void("ps_cond_init", [f"ptr {cond_slot}"])
                return cond_slot
            # boxed:begin
            if symbol == "ps_island_call":
                return self._emit_island_call(expr, values)
            if symbol == INVOKE_SYMBOL:
                return self._emit_island_fn_call(expr, values)
            if symbol == DEF_HANDLE_SYMBOL:
                return self._emit_island_def_handle(expr)
            if symbol == MOD_DEF_HANDLE_SYMBOL:
                return self._emit_island_mod_def_handle(expr)
            if symbol == "ps_island_getattr":
                return self._emit_island_getattr(expr, values)
            if symbol == "ps_island_getitem":
                return self._emit_island_getitem(expr, values)
            if symbol == "ps_island_unmarshal":
                return self._emit_island_unmarshal(expr, values)
            # boxed:end
            if module == "pyscriptc_ndarray":
                # The ndarray surface (issue #496): every call here carries
                # the runtime symbol the lowering attached.
                return self._nd_emit(expr, values)
            return self._emit_abi(expr, values)
        if module == "math":
            return self._emit_math(name, values)
        if module == "re":
            needed = self.needed
            needed.add("re")
            return self._emit_re(name, values)
        if module in {"json", "orjson"}:
            # orjson is the pack over the same writer and reader: dumps comes
            # back as bytes, loads accepts str or bytes through the same
            # (len, ptr) ABI.
            needed = self.needed
            needed.add("json")
            if name == "loads":
                return self._emit_json_loads(values[0], typ)
            json_arg: dict[str, Any] = args[0]
            json_typ: dict[str, Any] = _arg_type(json_arg)
            flags: int = _opt_int(expr, "json_flags", 0)
            return self._emit_json_dumps(values[0], json_typ, flags)
        if module == "urllib.parse":
            needed = self.needed
            needed.add("url")
            return self._emit_url(name, values)
        if module == "base64":
            needed = self.needed
            needed.add("b64")
            return self._emit_b64(name, values)
        if module == "hashlib":
            needed = self.needed
            needed.add("hash")
            return self._emit_hash(name, values)
        if module == "struct":
            needed = self.needed
            needed.add("struct")
            return self._emit_struct(name, values, args, typ)
        raise IRError(f"no LLVM lowering for {module}.{name}")

    def emit_method(self, expr: dict[str, Any], values: list[str]) -> str:
        callee: dict[str, Any] = _node_callee(expr)
        name: str = _callee_name(callee)
        kind: str = "str"
        if _has(callee, "receiver"):
            kind = _callee_receiver(callee)
        needed: set[str] = self.needed
        needed.add("str")
        parts6: tuple[str, str] = self._buf_parts(values[0])
        length: str = parts6[0]
        ptr: str = parts6[1]
        if name in {"strip", "lstrip", "rstrip", "upper", "lower"}:
            self._abi_decl(f"ps_str_{name}", "void", ["i64", "ptr", "ptr"])
            return self._buf_out(f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}"])
        if name in {"title", "capitalize", "casefold", "swapcase"}:
            self._abi_decl(f"ps_str_{name}", "void", ["i64", "ptr", "ptr"])
            return self._buf_out(f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}"])
        if name in {"isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower"}:
            self._abi_decl(f"ps_str_{name}", "i32", ["i64", "ptr"])
            return self._flag(self._call_ret("i32", f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}"]))
        if name == "isascii":
            self._abi_decl("ps_str_isascii", "i32", ["i64", "ptr"])
            return self._flag(self._call_ret("i32", "ps_str_isascii", [f"i64 {length}", f"ptr {ptr}"]))
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
            tabsize: str = "8"
            if len(values) > 1:
                tabsize = values[1]
            self._abi_decl("ps_str_expandtabs", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_expandtabs", [f"i64 {length}", f"ptr {ptr}", f"i64 {tabsize}"])
        if name == "zfill":
            self._abi_decl("ps_str_zfill", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_zfill", [f"i64 {length}", f"ptr {ptr}", f"i64 {values[1]}"])
        if name in {"ljust", "rjust", "center"}:
            side: str = {"ljust": "0", "rjust": "1", "center": "2"}[name]
            fill_len: str = "1"
            fill_ptr: str = self.format_global("fill", b" ")
            if len(values) > 2:
                parts7: tuple[str, str] = self._buf_parts(values[2])
                fill_len: str = parts7[0]
                fill_ptr: str = parts7[1]
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
            parts8: tuple[str, str] = self._buf_parts(values[1])
            sep_len: str = parts8[0]
            sep_ptr: str = parts8[1]
            tname: str = _callee_tuple(callee)
            mem = self._alloc_class(tname)
            right: str = "0"
            if name == "rpartition":
                right = "1"
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
            sep_len: str
            sep_ptr: str
            mode: str
            maxsplit: str
            if len(values) == 1:
                sep_len, sep_ptr = "0", "null"
                mode = "1"
                if kind == "bytes":
                    mode = "2"
                maxsplit = "-1"
            else:
                parts9: tuple[str, str] = self._buf_parts(values[1])
                sep_len: str = parts9[0]
                sep_ptr: str = parts9[1]
                mode = "0"
                maxsplit = "-1"
                if len(values) > 2:
                    maxsplit = values[2]
            right = "0"
            if name == "rsplit":
                right = "1"
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
        parts10: tuple[str, str] = self._buf_parts(values[1])
        arg_len: str = parts10[0]
        arg_ptr: str = parts10[1]
        if name in {"removeprefix", "removesuffix"}:
            self._abi_decl(f"ps_str_{name}", "void", ["i64", "ptr", "i64", "ptr", "ptr"])
            return self._buf_out(f"ps_str_{name}", [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}"])
        if name == "replace":
            parts11: tuple[str, str] = self._buf_parts(values[2])
            new_len: str = parts11[0]
            new_ptr: str = parts11[1]
            self._abi_decl("ps_str_replace", "void", ["i64", "ptr", "i64", "ptr", "i64", "ptr", "ptr"])
            return self._buf_out(
                "ps_str_replace",
                [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}", f"i64 {new_len}", f"ptr {new_ptr}"],
            )
        if name in {"find", "count", "index", "rfind"}:
            symbol: str = {
                "find": "ps_str_find",
                "count": "ps_str_count",
                "index": "ps_str_index",
                "rfind": "ps_str_rfind",
            }[name]
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
        needed: set[str] = self.needed
        needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        init = f"cls.{name}.__init__"
        if init in self.functions:
            fn = self.functions[init]
            call_args: list[str] = [mem]
            for value in values:
                call_args.append(value)
            self.call_function(fn, call_args)
        return mem
