"""Printing: the value formatting and the `print` calls."""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _llvm_bytes, _llvm_double, _llvm_type
from pyscriptc.errors import FitError, IRError


class PrintMixin:
    def helper_call(self, name: str, ret: str, args: list[str], arg_type: str = "i64") -> str:
        self.needed.add(name)
        rendered = ", ".join(f"{arg_type} {arg}" for arg in args)
        reg = self.tmp()
        self.line(f"{reg} = call {ret} @ps.{name}({rendered})")
        return reg

    def string_value(self, text: str, binary: bool = False) -> str:
        data = text.encode("latin1")
        idx = len(self.string_data)
        self.string_data.append(data)
        symbol = f"@ps.str.{idx}"
        self.globals.append(f'{symbol} = private unnamed_addr constant [{len(data) + 1} x i8] c"{_llvm_bytes(data)}"')
        # The compiler knows the code-point count and the ASCII flag from
        # the source text, so the constant path fills all four fields. In
        # the Latin-1 model the byte length is the count only for ASCII, so
        # the non-ASCII case carries len(text) as the count already.
        count = len(text)
        ascii_flag = 1 if count == len(data) else 0
        parts = self.tmp()
        first = self.tmp()
        second = self.tmp()
        third = self.tmp()
        self.line(f"{parts} = insertvalue %ps.str poison, i64 {len(data)}, 0")
        self.line(f"{first} = insertvalue %ps.str {parts}, ptr {symbol}, 1")
        self.line(f"{second} = insertvalue %ps.str {first}, i64 {count}, 2")
        self.line(f"{third} = insertvalue %ps.str {second}, i32 {ascii_flag}, 3")
        return third

    def materialize_arg(self, typ: dict[str, Any], value: Any) -> str:
        kind = typ["kind"]
        if kind == "int":
            return str(value)
        if kind == "float":
            return _llvm_double(value)
        if kind == "bool":
            return "true" if value else "false"
        if kind == "str":
            return self.string_value(value)
        raise FitError(f"cannot pass a {kind} argument into main")

    def print_value(self, typ: dict[str, Any], value: str) -> None:
        kind = typ["kind"]
        self.needed.add("printf")
        if kind == "int":
            fmt = self.format_global("int", b"%lld\n")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, i64 {value})")
            return
        if kind == "float":
            fmt = self.format_global("float", b"%.17g\n")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, double {value})")
            return
        if kind == "str":
            fmt = self.format_global("str", b"%.*s\n")
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            self.line(f"call i32 (ptr, ...) @printf(ptr {fmt}, i64 {length}, ptr {ptr})")
            return
        raise IRError(f"cannot print {kind}")

    def _emit_print(self, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> None:
        """`print` with CPython defaults: space separator, newline terminator.

        `sep=`/`end=` str arguments ride on the call dict; when absent the
        constants hold. Each value writes through `printf`, mirroring
        `print_value`'s int and str pieces. Floats go through `ps_f64_str` so
        the text is the CPython `str(float)` spelling (shortest round-trip,
        `.0`, `1e+16`), which a raw `%g` conversion cannot reproduce. Bools
        select between the `True` and `False` constants, not the runtime's
        lowercase literals. Bytes print as their `b'...'` repr.
        """
        self.needed.add("printf")
        err = expr.get("file") == "stderr"
        sep = self.emit_expr(expr["sep"]) if expr.get("sep") else None
        end = self.emit_expr(expr["end"]) if expr.get("end") else None
        for index, (value, typ) in enumerate(zip(values, arg_types, strict=True)):
            if index:
                if sep is None:
                    self._print_fmt("space", b" ", err=err)
                else:
                    slen, sptr = self._buf_parts(sep)
                    self._print_fmt("sep", b"%.*s", [f"i64 {slen}", f"ptr {sptr}"], err=err)
            self._print_one(typ, value, err)
        if end is None:
            self._print_fmt("nl", b"\n", err=err)
        else:
            elen, eptr = self._buf_parts(end)
            self._print_fmt("end", b"%.*s", [f"i64 {elen}", f"ptr {eptr}"], err=err)

    def _print_fmt(self, name: str, data: bytes, args: list[str] | None = None, err: bool = False) -> None:
        fmt = self.format_global(name, data)
        call_args = list(args or [])
        if self.entry_symbol == "__main_argc_argv":
            # wasm32 varargs are 32-bit: wasi-libc's printf reads a `%.*s`
            # precision through an i32, and an i64 there prints `(null)`.
            # `%lld` with i64 is fine; this truncates only `%.*s` lengths.
            call_args = [
                self._trunc_i32(arg) if arg.startswith("i64 ") and data.startswith(b"%.*s") else arg
                for arg in call_args
            ]
        if err:
            self.needed.add("fprintf")
            fd = self.tmp()
            # A helper, not `@stderr`: on Darwin `stderr` is a macro for
            # `__stderrp`, so the raw symbol never resolves at link time.
            self.line(f"{fd} = call ptr @ps_stderr_stream()")
            rendered = ", ".join([f"ptr {fd}", f"ptr {fmt}", *call_args])
            self.line(f"call i32 (ptr, ptr, ...) @fprintf({rendered})")
            return
        rendered = ", ".join([f"ptr {fmt}", *call_args])
        self.line(f"call i32 (ptr, ...) @printf({rendered})")

    def _trunc_i32(self, arg: str) -> str:
        reg = self.tmp()
        self.line(f"{reg} = trunc i64 {arg[4:]} to i32")
        return f"i32 {reg}"

    def _print_one(self, typ: dict[str, Any], value: str, err: bool) -> None:
        """One print argument: scalars spell directly, a tuple class recurses.

        Each branch here is a field of `_emit_print`'s loop; the tuple case
        writes CPython's `(a, b)` shape field by field.
        """

        kind = typ["kind"]
        if kind == "int":
            self._print_fmt("int", b"%lld", [f"i64 {value}"], err=err)
        elif kind == "float":
            out = self._buf_out("ps_f64_str", [f"double {value}"])
            length, ptr = self._buf_parts(out)
            self._abi_decl("ps_f64_str", "void", ["double", "ptr"])
            self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
        elif kind == "bool":
            picked = self.tmp()
            self.line(
                f"{picked} = select i1 {value}, ptr {self.format_global('boolT', b'True')}, "
                f"ptr {self.format_global('boolF', b'False')}"
            )
            self._print_fmt("s", b"%s", [f"ptr {picked}"], err=err)
        elif kind == "bytes":
            self._abi_decl("ps_repr_bytes", "void", ["i64", "ptr", "ptr"])
            rlen, rptr = self._buf_parts(value)
            out = self._buf_out("ps_repr_bytes", [f"i64 {rlen}", f"ptr {rptr}"])
            length, ptr = self._buf_parts(out)
            self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
        elif kind == "exc":
            self._abi_decl("ps_exc_str", "void", ["ptr", "ptr"])
            out = self._buf_out("ps_exc_str", [f"ptr {value}"])
            length, ptr = self._buf_parts(out)
            self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)
        elif kind == "class" and self._is_tuple(typ["name"]):
            self._print_tuple(typ["name"], value, err)
        else:
            length, ptr = self._buf_parts(value)
            self._print_fmt("str", b"%.*s", [f"i64 {length}", f"ptr {ptr}"], err=err)

    def _is_tuple(self, name: str) -> bool:
        """Tuple classes the lowerer synthesizes: `tuple_N_*` over fields _0.._n."""

        info = self.class_index.get(name)
        if info is None or not name.startswith("tuple_"):
            return False
        return all(field["name"] == f"_{index}" for index, field in enumerate(info["fields"]))

    def _print_tuple(self, name: str, value: str, err: bool) -> None:
        """A synthetic tuple prints `(a, b)`; a lone field keeps the `,` tail."""

        fields = self.class_index[name]["fields"]
        self._print_fmt("lparen", b"(", err=err)
        for index, field in enumerate(fields):
            if index:
                self._print_fmt("tsep", b", ", err=err)
            loaded = self._load(
                _llvm_type(field["type"]), self._field_ptr(name, value, self._field(name, field["name"]))
            )
            self._print_one(field["type"], loaded, err)
        if len(fields) == 1:
            self._print_fmt("tsep", b",", err=err)
        self._print_fmt("rparen", b")", err=err)

    def format_global(self, name: str, data: bytes) -> str:
        base = f"@ps.fmt.{name}"
        body = f'private unnamed_addr constant [{len(data) + 1} x i8] c"{_llvm_bytes(data)}"'
        defined = {g.split(" = ", 1)[0] for g in self.globals}
        symbol = base
        suffix = 0
        while symbol in defined and f"{symbol} = {body}" not in self.globals:
            suffix += 1
            symbol = f"{base}.{suffix}"
        if symbol not in defined:
            self.globals.append(f"{symbol} = {body}")
        return symbol
