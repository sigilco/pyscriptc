"""The pyscriptc_ndarray emission (issue #496, M1).

An ndarray value is the pointer to the runtime's `PsNd` record. The
lowering hands the emitter the symbol of the runtime call and the shape
(the extents ride as a stack slot the runtime reads through a pointer), so
this module only has to render the call and the few reads that are not a
plain call: the attribute reads, the scalar reductions and the indexed
element reads.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _memory_type

# The dtype codes, the order the runtime's enum spells.
_DTYPE_CODES = {"int32": 0, "int64": 1, "float32": 2, "float64": 3}


def _nd_dtype_code(typ: dict[str, Any]) -> int:
    return _DTYPE_CODES.get(str(typ.get("dtype", "float64")), 3)


class NdArrayMixin:
    """The ndarray calls, as a mixin over the LLVM emitter."""

    def _nd_emit(self, expr: dict[str, Any], values: list[str]) -> str:
        """The module-level and method calls that are not a plain ABI call."""
        symbol = expr.get("symbol", "")
        name = expr["callee"]["name"]
        if symbol == "ps_nd_tobytes":
            return self._nd_tobytes(values)
        if symbol in {"ps_nd_sum", "ps_nd_min", "ps_nd_max", "ps_nd_mean", "ps_nd_std", "ps_nd_var"}:
            return self._nd_reduce(expr, values, symbol)
        if symbol in {"ps_nd_argmin", "ps_nd_argmax"}:
            return self._nd_argreduce(expr, values, symbol)
        if symbol == "ps_nd_tolist":
            return self._nd_tolist(expr, values)
        if symbol in {"ps_nd_ndim", "ps_nd_size", "ps_nd_nbytes"}:
            return self._nd_int_attr(symbol, values)
        if symbol == "ps_nd_dtype":
            return self._nd_dtype_attr(values)
        if symbol == "ps_nd_shape":
            return self._nd_shape(values)
        if symbol == "ps_nd_element":
            return self._nd_element(expr, values)
        if symbol == "ps_nd_element_i64":
            return self._nd_element_i64(expr, values)
        if symbol == "ps_nd_getitem":
            return self._nd_getitem(expr, values)
        if symbol == "ps_nd_boolean_index":
            return self._nd_plain(symbol, values, ["ptr", "ptr"])
        if symbol == "ps_nd_slice":
            return self._nd_plain(symbol, values, ["ptr", "i64", "i64", "i64"])
        if symbol == "ps_nd_reshape":
            return self._nd_reshape(expr, values)
        if symbol == "ps_nd_zeros" or symbol == "ps_nd_ones" or symbol == "ps_nd_full":
            return self._nd_filled(expr, values, symbol)
        if symbol == "ps_nd_from_seq":
            return self._nd_from_seq(expr, values)
        if symbol in {"ps_nd_arange", "ps_nd_linspace"}:
            return self._nd_plain(symbol, values, self._nd_plain_params(symbol))
        if symbol in {"ps_nd_concatenate", "ps_nd_stack"}:
            return self._nd_plain(symbol, values, ["ptr", "ptr"])
        if symbol in {"ps_nd_copy", "ps_nd_ravel", "ps_nd_astype", "ps_nd_transpose"}:
            return self._nd_plain(symbol, values, ["ptr", "i64"])
        if symbol in {"ps_nd_add", "ps_nd_sub", "ps_nd_mul", "ps_nd_div", "ps_nd_floordiv", "ps_nd_mod"}:
            return self._nd_plain(symbol, values, ["ptr", "ptr"])
        if symbol in {"ps_nd_eq", "ps_nd_ne", "ps_nd_lt", "ps_nd_le", "ps_nd_gt", "ps_nd_ge"}:
            return self._nd_plain(symbol, values, ["ptr", "ptr"])
        if symbol in {"ps_nd_add_s", "ps_nd_sub_s", "ps_nd_mul_s", "ps_nd_div_s",
                      "ps_nd_floordiv_s", "ps_nd_mod_s", "ps_nd_eq_s", "ps_nd_ne_s",
                      "ps_nd_lt_s", "ps_nd_le_s", "ps_nd_gt_s", "ps_nd_ge_s"}:
            return self._nd_plain(symbol, values, ["ptr", "double"])
        if symbol in {"ps_nd_add_f", "ps_nd_sub_f", "ps_nd_mul_f", "ps_nd_div_f",
                      "ps_nd_floordiv_f", "ps_nd_mod_f", "ps_nd_eq_f", "ps_nd_ne_f",
                      "ps_nd_lt_f", "ps_nd_le_f", "ps_nd_gt_f", "ps_nd_ge_f"}:
            return self._nd_plain(symbol, values, ["ptr", "double"])
        return self._nd_plain(symbol, values, ["ptr"])
        raise AssertionError("unreachable")

    def _nd_plain_params(self, symbol: str) -> list[str]:
        if symbol == "ps_nd_arange":
            return ["i64", "double", "double", "double"]
        return ["i64", "double", "double", "i64"]

    def _nd_plain(self, symbol: str, values: list[str], params: list[str]) -> str:
        """A call whose arguments map one to one onto the runtime params."""
        rendered: list[str] = []
        for value, param in zip(values, params, strict=False):
            rendered.append(f"{param} {value}")
        self._abi_decl(symbol, "ptr", params)
        return self._call_ret("ptr", symbol, rendered)

    def _nd_filled(self, expr: dict[str, Any], values: list[str], symbol: str) -> str:
        """`zeros`, `ones` and `full`: the shape rides as a stack slot the
        runtime reads through a pointer, after the dtype and the ndim."""
        ndim = int(expr.get("nd_shape", 0))
        slot = self._alloca(f"[{max(ndim, 1)} x i64]")
        for index in range(ndim):
            base = self.tmp()
            self.line(f"{base} = getelementptr [{max(ndim, 1)} x i64], ptr {slot}, i64 0, i64 {index}")
            self.line(f"store i64 {values[2 + index]}, ptr {base}")
        params = ["i64", "i64", "ptr"]
        rendered = [f"i64 {values[0]}", f"i64 {values[1]}", f"ptr {slot}"]
        if symbol == "ps_nd_full":
            params.append("double")
            rendered.append(f"double {values[2 + ndim]}")
        self._abi_decl(symbol, "ptr", params)
        return self._call_ret("ptr", symbol, rendered)

    def _nd_reshape(self, expr: dict[str, Any], values: list[str]) -> str:
        """`reshape`: the new shape rides as a stack slot."""
        ndim = int(expr.get("nd_shape", 0))
        slot = self._alloca(f"[{max(ndim, 1)} x i64]")
        for index in range(ndim):
            base = self.tmp()
            self.line(f"{base} = getelementptr [{max(ndim, 1)} x i64], ptr {slot}, i64 0, i64 {index}")
            self.line(f"store i64 {values[2 + index]}, ptr {base}")
        self._abi_decl("ps_nd_reshape", "ptr", ["ptr", "i64", "ptr"])
        return self._call_ret("ptr", "ps_nd_reshape", [f"ptr {values[0]}", f"i64 {values[1]}", f"ptr {slot}"])

    def _nd_from_seq(self, expr: dict[str, Any], values: list[str]) -> str:
        """`array(dtype, data)`: the data is a list, so the runtime reads it
        as a PsSeq."""
        self._abi_decl("ps_nd_from_seq", "ptr", ["i64", "ptr", "i64"])
        return self._call_ret("ptr", "ps_nd_from_seq", [f"i64 {values[0]}", f"ptr {values[1]}", f"i64 {values[2]}"])

    def _nd_tobytes(self, values: list[str]) -> str:
        """`tobytes()`: the raw buffer out through the buf ABI."""
        self._abi_decl("ps_nd_tobytes", "void", ["ptr", "ptr"])
        return self._buf_out("ps_nd_tobytes", [f"ptr {values[0]}"])

    def _nd_int_attr(self, symbol: str, values: list[str]) -> str:
        """`ndim`, `size` and `nbytes`: an int out of the record."""
        self._abi_decl(symbol, "i64", ["ptr"])
        return self._call_ret("i64", symbol, [f"ptr {values[0]}"])

    def _nd_dtype_attr(self, values: list[str]) -> str:
        """`dtype`: the dtype code, which the emitter turns into the name."""
        self._abi_decl("ps_nd_dtype", "i64", ["ptr"])
        return self._call_ret("i64", "ps_nd_dtype", [f"ptr {values[0]}"])

    def _nd_shape(self, values: list[str]) -> str:
        """`shape`: the extents as a list of ints."""
        self._abi_decl("ps_nd_shape", "ptr", ["ptr"])
        return self._call_ret("ptr", "ps_nd_shape", [f"ptr {values[0]}"])

    def _nd_reduce(self, expr: dict[str, Any], values: list[str], symbol: str) -> str:
        """The reductions: the runtime returns the reduced array, and the
        emitter reads the number out of it. A whole-array reduction is a
        zero-dimensional array, so the value is the single element."""
        self._abi_decl(symbol, "ptr", ["ptr", "i64", "i64"])
        arr = self._call_ret("ptr", symbol, [f"ptr {values[0]}", f"i64 {values[1]}", f"i64 {values[2]}"])
        dtype = str(expr["type"].get("dtype", "float64"))
        if dtype in {"int32", "int64"}:
            self._abi_decl("ps_nd_scalar_i64", "i64", ["ptr"])
            return self._call_ret("i64", "ps_nd_scalar_i64", [f"ptr {arr}"])
        self._abi_decl("ps_nd_scalar", "double", ["ptr"])
        return self._call_ret("double", "ps_nd_scalar", [f"ptr {arr}"])

    def _nd_argreduce(self, expr: dict[str, Any], values: list[str], symbol: str) -> str:
        """`argmin` and `argmax`: the index, as an int."""
        self._abi_decl(symbol, "ptr", ["ptr"])
        arr = self._call_ret("ptr", symbol, [f"ptr {values[0]}"])
        self._abi_decl("ps_nd_scalar_i64", "i64", ["ptr"])
        return self._call_ret("i64", "ps_nd_scalar_i64", [f"ptr {arr}"])

    def _nd_tolist(self, expr: dict[str, Any], values: list[str]) -> str:
        """`tolist()`: the nested list. The runtime builds the list itself
        (a PsSeq of 8-byte cells, or a list of row lists), and a list value
        is a `ptr` at the ABI, so the runtime's result is the result."""
        self._abi_decl("ps_nd_tolist", "ptr", ["ptr"])
        return self._call_ret("ptr", "ps_nd_tolist", [f"ptr {values[0]}"])

    def _nd_element(self, expr: dict[str, Any], values: list[str]) -> str:
        """`a[i]` on a float array: the element as a double."""
        self._abi_decl("ps_nd_element", "double", ["ptr", "i64"])
        return self._call_ret("double", "ps_nd_element", [f"ptr {values[0]}", f"i64 {values[1]}"])

    def _nd_element_i64(self, expr: dict[str, Any], values: list[str]) -> str:
        """`a[i]` on an integer array: the element as an int."""
        self._abi_decl("ps_nd_element_i64", "i64", ["ptr", "i64"])
        return self._call_ret("i64", "ps_nd_element_i64", [f"ptr {values[0]}", f"i64 {values[1]}"])

    def _nd_getitem(self, expr: dict[str, Any], values: list[str]) -> str:
        """`a[i]` as an array: the zero-dimensional result."""
        self._abi_decl("ps_nd_getitem", "ptr", ["ptr", "i64"])
        return self._call_ret("ptr", "ps_nd_getitem", [f"ptr {values[0]}", f"i64 {values[1]}"])
