"""Expression emission: the expression dispatch and the value forms (constants,
operators, containers, subscripts, attributes, closures).
"""

from __future__ import annotations

from typing import Any

from pyscriptc._irnode import F_NEGATED, P_BOOL
from pyscriptc.emit._ir import (
    _elem_size,
    _field_type,
    _fn_name,
    _fn_params,
    _fn_symbol,
    _has,
    _llvm_double,
    _llvm_type,
    _memory_type,
    _record,
    _return_type,
    _type_of,
)
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import FitError, IRError
from pyscriptc.ir import fits_i64
from pyscriptc.tirserde import dict_to_node, node_to_dict


def dict_to_dict(value: Any) -> dict[str, Any]:
    """The dict IR one typed value spells, or the mapping itself when it is
    already a plain dict (`node_to_dict` narrowed to the record case).

    This is the transitional boundary of the mixin's migration (#829):
    expression-level reads go through the typed `_irnode` record, while the
    child paths that still speak dict IR (the sibling mixins' `emit_expr`
    consumers and the stub-spec tables) take the record's dict spelling
    back. Local to this module: `_stmts` carries its own copy, and the
    slices do not touch each other's files.
    """
    if isinstance(value, dict):
        return value
    out: Any = node_to_dict(value)
    return out


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


def _kind_of(node: Any) -> str:
    """The kind tag of a typed node, or of the plain dict the escape hatch
    kept whole (a call with payload keys the typed record has no slot for —
    print's `sep`/`end`, the island call's slots, sys.exit's `exitMessage`).

    The dict form is the documented escape hatch: `tirserde.dict_to_node`
    keeps such a call whole so the round trip stays lossless, so the typed
    record genuinely cannot carry its payload. Its `kind` still reads here,
    because the tag itself is the one shape both spellings agree on."""
    if isinstance(node, dict):
        kind: Any = node["kind"]
        return kind if isinstance(kind, str) else ""
    kind_tag: str = node.kind
    return kind_tag


def _type_dict_of(node: Any) -> dict[str, Any]:
    """The type dict a node's `T` record spells, for the `_llvm_type` family
    (the ABI helpers still read the dict spelling; the shape lives in the
    serializer round trip, never in a raw `["type"]` read here).

    A node with no type reads as the `any` leaf: the pointer-shaped ABI the
    old dict accessors assumed for a missing type key."""
    if isinstance(node, dict):
        t: Any = node.get("type")
        if t is None:
            out_any: dict[str, Any] = {"kind": "any"}
            return out_any
        return t
    t = node.type
    if t is None:
        out_any: dict[str, Any] = {"kind": "any"}
        return out_any
    out: Any = node_to_dict(t)
    return out


def _type_kind_of(node: Any) -> str:
    """The type kind of a typed node's `T` record (`any` when it has none)."""
    if isinstance(node, dict):
        t: Any = node.get("type")
        if t is None:
            return "any"
        tk: Any = t["kind"]
        return tk if isinstance(tk, str) else "any"
    t = node.type
    if t is None:
        return "any"
    kind: str = t.kind
    return kind


def _type_of_child(node: Any) -> dict[str, Any]:
    """The type dict of a typed child node, or of the plain dict the escape
    hatch kept whole (a call with payload keys the typed record has no slot
    for — print's `sep`/`end`, the island call's slots)."""
    return _type_dict_of(node)


