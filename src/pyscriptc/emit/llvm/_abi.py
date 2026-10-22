"""Runtime ABI emission: the `ps_*` call shapes, the class layout, the thread
and exit paths, and the `functools.partial` shims.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.emit._ir import _elem_size, _fn_symbol, _llvm_type
from pyscriptc.errors import IRError
from pyscriptc.stdmore import COMPARE_CLASSES


class AbiMixin:
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
        if kind in {"list", "dict", "class", "exc", "func"}:
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
        if kind in {"list", "dict", "func"} or (
            kind == "class"
            and ret.get("name") in {"Deque", "TextIO", "BytesIO", "CsvWriter", "Lock", "HttpResponse", "HttpRequest"}
        ):
            self._abi_decl(symbol, "ptr", types)
            return self._call_ret("ptr", symbol, rendered)
        if kind == "class":
            if symbol in {"ps_global_get_ptr", "ps_global_get_path"} or symbol in {"ps_event_new", "ps_sem_new"}:
                # The runtime owns the record: the value is the record pointer.
                self._abi_decl(symbol, "ptr", types)
                return self._call_ret("ptr", symbol, rendered)
            self._abi_decl(symbol, "void", [*types, "ptr"])
            mem = self._alloc_class(ret["name"])
            self._call_void(symbol, [*rendered, f"ptr {mem}"])
            return mem
        raise IRError(f"cannot emit {symbol}")

    def _emit_thread_new(self, expr: dict[str, Any]) -> str:
        """`Thread(...)`: build the target's callable pair (bound args inside
        the env), then hand it to the runtime with the name and daemon bits."""
        meta = expr["thread"]
        pair = self.emit_closure(meta["closure"])
        self._abi_decl("ps_thread_new", "ptr", ["ptr", "i64", "ptr"])
        name = self.string_value(meta["name"])
        name_len, name_ptr = self._buf_parts(name)
        return self._call_ret("ptr", "ps_thread_new", [f"ptr {pair}", f"i64 {meta['daemon']}", f"ptr {name_ptr}"])

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
            self.line(f"call void @ps_raise_code(i32 {EXCEPTIONS['ValueError']}) noreturn")
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

    def _emit_partial_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        """`pa.partial.N(env, p...)`: load the bound arguments and the inner
        pair out of the env, then call the inner impl env-first with the
        bound arguments prepended."""
        bound = spec["bound"]
        params = spec["params"]
        ret = _llvm_type(spec["return"])
        plist = ["ptr %env"] + [f"{_llvm_type(p)} %p{index}" for index, p in enumerate(params)]
        self.out.append(f"define internal {ret} @{symbol}({', '.join(plist)}) {{")
        self.out.append("entry:")
        args: list[str] = []
        for index, typ in enumerate(bound):
            if index == 0:
                self.out.append(f"  %b{index} = load ptr, ptr %env")
            else:
                self.out.append(f"  %g{index} = getelementptr ptr, ptr %env, i64 {index}")
                self.out.append(f"  %b{index} = load ptr, ptr %g{index}")
            self.out.append(f"  %v{index} = load {_llvm_type(typ)}, ptr %b{index}")
            args.append(f"{_llvm_type(typ)} %v{index}")
        self.out.append(f"  %ip = getelementptr ptr, ptr %env, i64 {len(bound)}")
        self.out.append("  %pc = load ptr, ptr %ip")
        self.out.append("  %pp = load ptr, ptr %pc")
        self.out.append("  %impl = load ptr, ptr %pp")
        self.out.append("  %ip2 = getelementptr ptr, ptr %pp, i64 1")
        self.out.append("  %env2 = load ptr, ptr %ip2")
        call_args = ["ptr %env2", *args] + [f"{_llvm_type(p)} %p{index}" for index, p in enumerate(params)]
        if ret == "void":
            self.out.append(f"  call void %impl({', '.join(call_args)})")
            self.out.append("  ret void")
        else:
            self.out.append(f"  %r = call {ret} %impl({', '.join(call_args)})")
            self.out.append(f"  ret {ret} %r")
        self.out.append("}")
        self.out.append("")

    def _partial_symbol(self, spec: dict[str, Any]) -> str:
        key = repr((spec["bound"], spec["params"], spec["return"]))
        symbol = self.partial_keys.get(key)
        if symbol is None:
            symbol = f"pa.partial.{self.partial_n}"
            self.partial_n += 1
            self.partial_keys[key] = symbol
            self.partials[symbol] = spec
        return symbol

    def _heap_cell(self, typ: dict[str, Any], value: str) -> str:
        self.needed.add("alloc")
        cell = self._call_ret("ptr", "ps_alloc", [f"i64 {_elem_size(typ)}"])
        self.line(f"store {_llvm_type(typ)} {value}, ptr {cell}")
        return cell
