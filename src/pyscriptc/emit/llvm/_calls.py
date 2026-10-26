"""Call emission: builtins, module calls, method calls and constructors."""

from __future__ import annotations

from typing import Any

# boxed:begin
from pyscriptc.boxed import DEF_HANDLE_SYMBOL, INVOKE_SYMBOL
from pyscriptc.emit._ir import _elem_size, _memory_type

# boxed:end
from pyscriptc.errors import IRError


class CallsMixin:
    def emit_call(self, expr: dict[str, Any]) -> str:
        callee = expr["callee"]
        values = [self.emit_expr(arg) for arg in expr["args"]]
        if callee["kind"] == "builtin":
            return self.emit_builtin(callee["name"], values, [arg["type"] for arg in expr["args"]], expr)
        if callee["kind"] == "module":
            return self.emit_module_call(expr, values)
        if callee["kind"] == "method":
            return self.emit_method(expr, values)
        if callee["kind"] == "class":
            return self.emit_constructor(callee["name"], values)
        if callee["kind"] == "value":
            return self.emit_value_call(expr, values)
        fn = self.functions.get(callee["name"])
        if fn is None:
            # Refs #641: the defining module planned the callee for the
            # island and the merged module emits no body for it. A crash
            # here is a planning hole; name the callee instead.
            raise IRError(f"call to {callee['name']} has no lowered body (its module planned it for the island floor)")
        return self.call_function(fn, values)

    def emit_builtin(self, name: str, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> str:
        if name == "print":
            self._emit_print(values, arg_types, expr)
            return ""
        if name == "len" and arg_types[0]["kind"] == "str":
            # The code-point count: field 2 (field 0 is the byte length).
            reg = self.tmp()
            self.line(f"{reg} = extractvalue %ps.str {values[0]}, 2")
            return reg
        if name == "len" and arg_types[0]["kind"] == "bytes":
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
                length, ptr = self._buf_parts(values[0])
                return self._buf_out("ps_repr_bytes", [f"i64 {length}", f"ptr {ptr}"])
            if kind == "exc":
                self._abi_decl("ps_exc_str", "void", ["ptr", "ptr"])
                return self._buf_out("ps_exc_str", [f"ptr {values[0]}"])
            if kind == "class" and arg_types[0].get("name") == "Path":
                self._abi_decl("ps_path_posix", "void", ["ptr", "ptr"])
                return self._buf_out("ps_path_posix", [f"ptr {values[0]}"])
        raise IRError(f"no LLVM lowering for builtin {name}")

    def emit_module_call(self, expr: dict[str, Any], values: list[str]) -> str:
        callee = expr["callee"]
        module = callee["module"]
        name = callee["name"]
        args = expr["args"]
        if expr.get("fn"):
            return self._emit_reduce(expr)
        if name == "exit":
            if expr.get("exitMessage"):
                return self._emit_exit_message(values[0])
            return self._emit_exit(values[0])
        if module == "atexit" and name == "register":
            return self._emit_atexit(expr)
        if expr.get("symbol") == "ps_path_join":
            return self._emit_join(values)
        if expr.get("symbol") in {"ps_deque_append", "ps_deque_appendleft"}:
            return self._emit_deque_store(expr, values)
        if expr.get("symbol") in {"ps_deque_pop", "ps_deque_popleft"}:
            return self._emit_deque_load(expr, values)
        if expr.get("symbol") == "ps_task_result":
            return self._emit_task_result(expr, values)
        if expr.get("symbol") == "ps_queue_put":
            # The item travels as a pointer to a stashed slot, the shape
            # `deque.append` uses: the queue stores element-sized slots.
            slot = self._stash_cell(expr["args"][2]["type"], values[2])
            self._abi_decl("ps_queue_put", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_queue_put", [f"ptr {values[0]}", f"i64 {values[1]}", f"ptr {slot}"])
            return ""
        if expr.get("symbol") == "ps_queue_get":
            out = self._alloca(_memory_type(expr["type"]))
            self._abi_decl("ps_queue_get", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_queue_get", [f"ptr {values[0]}", f"i64 {values[1]}", f"ptr {out}"])
            return self._widen_cell(expr["type"], out)
        if expr.get("symbol") == "ps_aqueue_put":
            # The item travels as a pointer to a stashed slot, the shape
            # `deque.append` uses: the queue stores element-sized slots.
            slot = self._stash_cell(expr["args"][1]["type"], values[1])
            self._abi_decl("ps_aqueue_put", "i64", ["ptr", "ptr"])
            return self._call_ret("i64", "ps_aqueue_put", [f"ptr {values[0]}", f"ptr {slot}"])
        if expr.get("symbol") == "ps_aqueue_taken":
            # The element the take removed rides out through an out pointer.
            elem = expr["type"]
            out = self._alloca(_memory_type(elem))
            self._abi_decl("ps_aqueue_taken", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_aqueue_taken", [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
            return self._widen_cell(elem, out)
        if expr.get("symbol") == "ps_to_thread_result":
            elem = expr["type"]
            out = self._alloca(_memory_type(elem))
            self._abi_decl("ps_to_thread_result", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_to_thread_result", [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
            return self._widen_cell(elem, out)
        if expr.get("symbol") == "ps_to_thread_new":
            return self._emit_to_thread_new(expr, values)
        if expr.get("symbol") == "ps_to_thread_start":
            return self._emit_to_thread_start(expr, values)
        if expr.get("symbol") == "ps_list_push":
            return self._emit_list_push(expr, values)
        if isinstance(expr.get("symbol"), str) and expr["symbol"].startswith("ps_exc_isa_value:"):
            # isinstance(e, Exc): the wanted code rides in the symbol name; the
            # runtime walks the parent chain off the caught value.
            self.needed.add("exc")
            code = expr["symbol"].split(":", 1)[1]
            self._abi_decl("ps_exc_isa_value", "i32", ["ptr", "i32"])
            raw = self._call_ret("i32", "ps_exc_isa_value", [f"ptr {values[0]}", f"i32 {code}"])
            return self._flag(raw)
        if expr.get("symbol") == "ps_map_del":
            self.needed.add("map")
            key = self._stash_cell(expr["args"][1]["type"], values[1])
            self._abi_decl("ps_map_del", "void", ["ptr", "ptr"])
            self._call_void("ps_map_del", [f"ptr {values[0]}", f"ptr {key}"])
            return ""
        if expr.get("symbol") == "ps_copy_deep":
            return self._emit_copy_deep(expr, values)
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
        if expr.get("symbol") == "ps_thread_new":
            return self._emit_thread_new(expr)
        if expr.get("symbol") == "ps_cond_init":
            # `Condition()`: the runtime owns the record; the value is its
            # pointer, allocated here and initialized by ps_cond_init.
            self.needed.add("alloc")
            slot = self._call_ret("ptr", "ps_alloc", ["i64 96"])
            self._abi_decl("ps_cond_init", "void", ["ptr"])
            self._call_void("ps_cond_init", [f"ptr {slot}"])
            return slot
        # boxed:begin
        if expr.get("symbol") == "ps_island_call":
            return self._emit_island_call(expr, values)
        if expr.get("symbol") == INVOKE_SYMBOL:
            return self._emit_island_fn_call(expr, values)
        if expr.get("symbol") == DEF_HANDLE_SYMBOL:
            return self._emit_island_def_handle(expr)
        if expr.get("symbol") == "ps_island_getattr":
            return self._emit_island_getattr(expr, values)
        if expr.get("symbol") == "ps_island_getitem":
            return self._emit_island_getitem(expr, values)
        if expr.get("symbol") == "ps_island_unmarshal":
            return self._emit_island_unmarshal(expr, values)
        # boxed:end
        if expr.get("symbol"):
            return self._emit_abi(expr, values)
        if module == "math":
            return self._emit_math(name, values)
        if module == "re":
            self.needed.add("re")
            return self._emit_re(name, values)
        if module in {"json", "orjson"}:
            # orjson is the pack over the same writer and reader: dumps comes
            # back as bytes, loads accepts str or bytes through the same
            # (len, ptr) ABI.
            self.needed.add("json")
            if name == "loads":
                return self._emit_json_loads(values[0], expr["type"])
            return self._emit_json_dumps(values[0], args[0]["type"], expr.get("json_flags", 0))
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

    def emit_method(self, expr: dict[str, Any], values: list[str]) -> str:
        callee = expr["callee"]
        name = callee["name"]
        kind = str(callee.get("receiver", "str"))
        self.needed.add("str")
        length, ptr = self._buf_parts(values[0])
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
            self._abi_decl("ps_str_replace", "void", ["i64", "ptr", "i64", "ptr", "i64", "ptr", "ptr"])
            return self._buf_out(
                "ps_str_replace",
                [f"i64 {length}", f"ptr {ptr}", f"i64 {arg_len}", f"ptr {arg_ptr}", f"i64 {new_len}", f"ptr {new_ptr}"],
            )
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