class ExprsMixin(EmitterBase):
    def emit_expr(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        kind: str = _kind_of(node)
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            return self.load(node.id)
        if kind == "unary":
            operand = self.emit_expr(dict_to_dict(node.operand))
            tk: str = _type_kind_of(node)
            if node.op == "-" and tk == "int":
                return self.helper_call("ineg", "i64", [operand])
            if node.op == "-" and tk == "float":
                reg = self.tmp()
                self.line(f"{reg} = fneg double {operand}")
                return reg
            if node.op == "-" and tk == "class":
                mem = self._alloc_class("TimeDelta")
                self._abi_decl("ps_delta_neg", "void", ["ptr", "ptr"])
                self._call_void("ps_delta_neg", [f"ptr {operand}", f"ptr {mem}"])
                return mem
            if node.op == "not":
                reg = self.tmp()
                self.line(f"{reg} = xor i1 {operand}, true")
                return reg
            if node.op == "~" and tk == "int":
                reg = self.tmp()
                self.line(f"{reg} = xor i64 {operand}, -1")
                return reg
            raise IRError(f"cannot emit unary {node.op}")
        if kind == "binary":
            return self.emit_binary(
                expr, self.emit_expr(dict_to_dict(node.left)), self.emit_expr(dict_to_dict(node.right))
            )
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
            value = self.emit_expr(dict_to_dict(node.value))
            reg = self.tmp()
            self.line(f"{reg} = sitofp i64 {value} to double")
            return reg
        if kind == "contains":
            return self.emit_contains(expr)
        if kind == "closure":
            return self.emit_closure(expr)
        raise IRError(f"cannot emit expression {kind}")

    def emit_const(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        kind: str = _type_kind_of(node)
        # The const payload is the record's tagged `svalue` (the serializer's
        # `_const_value` rule): bool spells "true"/"false", none is absent,
        # everything else keeps its text.
        if node.ptag == P_BOOL:
            value: Any = node.svalue == "true"
        else:
            value = node.svalue
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
        node: Any = dict_to_node(expr)
        kind: str = _type_kind_of(node)
        op: str = node.op
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
        if _type_kind_of(node.left) == "class":
            return self._emit_class_binary(expr, left, right)
        raise IRError(f"cannot emit {op} on {kind}")

    def emit_compare(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        left_kind: str = _type_kind_of(node.left)
        right_kind: str = _type_kind_of(node.right)
        op: str = node.op
        if left_kind == "none" or right_kind == "none":
            if op not in {"==", "!="}:
                raise IRError(f"cannot compare None with {op}")
            self._emit_none_side(node.left)
            self._emit_none_side(node.right)
            # None equals only None. A str, int, or other value is never None.
            same = left_kind == "none" and right_kind == "none"
            truth = same if op == "==" else not same
            reg = self.tmp()
            bit = "true" if truth else "false"
            self.line(f"{reg} = icmp eq i1 {bit}, true")
            return reg
        left = self.emit_expr(dict_to_dict(node.left))
        right = self.emit_expr(dict_to_dict(node.right))
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

    def _emit_none_side(self, node: Any) -> None:
        """Emit a call that returns None. A None constant or a None local is not a register."""
        if _type_kind_of(node) != "none":
            self.emit_expr(dict_to_dict(node))
            return
        if node.kind == "const" or node.kind == "name":
            return
        self.emit_expr(dict_to_dict(node))

    def emit_list(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        self.needed.add("seq")
        elem: dict[str, Any] = _type_dict_of(node)["elem"]
        values = [self.emit_expr(dict_to_dict(item)) for item in node.elems or []]
        reg = self._call_ret("ptr", "ps_seq_new", [f"i64 {_elem_size(elem)}", f"i64 {len(values)}"])
        for index, value in enumerate(values):
            slot = self._stash_cell(elem, value)
            self.line(f"call void @ps_seq_set(ptr {reg}, i64 {index}, ptr {slot})")
        return reg

    def emit_dict(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        self.needed.add("map")
        typ: dict[str, Any] = _type_dict_of(node)
        key_t: dict[str, Any] = typ["key"]
        val_t: dict[str, Any] = typ["value"]
        key_kind = 1 if key_t["kind"] == "int" else 2
        stored = [
            (self.emit_expr(dict_to_dict(pair.key)), self.emit_expr(dict_to_dict(pair.value)))
            for pair in node.pairs or []
        ]
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
            key = self._stash_cell(key_t, key_v)
            value = self._stash_cell(val_t, val_v)
            self.line(f"call void @ps_map_put(ptr {reg}, ptr {key}, ptr {value})")
        return reg

    def emit_subscript(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        value = self.emit_expr(dict_to_dict(node.value))
        index = self.emit_expr(dict_to_dict(node.index))
        vt: dict[str, Any] = _type_dict_of(node.value)
        kind: str = vt["kind"]
        out_t: dict[str, Any] = _type_dict_of(node)
        if kind == "list":
            slot: dict[str, Any] = vt["elem"]
            self.needed.add("seq")
            out = self._alloca(_memory_type(out_t))
            self.line(f"call void @ps_seq_get(ptr {value}, i64 {index}, ptr {out})")
            return self._widen_cell(out_t, out, slot if slot["kind"] == "any" else None)
        if kind == "dict":
            slot = vt["value"]
            self.needed.add("map")
            key = self._stash_cell(_type_dict_of(node.index), index)
            out = self._alloca(_memory_type(out_t))
            self.line(f"call void @ps_map_get(ptr {value}, ptr {key}, ptr {out})")
            return self._widen_cell(out_t, out, slot if slot["kind"] == "any" else None)
        if kind == "str":
            self.needed.add("str")
            length, ptr = self._buf_parts(value)
            self._abi_decl("ps_str_get", "void", ["i64", "ptr", "i64", "ptr"])
            return self._buf_out("ps_str_get", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])
        if kind == "bytes":
            self.needed.add("str")
            length, ptr = self._buf_parts(value)
            return self._call_ret("i64", "ps_buf_at", [f"i64 {length}", f"ptr {ptr}", f"i64 {index}"])
        if kind == "class" and vt.get("name") == "Deque":
            out = self._alloca(_memory_type(out_t))
            self._abi_decl("ps_deque_get", "void", ["ptr", "i64", "ptr"])
            self._call_void("ps_deque_get", [f"ptr {value}", f"i64 {index}", f"ptr {out}"])
            return self._widen_cell(out_t, out)
        raise IRError(f"cannot index {kind}")

    def emit_attr(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        obj = self.emit_expr(dict_to_dict(node.value))
        vt: dict[str, Any] = _type_dict_of(node.value)
        field = self._field(vt["name"], node.name)
        slot = self._field_ptr(vt["name"], obj, field)
        return self._widen_cell(_field_type(field), slot)

    def emit_contains(self, expr: dict[str, Any]) -> str:
        node: Any = dict_to_node(expr)
        needle = self.emit_expr(dict_to_dict(node.needle))
        hay = self.emit_expr(dict_to_dict(node.hay))
        ht: dict[str, Any] = _type_dict_of(node.hay)
        hay_kind: str = ht["kind"]
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
            elem: str = ht["elem"]["kind"]
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
            key = self._stash_cell(_type_dict_of(node.needle), needle)
            raw = self._call_ret("i32", "ps_map_has", [f"ptr {hay}", f"ptr {key}"])
            flag = self.tmp()
            self.line(f"{flag} = icmp ne i32 {raw}, 0")
        else:
            raise IRError(f"cannot emit containment for {hay_kind}")
        if not (node.flags & F_NEGATED):
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
        node: Any = dict_to_node(expr)
        if node.stub is not None:
            impl = self._partial_symbol(node_to_dict(node.stub))
        elif node.thread_stub is not None:
            impl = self._thread_symbol(node_to_dict(node.thread_stub))
        else:
            fn = _record(self.functions, node.name)
            fn_name = _fn_name(fn)
            fn_sym = _fn_symbol(fn_name)
            if _has(fn, "env"):
                impl = fn_sym
            else:
                impl = f"{fn_sym}.clo"
                self.clo_wrappers.add(fn_name)
        items: list[Any] = node.env or []
        if not items:
            env = "null"
        else:
            self.needed.add("alloc")
            env = self._call_ret("ptr", "ps_alloc", [f"i64 {8 * len(items)}"])
            for index, item in enumerate(items):
                if item.init is not None:
                    cell = self._heap_cell(_type_dict_of(item), self.emit_expr(dict_to_dict(item.init)))
                else:
                    cell = self.slots[item.name][0]
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
        node: Any = dict_to_node(expr)
        # A call with payload keys the typed record has no slot for (print's
        # `sep`/`end`, the island call's slots, sys.exit's `exitMessage`)
        # stays a plain dict here (tirserde keeps it whole so the round trip
        # is lossless); its callee then reads as a dict whose target key is
        # `target`, and its own type reads through the dict escape of
        # `_type_of_child`.
        callee: Any = node.callee
        if isinstance(callee, dict):
            target: Any = callee["target"]
        else:
            target = callee.target_node
        pair = self.emit_expr(dict_to_dict(target))
        impl = self._load("ptr", pair)
        env_ptr = self.tmp()
        self.line(f"{env_ptr} = getelementptr ptr, ptr {pair}, i64 1")
        env = self._load("ptr", env_ptr)
        args = [f"ptr {env}"]
        args.extend(
            f"{_llvm_type(_type_of_child(arg))} {value}" for arg, value in zip(node.args or [], values, strict=True)
        )
        ret = _llvm_type(_type_dict_of(node))
        if ret == "void":
            self.line(f"call void {impl}({', '.join(args)})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} {impl}({', '.join(args)})")
        return reg

    def call_function(self, fn: dict[str, Any], values: list[str]) -> str:
        ret = _llvm_type(_return_type(fn))
        rendered = ", ".join(
            f"{_llvm_type(_type_of(param))} {value}" for param, value in zip(_fn_params(fn), values, strict=True)
        )
        symbol = _fn_symbol(_fn_name(fn))
        if ret == "void":
            self.line(f"call void @{symbol}({rendered})")
            return ""
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @{symbol}({rendered})")
        return reg
