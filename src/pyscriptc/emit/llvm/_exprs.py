"""Expression emission: the expression dispatch and the value forms (constants,
operators, containers, subscripts, attributes, closures).
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _elem_size, _fn_symbol, _llvm_double, _llvm_field_type, _llvm_type
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


class ExprsMixin:
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
            if expr["op"] == "~" and expr["type"]["kind"] == "int":
                reg = self.tmp()
                self.line(f"{reg} = xor i64 {operand}, -1")
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
        if kind == "closure":
            return self.emit_closure(expr)
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
        return self._load(_llvm_field_type(field["type"]), slot)

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
        stub = expr.get("stub")
        if stub is not None:
            impl = self._partial_symbol(stub)
        elif expr.get("thread_stub") is not None:
            impl = self._thread_symbol(expr["thread_stub"])
        else:
            fn = self.functions[expr["name"]]
            if "env" in fn:
                impl = _fn_symbol(fn["name"])
            else:
                impl = f"{_fn_symbol(fn['name'])}.clo"
                self.clo_wrappers.add(fn["name"])
        items = expr["env"]
        if not items:
            env = "null"
        else:
            self.needed.add("alloc")
            env = self._call_ret("ptr", "ps_alloc", [f"i64 {8 * len(items)}"])
            for index, item in enumerate(items):
                if "init" in item:
                    cell = self._heap_cell(item["type"], self.emit_expr(item["init"]))
                else:
                    cell = self.slots[item["name"]][0]
                dest = env if index == 0 else self.tmp()
                if index != 0:
                    self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
                self.line(f"store ptr {cell}, ptr {dest}")
        self.needed.add("alloc")
        pair = self._call_ret("ptr", "ps_alloc", ["i64 16"])
        self.line(f"store ptr @{impl}, ptr {pair}")
        env_ptr = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        self.line(f"store ptr {env}, ptr {env_ptr}")
        return pair

    def emit_value_call(self, expr: dict[str, Any], values: list[str]) -> str:
        """Indirect call through a callable cell: load {impl, env}, call env-first."""
        pair = self.emit_expr(expr["callee"]["target"])
        impl = self._load("ptr", pair)
        env_ptr = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        env = self._load("ptr", env_ptr)
        args = [f"ptr {env}"]
        args.extend(f"{_llvm_type(arg['type'])} {value}" for arg, value in zip(expr["args"], values, strict=True))
        ret = _llvm_type(expr["type"])
        if ret == "void":
            self.line(f"call void {impl}({', '.join(args)})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} {impl}({', '.join(args)})")
        return reg

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
