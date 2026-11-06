"""Expression emission: the expression dispatch and the value forms (constants,
operators, containers, subscripts, attributes, closures).
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import (
    _elem_size,
    _env_item_init,
    _env_item_name,
    _expr_kind,
    _fn_symbol,
    _kind,
    _llvm_double,
    _llvm_type,
    _memory_type,
    _node_args,
    _node_callee,
    _node_elems,
    _node_env,
    _node_hay,
    _node_id,
    _node_index,
    _node_left,
    _node_name,
    _node_needle,
    _node_negated,
    _node_op,
    _node_operand,
    _node_pairs,
    _node_right,
    _node_stub,
    _node_target_node,
    _node_thread_stub,
    _node_value_node,
    _pair_key,
    _pair_value,
)
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
        kind: str = _expr_kind(expr)
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            name_id: str = _node_id(expr)
            return self.load(name_id)
        if kind == "unary":
            op: str = _node_op(expr)
            sub_operand: dict[str, Any] = _node_operand(expr)
            operand: str = self.emit_expr(sub_operand)
            expr_type: dict[str, Any] = expr["type"]
            type_kind: str = _kind(expr_type)
            if op == "-" and type_kind == "int":
                return self.helper_call("ineg", "i64", [operand])
            if op == "-" and type_kind == "float":
                reg: str = self.tmp()
                self.line(f"{reg} = fneg double {operand}")
                return reg
            if op == "-" and type_kind == "class":
                mem: str = self._alloc_class("TimeDelta")
                self._abi_decl("ps_delta_neg", "void", ["ptr", "ptr"])
                self._call_void("ps_delta_neg", [f"ptr {operand}", f"ptr {mem}"])
                return mem
            if op == "not":
                reg: str = self.tmp()
                self.line(f"{reg} = xor i1 {operand}, true")
                return reg
            if op == "~" and type_kind == "int":
                reg: str = self.tmp()
                self.line(f"{reg} = xor i64 {operand}, -1")
                return reg
            raise IRError(f"cannot emit unary {op}")
        if kind == "binary":
            left_sub: dict[str, Any] = _node_left(expr)
            right_sub: dict[str, Any] = _node_right(expr)
            return self.emit_binary(expr, self.emit_expr(left_sub), self.emit_expr(right_sub))
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
            sub_val: dict[str, Any] = _node_value_node(expr)
            value: str = self.emit_expr(sub_val)
            reg: str = self.tmp()
            self.line(f"{reg} = sitofp i64 {value} to double")
            return reg
        if kind == "contains":
            return self.emit_contains(expr)
        if kind == "closure":
            return self.emit_closure(expr)
        raise IRError(f"cannot emit expression {kind}")

    def emit_const(self, expr: dict[str, Any]) -> str:
        expr_type: dict[str, Any] = expr["type"]
        kind: str = _kind(expr_type)
        if kind == "int":
            raw_int: str = expr["value"]
            number: int = int(raw_int)
            if not fits_i64(number):
                raise FitError(f"integer literal {raw_int} does not fit in i64; refusing to truncate")
            return str(number)
        if kind == "bool":
            bool_val: bool = expr["value"]
            return "true" if bool_val else "false"
        if kind == "float":
            raw_float: str = expr["value"]
            float_val: float = float(raw_float)
            return _llvm_double(float_val)
        if kind == "str":
            str_val: str = expr["value"]
            return self.string_value(str_val)
        if kind == "bytes":
            bytes_val: str = expr["value"]
            return self.string_value(bytes_val, binary=True)
        if kind == "none":
            raise IRError("None is not a runtime value")
        raise IRError(f"cannot emit constant of type {kind}")

    def emit_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        expr_type: dict[str, Any] = expr["type"]
        kind: str = _kind(expr_type)
        op: str = _node_op(expr)
        if kind == "int":
            if op in _INT_INS:
                ins: str = _INT_INS[op]
                reg: str = self.tmp()
                self.line(f"{reg} = {ins} i64 {left}, {right}")
                return reg
            helper: str = _INT_HELPER[op]
            return self.helper_call(helper, "i64", [left, right])
        if kind == "float":
            if op in _FOP:
                fop: str = _FOP[op]
                reg = self.tmp()
                self.line(f"{reg} = {fop} double {left}, {right}")
                return reg
            fhelper: str = _FHELPER[op]
            return self.helper_call(fhelper, "double", [left, right], arg_type="double")
        if kind in {"str", "bytes"} and op == "+":
            return self.helper_call("strconcat", "%ps.str", [left, right], arg_type="%ps.str")
        left_sub: dict[str, Any] = _node_left(expr)
        left_sub_type: dict[str, Any] = left_sub["type"]
        left_kind: str = _kind(left_sub_type)
        if left_kind == "class":
            return self._emit_class_binary(expr, left, right)
        raise IRError(f"cannot emit {op} on {kind}")

    def emit_compare(self, expr: dict[str, Any]) -> str:
        left_expr: dict[str, Any] = _node_left(expr)
        right_expr: dict[str, Any] = _node_right(expr)
        left_type: dict[str, Any] = left_expr["type"]
        right_type: dict[str, Any] = right_expr["type"]
        left_kind: str = _kind(left_type)
        right_kind: str = _kind(right_type)
        op: str = _node_op(expr)
        if left_kind == "none" or right_kind == "none":
            if op not in {"==", "!="}:
                raise IRError(f"cannot compare None with {op}")
            self._emit_none_side(left_expr)
            self._emit_none_side(right_expr)
            # None equals only None. A str, int, or other value is never None.
            same: bool = left_kind == "none" and right_kind == "none"
            truth: bool = same if op == "==" else not same
            reg: str = self.tmp()
            bit: str = "true" if truth else "false"
            self.line(f"{reg} = icmp eq i1 {bit}, true")
            return reg
        left: str = self.emit_expr(left_expr)
        right: str = self.emit_expr(right_expr)
        kind: str = left_kind
        if op in {"is", "is not"}:
            return self._emit_identity(op, kind, left, right)
        reg = self.tmp()
        if kind in {"int", "bool"}:
            icmp_op: str = _ICMP[op]
            itype: str = "i64" if kind == "int" else "i1"
            self.line(f"{reg} = icmp {icmp_op} {itype} {left}, {right}")
            return reg
        if kind == "float":
            fcmp_preds: dict[str, str] = {"==": "oeq", "!=": "une", "<": "olt", "<=": "ole", ">": "ogt", ">=": "oge"}
            pred: str = fcmp_preds[op]
            self.line(f"{reg} = fcmp {pred} double {left}, {right}")
            return reg
        if kind in {"str", "bytes"}:
            cmp: str = self._buf_cmp(left, right)
            scmp_preds: dict[str, str] = {"==": "eq", "!=": "ne", "<": "slt", "<=": "sle", ">": "sgt", ">=": "sge"}
            pred = scmp_preds[op]
            self.line(f"{reg} = icmp {pred} i32 {cmp}, 0")
            return reg
        if kind == "class":
            return self._emit_class_cmp(expr, left, right)
        raise IRError(f"cannot compare {kind}")

    def _emit_identity(self, op: str, kind: str, left: str, right: str) -> str:
        """`is`/`is not`: pointer identity for heap values, backing-pointer
        identity for str/bytes. Scalar kinds never reach here (the lowerer
        emits plain ==/!= for them)."""
        pred: str = "eq" if op == "is" else "ne"
        reg: str = self.tmp()
        if kind in {"str", "bytes"}:
            left_ptr: str = self.tmp()
            right_ptr: str = self.tmp()
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
        expr_type: dict[str, Any] = expr["type"]
        type_kind: str = _kind(expr_type)
        if type_kind != "none":
            self.emit_expr(expr)
            return
        expr_kind: str = _expr_kind(expr)
        if expr_kind == "const" or expr_kind == "name":
            return
        self.emit_expr(expr)

    def emit_list(self, expr: dict[str, Any]) -> str:
        needed = self.needed
        needed.add("seq")
        expr_type: dict[str, Any] = expr["type"]
        elem: dict[str, Any] = expr_type["elem"]
        elems_list: list[dict[str, Any]] = _node_elems(expr)
        values: list[str] = [self.emit_expr(item) for item in elems_list]
        reg: str = self._call_ret("ptr", "ps_seq_new", [f"i64 {_elem_size(elem)}", f"i64 {len(values)}"])
        for index in range(len(values)):
            value: str = values[index]
            slot: str = self._stash_cell(elem, value)
            self.line(f"call void @ps_seq_set(ptr {reg}, i64 {index}, ptr {slot})")
        return reg

    def emit_dict(self, expr: dict[str, Any]) -> str:
        needed = self.needed
        needed.add("map")
        expr_type: dict[str, Any] = expr["type"]
        key_t: dict[str, Any] = expr_type["key"]
        val_t: dict[str, Any] = expr_type["value"]
        key_t_kind: str = _kind(key_t)
        key_kind: int = 1 if key_t_kind == "int" else 2
        pairs: list[dict[str, Any]] = _node_pairs(expr)
        stored_keys: list[str] = []
        stored_vals: list[str] = []
        for pair in pairs:
            pair_key: dict[str, Any] = _pair_key(pair)
            pair_val: dict[str, Any] = _pair_value(pair)
            stored_keys.append(self.emit_expr(pair_key))
            stored_vals.append(self.emit_expr(pair_val))
        reg: str = self._call_ret(
            "ptr",
            "ps_map_new",
            [
                f"i32 {key_kind}",
                f"i64 {_elem_size(key_t)}",
                f"i64 {_elem_size(val_t)}",
                f"i64 {len(stored_keys)}",
            ],
        )
        for index in range(len(stored_keys)):
            key_v: str = stored_keys[index]
            val_v: str = stored_vals[index]
            key: str = self._stash_cell(key_t, key_v)
            value: str = self._stash_cell(val_t, val_v)
            self.line(f"call void @ps_map_put(ptr {reg}, ptr {key}, ptr {value})")
        return reg

    def emit_subscript(self, expr: dict[str, Any]) -> str:
        sub_val: dict[str, Any] = _node_value_node(expr)
        value: str = self.emit_expr(sub_val)
        sub_idx: dict[str, Any] = _node_index(expr)
        index: str = self.emit_expr(sub_idx)
        val_type: dict[str, Any] = sub_val["type"]
        kind: str = _kind(val_type)
        expr_type: dict[str, Any] = expr["type"]
        mem_type: str = _memory_type(expr_type)
        needed = self.needed
        if kind == "list":
            slot: dict[str, Any] = val_type["elem"]
            needed.add("seq")
            out: str = self._alloca(mem_type)
            self.line(f"call void @ps_seq_get(ptr {value}, i64 {index}, ptr {out})")
            slot_kind: str = _kind(slot)
            proven: dict[str, Any] | None = slot if slot_kind == "any" else None
            return self._widen_cell(expr_type, out, proven)
        if kind == "dict":
            slot = val_type["value"]
            needed.add("map")
            idx_type: dict[str, Any] = sub_idx["type"]
            key: str = self._stash_cell(idx_type, index)
            out = self._alloca(mem_type)
            self.line(f"call void @ps_map_get(ptr {value}, ptr {key}, ptr {out})")
            slot_kind = _kind(slot)
            proven = slot if slot_kind == "any" else None
            return self._widen_cell(expr_type, out, proven)
        if kind == "str":
            needed.add("str")
            parts: tuple[str, str] = self._buf_parts(value)
            length, ptr = parts
            self._abi_decl("ps_str_get", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_get", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])
        if kind == "bytes":
            needed.add("str")
            bparts: tuple[str, str] = self._buf_parts(value)
            blength, bptr = bparts
            return self._call_ret("i64", "ps_buf_at", [f"i64 {blength}", f"ptr {bptr}", f"i64 {index}"])
        if kind == "class" and "name" in val_type:
            type_name: str = val_type["name"]
            if type_name == "Deque":
                out = self._alloca(mem_type)
                self._abi_decl("ps_deque_get", "void", ["ptr", "i64", "ptr"])
                self._call_void("ps_deque_get", [f"ptr {value}", f"i64 {index}", f"ptr {out}"])
                return self._widen_cell(expr_type, out)
        raise IRError(f"cannot index {kind}")

    def emit_attr(self, expr: dict[str, Any]) -> str:
        sub_val: dict[str, Any] = _node_value_node(expr)
        obj: str = self.emit_expr(sub_val)
        val_type: dict[str, Any] = sub_val["type"]
        class_name: str = val_type["name"]
        attr_name: str = _node_name(expr)
        field: dict[str, Any] = self._field(class_name, attr_name)
        slot: str = self._field_ptr(class_name, obj, field)
        field_type: dict[str, Any] = field["type"]
        return self._widen_cell(field_type, slot)

    def emit_contains(self, expr: dict[str, Any]) -> str:
        needle_expr: dict[str, Any] = _node_needle(expr)
        needle: str = self.emit_expr(needle_expr)
        hay_expr: dict[str, Any] = _node_hay(expr)
        hay: str = self.emit_expr(hay_expr)
        hay_type: dict[str, Any] = hay_expr["type"]
        hay_kind: str = _kind(hay_type)
        flag: str = ""
        needed = self.needed
        if hay_kind in {"str", "bytes"}:
            needed.add("str")
            hay_len, hay_ptr = self._buf_parts(hay)
            needle_len, needle_ptr = self._buf_parts(needle)
            found: str = self._call_ret(
                "i64", "ps_str_find", [f"i64 {hay_len}", f"ptr {hay_ptr}", f"i64 {needle_len}", f"ptr {needle_ptr}"]
            )
            flag = self.tmp()
            self.line(f"{flag} = icmp sge i64 {found}, 0")
        elif hay_kind == "list":
            needed.add("seq")
            elem_type: dict[str, Any] = hay_type["elem"]
            elem: str = _kind(elem_type)
            raw_flag: str = ""
            if elem == "int":
                raw_flag = self._call_ret("i32", "ps_seq_has_i64", [f"ptr {hay}", f"i64 {needle}"])
            elif elem == "float":
                raw_flag = self._call_ret("i32", "ps_seq_has_f64", [f"ptr {hay}", f"double {needle}"])
            else:
                length, ptr = self._buf_parts(needle)
                raw_flag = self._call_ret("i32", "ps_seq_has_buf", [f"ptr {hay}", f"i64 {length}", f"ptr {ptr}"])
            bit: str = self.tmp()
            self.line(f"{bit} = icmp ne i32 {raw_flag}, 0")
            flag = bit
        elif hay_kind == "dict":
            needed.add("map")
            needle_type: dict[str, Any] = needle_expr["type"]
            key: str = self._stash_cell(needle_type, needle)
            raw: str = self._call_ret("i32", "ps_map_has", [f"ptr {hay}", f"ptr {key}"])
            flag = self.tmp()
            self.line(f"{flag} = icmp ne i32 {raw}, 0")
        else:
            raise IRError(f"cannot emit containment for {hay_kind}")
        negated: bool = _node_negated(expr)
        if not negated:
            return flag
        reg: str = self.tmp()
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
        impl: str = ""
        if "stub" in expr:
            stub: dict[str, Any] = _node_stub(expr)
            impl = self._partial_symbol(stub)
        elif "thread_stub" in expr:
            thread_stub: dict[str, Any] = _node_thread_stub(expr)
            impl = self._thread_symbol(thread_stub)
        else:
            expr_name: str = _node_name(expr)
            functions: dict[str, dict[str, Any]] = self.functions
            fn: dict[str, Any] = functions[expr_name]
            fn_name: str = fn["name"]
            fn_sym: str = _fn_symbol(fn_name)
            if "env" in fn:
                impl = fn_sym
            else:
                impl = f"{fn_sym}.clo"
                clo_wrappers = self.clo_wrappers
                clo_wrappers.add(fn_name)
        items: list[dict[str, Any]] = _node_env(expr)
        needed = self.needed
        if not items:
            env: str = "null"
        else:
            needed.add("alloc")
            env = self._call_ret("ptr", "ps_alloc", [f"i64 {8 * len(items)}"])
            for index in range(len(items)):
                item: dict[str, Any] = items[index]
                init_expr: dict[str, Any] | None = _env_item_init(item)
                if init_expr is not None:
                    item_type: dict[str, Any] = item["type"]
                    cell: str = self._heap_cell(item_type, self.emit_expr(init_expr))
                else:
                    item_name: str = _env_item_name(item)
                    slots = self.slots
                    slot_entry: tuple[str, dict[str, Any]] = slots[item_name]
                    cell = slot_entry[0]
                dest: str = env if index == 0 else self.tmp()
                if index != 0:
                    self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
                self.line(f"store ptr {cell}, ptr {dest}")
        needed.add("alloc")
        pair: str = self._call_ret("ptr", "ps_alloc", ["i64 16"])
        self.line(f"store ptr @{impl}, ptr {pair}")
        env_ptr: str = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        self.line(f"store ptr {env}, ptr {env_ptr}")
        return pair

    def emit_value_call(self, expr: dict[str, Any], values: list[str]) -> str:
        """Indirect call through a callable cell: load {impl, env}, call env-first."""
        callee: dict[str, Any] = _node_callee(expr)
        target: dict[str, Any] = _node_target_node(callee)
        pair: str = self.emit_expr(target)
        impl: str = self._load("ptr", pair)
        env_ptr: str = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        env: str = self._load("ptr", env_ptr)
        args: list[str] = [f"ptr {env}"]
        expr_args: list[dict[str, Any]] = _node_args(expr)
        for index in range(len(values)):
            arg: dict[str, Any] = expr_args[index]
            value: str = values[index]
            arg_type: dict[str, Any] = arg["type"]
            args.append(f"{_llvm_type(arg_type)} {value}")
        expr_type: dict[str, Any] = expr["type"]
        ret: str = _llvm_type(expr_type)
        if ret == "void":
            self.line(f"call void {impl}({', '.join(args)})")
            return ""
        reg: str = self.tmp()
        self.line(f"{reg} = call {ret} {impl}({', '.join(args)})")
        return reg

    def call_function(self, fn: dict[str, Any], values: list[str]) -> str:
        ret_type: dict[str, Any] = fn["returnType"]
        ret: str = _llvm_type(ret_type)
        fn_params: list[dict[str, Any]] = fn["params"]
        parts: list[str] = []
        for index in range(len(values)):
            param: dict[str, Any] = fn_params[index]
            value: str = values[index]
            param_type: dict[str, Any] = param["type"]
            parts.append(f"{_llvm_type(param_type)} {value}")
        rendered: str = ", ".join(parts)
        fn_name: str = fn["name"]
        symbol: str = _fn_symbol(fn_name)
        if ret == "void":
            self.line(f"call void @{symbol}({rendered})")
            return ""
        reg: str = self.tmp()
        self.line(f"{reg} = call {ret} @{symbol}({rendered})")
        return reg
