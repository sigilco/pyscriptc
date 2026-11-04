"""Expression emission: the expression dispatch and the value forms (constants,
operators, containers, subscripts, attributes, closures).
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _elem_size, _fn_symbol, _llvm_double, _llvm_type, _memory_type
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import FitError, IRError
from pyscriptc.ir import fits_i64

_ICMP = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge", "==": "eq", "!=": "ne"}

_FOP = {"+": "fadd", "-": "fsub", "*": "fmul"}

_INT_HELPER = {
    "+": "iadd",
    "-": "isub",
    "*": "imul",
    "//": "floordiv",
    "%": "imod",
    "<<": "ishl",
    ">>": "ishr",
}

_INT_INS = {"&": "and", "|": "or", "^": "xor"}

_FHELPER = {"/": "fdiv", "%": "fmod", "//": "ffloordiv", "**": "fpow"}


class ExprsMixin(EmitterBase):
    def emit_expr(self, expr: dict[str, Any]) -> str:
        kind: str = expr["kind"]
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            name_id: str = expr["id"]
            return self.load(name_id)
        if kind == "unary":
            operand_expr: dict[str, Any] = expr["operand"]
            operand = self.emit_expr(operand_expr)
            op: str = expr["op"]
            typ: dict[str, Any] = expr["type"]
            tk: str = typ["kind"]
            if op == "-" and tk == "int":
                return self.helper_call("ineg", "i64", [operand])
            if op == "-" and tk == "float":
                reg = self.tmp()
                self.line(f"{reg} = fneg double {operand}")
                return reg
            if op == "-" and tk == "class":
                mem = self._alloc_class("TimeDelta")
                self._abi_decl("ps_delta_neg", "void", ["ptr", "ptr"])
                self._call_void("ps_delta_neg", [f"ptr {operand}", f"ptr {mem}"])
                return mem
            if op == "not":
                reg = self.tmp()
                self.line(f"{reg} = xor i1 {operand}, true")
                return reg
            if op == "~" and tk == "int":
                reg = self.tmp()
                self.line(f"{reg} = xor i64 {operand}, -1")
                return reg
            raise IRError(f"cannot emit unary {op}")
        if kind == "binary":
            left_expr: dict[str, Any] = expr["left"]
            right_expr: dict[str, Any] = expr["right"]
            return self.emit_binary(expr, self.emit_expr(left_expr), self.emit_expr(right_expr))
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
            val_expr: dict[str, Any] = expr["value"]
            value = self.emit_expr(val_expr)
            reg = self.tmp()
            self.line(f"{reg} = sitofp i64 {value} to double")
            return reg
        if kind == "contains":
            return self.emit_contains(expr)
        if kind == "closure":
            return self.emit_closure(expr)
        raise IRError(f"cannot emit expression {kind}")

    def emit_const(self, expr: dict[str, Any]) -> str:
        typ: dict[str, Any] = expr["type"]
        kind: str = typ["kind"]
        if kind == "int":
            val_raw: str = expr["value"]
            number = int(val_raw)
            if not fits_i64(number):
                raise FitError(f"integer literal {val_raw} does not fit in i64; refusing to truncate")
            return str(number)
        if kind == "bool":
            val_bool: bool = expr["value"]
            return "true" if val_bool else "false"
        if kind == "float":
            val_float_raw: str = expr["value"]
            return _llvm_double(float(val_float_raw))
        if kind == "str":
            val_str: str = expr["value"]
            return self.string_value(val_str)
        if kind == "bytes":
            val_bytes: str = expr["value"]
            return self.string_value(val_bytes, binary=True)
        if kind == "none":
            raise IRError("None is not a runtime value")
        raise IRError(f"cannot emit constant of type {kind}")

    def emit_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        typ: dict[str, Any] = expr["type"]
        kind: str = typ["kind"]
        op: str = expr["op"]
        if kind == "int":
            if op in _INT_INS:
                reg = self.tmp()
                self.line(f"{reg} = {_INT_INS[op]} i64 {left}, {right}")
                return reg
            return self.helper_call(_INT_HELPER[op], "i64", [left, right])
        if kind == "float":
            if op in _FOP:
                reg = self.tmp()
                self.line(f"{reg} = {_FOP[op]} double {left}, {right}")
                return reg
            return self.helper_call(_FHELPER[op], "double", [left, right], arg_type="double")
        if kind in {"str", "bytes"} and op == "+":
            return self.helper_call("strconcat", "%ps.str", [left, right], arg_type="%ps.str")
        left_node: dict[str, Any] = expr["left"]
        left_type: dict[str, Any] = left_node["type"]
        left_kind: str = left_type["kind"]
        if left_kind == "class":
            return self._emit_class_binary(expr, left, right)
        raise IRError(f"cannot emit {op} on {kind}")

    def emit_compare(self, expr: dict[str, Any]) -> str:
        left_node: dict[str, Any] = expr["left"]
        right_node: dict[str, Any] = expr["right"]
        left_type: dict[str, Any] = left_node["type"]
        right_type: dict[str, Any] = right_node["type"]
        left_kind: str = left_type["kind"]
        right_kind: str = right_type["kind"]
        op: str = expr["op"]
        if left_kind == "none" or right_kind == "none":
            if op not in {"==", "!="}:
                raise IRError(f"cannot compare None with {op}")
            self._emit_none_side(left_node)
            self._emit_none_side(right_node)
            # None equals only None. A str, int, or other value is never None.
            same: bool = left_kind == "none" and right_kind == "none"
            truth: bool = same if op == "==" else not same
            reg = self.tmp()
            bit: str = "true" if truth else "false"
            self.line(f"{reg} = icmp eq i1 {bit}, true")
            return reg
        left = self.emit_expr(left_node)
        right = self.emit_expr(right_node)
        kind: str = left_kind
        if op in {"is", "is not"}:
            return self._emit_identity(op, kind, left, right)
        reg = self.tmp()
        if kind in {"int", "bool"}:
            icmp_op: str = _ICMP[op]
            llvm_type: str = "i64" if kind == "int" else "i1"
            self.line(f"{reg} = icmp {icmp_op} {llvm_type} {left}, {right}")
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
        typ: dict[str, Any] = expr["type"]
        tk: str = typ["kind"]
        if tk != "none":
            self.emit_expr(expr)
            return
        kind: str = expr["kind"]
        if kind == "const" or kind == "name":
            return
        self.emit_expr(expr)

    def emit_list(self, expr: dict[str, Any]) -> str:
        needed = self.needed
        needed.add("seq")
        typ: dict[str, Any] = expr["type"]
        elem: dict[str, Any] = typ["elem"]
        elems: list[dict[str, Any]] = expr["elems"]
        values = [self.emit_expr(item) for item in elems]
        elem_sz: int = _elem_size(elem)
        v_len: int = len(values)
        reg = self._call_ret("ptr", "ps_seq_new", [f"i64 {elem_sz}", f"i64 {v_len}"])
        for index, value in enumerate(values):
            slot = self._stash_cell(elem, value)
            self.line(f"call void @ps_seq_set(ptr {reg}, i64 {index}, ptr {slot})")
        return reg

    def emit_dict(self, expr: dict[str, Any]) -> str:
        needed = self.needed
        needed.add("map")
        typ: dict[str, Any] = expr["type"]
        key_t: dict[str, Any] = typ["key"]
        val_t: dict[str, Any] = typ["value"]
        key_kind_str: str = key_t["kind"]
        key_kind = 1 if key_kind_str == "int" else 2
        pairs: list[dict[str, Any]] = expr["pairs"]
        stored: list[tuple[str, str]] = []
        for pair in pairs:
            k_node: dict[str, Any] = pair["key"]
            v_node: dict[str, Any] = pair["value"]
            stored.append((self.emit_expr(k_node), self.emit_expr(v_node)))
        k_sz: int = _elem_size(key_t)
        v_sz: int = _elem_size(val_t)
        s_len: int = len(stored)
        reg = self._call_ret(
            "ptr",
            "ps_map_new",
            [
                f"i32 {key_kind}",
                f"i64 {k_sz}",
                f"i64 {v_sz}",
                f"i64 {s_len}",
            ],
        )
        for pair_entry in stored:
            key_v = pair_entry[0]
            val_v = pair_entry[1]
            key = self._stash_cell(key_t, key_v)
            value = self._stash_cell(val_t, val_v)
            self.line(f"call void @ps_map_put(ptr {reg}, ptr {key}, ptr {value})")
        return reg

    def emit_subscript(self, expr: dict[str, Any]) -> str:
        val_node: dict[str, Any] = expr["value"]
        idx_node: dict[str, Any] = expr["index"]
        value = self.emit_expr(val_node)
        index = self.emit_expr(idx_node)
        val_type: dict[str, Any] = val_node["type"]
        kind: str = val_type["kind"]
        needed = self.needed
        expr_type: dict[str, Any] = expr["type"]
        if kind == "list":
            slot: dict[str, Any] = val_type["elem"]
            needed.add("seq")
            out = self._alloca(_memory_type(expr_type))
            self.line(f"call void @ps_seq_get(ptr {value}, i64 {index}, ptr {out})")
            slot_kind: str = slot["kind"]
            proven: dict[str, Any] | None = slot if slot_kind == "any" else None
            return self._widen_cell(expr_type, out, proven)
        if kind == "dict":
            slot_d: dict[str, Any] = val_type["value"]
            needed.add("map")
            idx_type: dict[str, Any] = idx_node["type"]
            key = self._stash_cell(idx_type, index)
            out = self._alloca(_memory_type(expr_type))
            self.line(f"call void @ps_map_get(ptr {value}, ptr {key}, ptr {out})")
            slot_d_kind: str = slot_d["kind"]
            proven_d: dict[str, Any] | None = slot_d if slot_d_kind == "any" else None
            return self._widen_cell(expr_type, out, proven_d)
        if kind == "str":
            needed.add("str")
            parts = self._buf_parts(value)
            length = parts[0]
            ptr = parts[1]
            self._abi_decl("ps_str_get", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_get", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])
        if kind == "bytes":
            needed.add("str")
            parts_b = self._buf_parts(value)
            length_b = parts_b[0]
            ptr_b = parts_b[1]
            return self._call_ret("i64", "ps_buf_at", [f"i64 {length_b}", f"ptr {ptr_b}", f"i64 {index}"])
        if kind == "class":
            if "name" in val_type:
                class_name: str = val_type["name"]
                if class_name == "Deque":
                    out = self._alloca(_memory_type(expr_type))
                    self._abi_decl("ps_deque_get", "void", ["ptr", "i64", "ptr"])
                    self._call_void("ps_deque_get", [f"ptr {value}", f"i64 {index}", f"ptr {out}"])
                    return self._widen_cell(expr_type, out)
        raise IRError(f"cannot index {kind}")

    def emit_attr(self, expr: dict[str, Any]) -> str:
        val_node: dict[str, Any] = expr["value"]
        obj = self.emit_expr(val_node)
        val_type: dict[str, Any] = val_node["type"]
        class_name: str = val_type["name"]
        attr_name: str = expr["name"]
        field: dict[str, Any] = self._field(class_name, attr_name)
        slot = self._field_ptr(class_name, obj, field)
        field_type: dict[str, Any] = field["type"]
        return self._widen_cell(field_type, slot)

    def emit_contains(self, expr: dict[str, Any]) -> str:
        needle_node: dict[str, Any] = expr["needle"]
        hay_node: dict[str, Any] = expr["hay"]
        needle = self.emit_expr(needle_node)
        hay = self.emit_expr(hay_node)
        hay_type: dict[str, Any] = hay_node["type"]
        hay_kind: str = hay_type["kind"]
        needed = self.needed
        if hay_kind in {"str", "bytes"}:
            needed.add("str")
            hay_parts = self._buf_parts(hay)
            hay_len = hay_parts[0]
            hay_ptr = hay_parts[1]
            needle_parts = self._buf_parts(needle)
            needle_len = needle_parts[0]
            needle_ptr = needle_parts[1]
            found = self._call_ret(
                "i64", "ps_str_find", [f"i64 {hay_len}", f"ptr {hay_ptr}", f"i64 {needle_len}", f"ptr {needle_ptr}"]
            )
            flag = self.tmp()
            self.line(f"{flag} = icmp sge i64 {found}, 0")
        elif hay_kind == "list":
            needed.add("seq")
            elem_type: dict[str, Any] = hay_type["elem"]
            elem: str = elem_type["kind"]
            if elem == "int":
                flag = self._call_ret("i32", "ps_seq_has_i64", [f"ptr {hay}", f"i64 {needle}"])
            elif elem == "float":
                flag = self._call_ret("i32", "ps_seq_has_f64", [f"ptr {hay}", f"double {needle}"])
            else:
                parts = self._buf_parts(needle)
                length = parts[0]
                ptr = parts[1]
                flag = self._call_ret("i32", "ps_seq_has_buf", [f"ptr {hay}", f"i64 {length}", f"ptr {ptr}"])
            bit = self.tmp()
            self.line(f"{bit} = icmp ne i32 {flag}, 0")
            flag = bit
        elif hay_kind == "dict":
            needed.add("map")
            needle_type: dict[str, Any] = needle_node["type"]
            key = self._stash_cell(needle_type, needle)
            raw = self._call_ret("i32", "ps_map_has", [f"ptr {hay}", f"ptr {key}"])
            flag = self.tmp()
            self.line(f"{flag} = icmp ne i32 {raw}, 0")
        else:
            raise IRError(f"cannot emit containment for {hay_kind}")
        negated: bool = expr["negated"]
        if not negated:
            return flag
        reg = self.tmp()
        self.line(f"{reg} = xor i1 {flag}, true")
        return reg

    def emit_closure(self, expr: dict[str, Any]) -> str:
        """A callable value: a heap cell of {implementation, env}.

        Pair and env are `ps_alloc`'d so a callable returned or stored
        outlives its frame. Env-first functions (nested defs, lambdas) are
        their own entry point; a plain function gets a lazy `name.clo`
        wrapper that drops the env; a `stub` spec gets a synthesized
        env-first `functools.partial` shim. Env items either name a local
        (its storage pointer is packed) or carry an `init` expression (its
        value goes into a fresh heap cell).
        """
        if "stub" in expr:
            stub: dict[str, Any] = expr["stub"]
            impl = self._partial_symbol(stub)
        elif "thread_stub" in expr:
            thread_stub: dict[str, Any] = expr["thread_stub"]
            impl = self._thread_symbol(thread_stub)
        else:
            fn_name: str = expr["name"]
            fn: dict[str, Any] = self.functions[fn_name]
            f_sym: str = _fn_symbol(fn["name"])
            if "env" in fn:
                impl = f_sym
            else:
                impl = f"{f_sym}.clo"
                clo_wrappers = self.clo_wrappers
                clo_wrappers.add(fn["name"])
        items: list[dict[str, Any]] = expr["env"]
        needed = self.needed
        if not items:
            env = "null"
        else:
            needed.add("alloc")
            env = self._call_ret("ptr", "ps_alloc", [f"i64 {8 * len(items)}"])
            for index in range(len(items)):
                item: dict[str, Any] = items[index]
                if "init" in item:
                    item_type: dict[str, Any] = item["type"]
                    item_init: dict[str, Any] = item["init"]
                    cell = self._heap_cell(item_type, self.emit_expr(item_init))
                else:
                    item_name: str = item["name"]
                    slot_tuple: tuple[str, dict[str, Any]] = self.slots[item_name]
                    cell = slot_tuple[0]
                dest = env if index == 0 else self.tmp()
                if index != 0:
                    self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
                self.line(f"store ptr {cell}, ptr {dest}")
        needed.add("alloc")
        pair = self._call_ret("ptr", "ps_alloc", ["i64 16"])
        self.line(f"store ptr @{impl}, ptr {pair}")
        env_ptr = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        self.line(f"store ptr {env}, ptr {env_ptr}")
        return pair

    def emit_value_call(self, expr: dict[str, Any], values: list[str]) -> str:
        """Indirect call through a callable cell: load {impl, env}, call env-first."""
        callee: dict[str, Any] = expr["callee"]
        target: dict[str, Any] = callee["target"]
        pair = self.emit_expr(target)
        impl = self._load("ptr", pair)
        env_ptr = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        env = self._load("ptr", env_ptr)
        args: list[str] = [f"ptr {env}"]
        expr_args: list[dict[str, Any]] = expr["args"]
        for i in range(len(expr_args)):
            arg: dict[str, Any] = expr_args[i]
            value = values[i]
            arg_type: dict[str, Any] = arg["type"]
            args.append(f"{_llvm_type(arg_type)} {value}")
        expr_type: dict[str, Any] = expr["type"]
        ret = _llvm_type(expr_type)
        if ret == "void":
            self.line(f"call void {impl}({', '.join(args)})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} {impl}({', '.join(args)})")
        return reg

    def call_function(self, fn: dict[str, Any], values: list[str]) -> str:
        ret_type: dict[str, Any] = fn["returnType"]
        ret = _llvm_type(ret_type)
        params: list[dict[str, Any]] = fn["params"]
        parts: list[str] = []
        for i in range(len(params)):
            param: dict[str, Any] = params[i]
            value = values[i]
            param_type: dict[str, Any] = param["type"]
            parts.append(f"{_llvm_type(param_type)} {value}")
        rendered = ", ".join(parts)
        fn_name: str = fn["name"]
        symbol = _fn_symbol(fn_name)
        if ret == "void":
            self.line(f"call void @{symbol}({rendered})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @{symbol}({rendered})")
        return reg
