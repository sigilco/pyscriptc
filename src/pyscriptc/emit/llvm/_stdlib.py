"""Standard-library call emission: the queued modules (`math`, `re`, `base64`,
`hashlib`, `urllib.parse`, `struct`, `json`) and the deep-copy path.
"""

from __future__ import annotations

from typing import Any

from pyscriptc._irnode import F_TOPT
from pyscriptc.emit._ir import (
    _llvm_type,
    _node_args,
    _type_of,
)
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import IRError
from pyscriptc.sigrec import sig_for
from pyscriptc.stdlib_queue import MATH_FUNCTIONS
from pyscriptc.tirserde import dict_to_type


class StdlibMixin(EmitterBase):
    def _t_elem_of(self, typ: Any) -> Any:
        """The element `T` record a list or optional type carries, or None.

        The kind tag read lives on `AbiMixin._t_kind` (the emitter is one
        class, so the mixins share the typed-record helpers); only the
        shape walks the JSON and copy tables need stay here.

        A dict with no kind tag is the kind-less wrapper (the FFI `T |
        None` type dict, which carries only `optional` and `elem`); its
        element is the dict's own `elem` key.
        """
        if isinstance(typ, dict) and "kind" not in typ:
            return typ.get("elem", None)
        record: Any = dict_to_type(typ)
        return record.elem

    def _t_map_value(self, typ: Any) -> Any:
        """The value `T` record of a dict type, or None."""
        record: Any = dict_to_type(typ)
        return record.value

    def _t_is_optional(self, typ: Any) -> bool:
        """Whether a type record spells the `X | None` optional bit.

        The `T` record carries the bit as the `F_TOPT` flag word; the
        kind-less wrapper spells it as the `optional` key.
        """
        if isinstance(typ, dict) and "kind" not in typ:
            return bool(typ.get("optional", False))
        record: Any = dict_to_type(typ)
        return bool(record.flags & F_TOPT)

    def _emit_math(self, name: str, values: list[str]) -> str:
        spec = sig_for(MATH_FUNCTIONS[name])
        if name == "isnan":
            reg = self.tmp()
            self.line(f"{reg} = fcmp uno double {values[0]}, 0.0")
            return reg
        if name == "isinf":
            abs_reg = self.tmp()
            self.line(f"{abs_reg} = call double @llvm.fabs.f64(double {values[0]})")
            self.needed.add("fabs")
            reg = self.tmp()
            self.line(f"{reg} = fcmp oeq double {abs_reg}, 0x7FF0000000000000")
            return reg
        if name == "isfinite":
            # `x` is finite when it is neither NaN (fcmp ord) nor at the
            # infinity magnitude (ordered |x| != +inf).
            nn = self.tmp()
            self.line(f"{nn} = fcmp ord double {values[0]}, 0.0")
            abs_reg = self.tmp()
            self.line(f"{abs_reg} = call double @llvm.fabs.f64(double {values[0]})")
            self.needed.add("fabs")
            ni = self.tmp()
            self.line(f"{ni} = fcmp one double {abs_reg}, 0x7FF0000000000000")
            reg = self.tmp()
            self.line(f"{reg} = and i1 {nn}, {ni}")
            return reg
        self.needed.add("math")
        return self._call_ret(spec["llvm"], spec["symbol"], [f"double {value}" for value in values])

    def _emit_math_int(self, symbol: str, values: list[str]) -> str:
        """The `_abi_call` math symbols (`factorial`, `lcm`): i64 params in
        and out, declared from the call shape, not the `MATH_FUNCTIONS`
        float table."""
        self.needed.add("math")
        self._abi_decl(symbol, "i64", ["i64"] * len(values))
        return self._call_ret("i64", symbol, [f"i64 {value}" for value in values])

    def _emit_re(self, name: str, values: list[str]) -> str:
        if name == "compile":
            return self._emit_pattern(values)
        if name == "escape":
            parts: tuple[str, str] = self._buf_parts(values[0])
            length = parts[0]
            ptr = parts[1]
            return self._buf_out("ps_re_escape", [f"i64 {length}", f"ptr {ptr}"])
        if name == "findall":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            text_len, text_ptr = self._buf_parts(values[1])
            return self._call_ret(
                "ptr",
                "ps_re_findall",
                [
                    f"i64 {pattern_len}",
                    f"ptr {pattern_ptr}",
                    f"i64 {text_len}",
                    f"ptr {text_ptr}",
                    f"i64 {values[2]}",
                ],
            )
        if name == "split":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            text_len, text_ptr = self._buf_parts(values[1])
            return self._call_ret(
                "ptr",
                "ps_re_split",
                [
                    f"i64 {pattern_len}",
                    f"ptr {pattern_ptr}",
                    f"i64 {text_len}",
                    f"ptr {text_ptr}",
                    f"i64 {values[2]}",
                    f"i64 {values[3]}",
                ],
            )
        if name == "sub":
            pattern_len, pattern_ptr = self._buf_parts(values[0])
            repl_len, repl_ptr = self._buf_parts(values[1])
            text_len, text_ptr = self._buf_parts(values[2])
            return self._buf_out(
                "ps_re_sub",
                [
                    f"i64 {pattern_len}",
                    f"ptr {pattern_ptr}",
                    f"i64 {repl_len}",
                    f"ptr {repl_ptr}",
                    f"i64 {text_len}",
                    f"ptr {text_ptr}",
                    f"i64 {values[3]}",
                    f"i64 {values[4]}",
                ],
            )
        raise IRError(f"no LLVM lowering for re.{name}")

    def _emit_pattern(self, values: list[str]) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.Pattern, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        slot = self.tmp()
        self.line(f"{slot} = getelementptr %cls.Pattern, ptr {mem}, i32 0, i32 0")
        length = self.tmp()
        ptr = self.tmp()
        self.line(f"{length} = extractvalue %ps.str {values[0]}, 0")
        self.line(f"{ptr} = extractvalue %ps.str {values[0]}, 1")
        len_ptr = self.tmp()
        data_ptr = self.tmp()
        self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
        self.line(f"store i64 {length}, ptr {len_ptr}")
        self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
        self.line(f"store ptr {ptr}, ptr {data_ptr}")
        flags = values[1] if len(values) > 1 else "0"
        fslot = self.tmp()
        self.line(f"{fslot} = getelementptr %cls.Pattern, ptr {mem}, i32 0, i32 1")
        self.line(f"store i64 {flags}, ptr {fslot}")
        return mem

    def _emit_b64(self, name: str, values: list[str]) -> str:
        symbols = {
            "b64encode": "ps_b64_std_encode",
            "standard_b64encode": "ps_b64_std_encode",
            "urlsafe_b64encode": "ps_b64_url_encode",
            "b64decode": "ps_b64_std_decode",
            "standard_b64decode": "ps_b64_std_decode",
            "urlsafe_b64decode": "ps_b64_url_decode",
        }
        parts: tuple[str, str] = self._buf_parts(values[0])
        length = parts[0]
        ptr = parts[1]
        return self._buf_out(symbols[name], [f"i64 {length}", f"ptr {ptr}"])

    def _emit_hash(self, name: str, values: list[str]) -> str:
        symbols = {
            "md5": "ps_hash_md5",
            "sha1": "ps_hash_sha1",
            "sha256": "ps_hash_sha256",
            "sha384": "ps_hash_sha384",
            "sha512": "ps_hash_sha512",
        }
        if name in symbols:
            length, ptr = self._buf_parts(values[0])
            return self._call_ret("ptr", symbols[name], [f"i64 {length}", f"ptr {ptr}"])
        if name == "hexdigest":
            return self._buf_out("ps_hash_hex", [f"ptr {values[0]}"])
        if name == "digest":
            return self._buf_out("ps_hash_digest", [f"ptr {values[0]}"])
        raise IRError(f"no LLVM lowering for hashlib.{name}")

    def _emit_url(self, name: str, values: list[str]) -> str:
        if name in {"quote", "quote_plus"}:
            text_len, text_ptr = self._buf_parts(values[0])
            safe_len, safe_ptr = self._buf_parts(values[1])
            symbol = "ps_url_quote" if name == "quote" else "ps_url_quote_plus"
            return self._buf_out(symbol, [f"i64 {text_len}", f"ptr {text_ptr}", f"i64 {safe_len}", f"ptr {safe_ptr}"])
        if name in {"unquote", "unquote_plus"}:
            length, ptr = self._buf_parts(values[0])
            symbol = "ps_url_unquote" if name == "unquote" else "ps_url_unquote_plus"
            return self._buf_out(symbol, [f"i64 {length}", f"ptr {ptr}"])
        if name == "urlencode":
            return self._buf_out("ps_url_encode", [f"ptr {values[0]}"])
        if name == "parse_qs":
            length, ptr = self._buf_parts(values[0])
            return self._call_ret("ptr", "ps_url_parse_qs", [f"i64 {length}", f"ptr {ptr}"])
        if name == "urlsplit":
            return self._emit_urlsplit(values[0])
        raise IRError(f"no LLVM lowering for urllib.parse.{name}")

    def _emit_urlsplit(self, value: str) -> str:
        self.needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.SplitResult, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        mem = self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])
        parts: tuple[str, str] = self._buf_parts(value)
        length = parts[0]
        ptr = parts[1]
        self.line(f"call void @ps_url_split(i64 {length}, ptr {ptr}, ptr {mem})")
        return mem

    def _emit_struct(self, name: str, values: list[str], args: list[dict[str, Any]], typ: dict[str, Any]) -> str:
        if name == "pack":
            return self._emit_struct_pack(values, args)
        elem = self._t_kind(self._t_elem_of(typ))
        want = {"int": 1, "float": 2, "bytes": 3}[elem]
        fmt_len, fmt_ptr = self._buf_parts(values[0])
        data_len, data_ptr = self._buf_parts(values[1])
        if name == "unpack_from":
            return self._call_ret(
                "ptr",
                "ps_struct_unpack_from",
                [
                    f"i64 {fmt_len}",
                    f"ptr {fmt_ptr}",
                    f"i64 {data_len}",
                    f"ptr {data_ptr}",
                    f"i64 {values[2]}",
                    f"i64 {values[3]}",
                    f"i32 {want}",
                ],
            )
        return self._call_ret(
            "ptr",
            "ps_struct_unpack",
            [f"i64 {fmt_len}", f"ptr {fmt_ptr}", f"i64 {data_len}", f"ptr {data_ptr}", f"i32 {want}"],
        )

    def _emit_struct_pack(self, values: list[str], args: list[dict[str, Any]]) -> str:
        fmt_len, fmt_ptr = self._buf_parts(values[0])
        count = len(args) - 1
        if count < 1:
            arg_ptr = "null"
            packed = 0
        else:
            self._ensure_arg_type()
            slot = self.tmp()
            self.line(f"{slot} = alloca [{count} x %ps.arg]")
            for index in range(count):
                value = values[index + 1]
                arg = args[index + 1]
                base = self.tmp()
                self.line(f"{base} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 {index}")
                self._store_ps_arg(base, value, _type_of(arg))
            arg_ptr = self.tmp()
            self.line(f"{arg_ptr} = getelementptr [{count} x %ps.arg], ptr {slot}, i64 0, i64 0")
            packed = count
        return self._buf_out("ps_struct_pack", [f"i64 {fmt_len}", f"ptr {fmt_ptr}", f"ptr {arg_ptr}", f"i64 {packed}"])

    def _emit_json_loads(self, text: str, typ: dict[str, Any], symbol_name: str = "ps_json_loads") -> str:
        if self._t_kind(typ) == "dynval":
            # The dynamic annotation (issue #1267): no shape table, the
            # parser assigns the arm as it reads. The result is the cell
            # pointer the type already carries.
            self._abi_decl("ps_json_loads_dyn", "void", ["i64", "ptr", "ptr"])
            length, ptr = self._buf_parts(text)
            slot = self._alloca("ptr")
            self.line(f"call void @ps_json_loads_dyn(i64 {length}, ptr {ptr}, ptr {slot})")
            return self._load("ptr", slot)
        symbol, root = self._json_global(typ)
        length, ptr = self._buf_parts(text)
        slot = self._json_alloca(typ)
        self.line(f"call void @{symbol_name}(i64 {length}, ptr {ptr}, ptr {symbol}, i32 {root}, ptr {slot})")
        return self._json_read(typ, slot)

    def _emit_json_dumps(self, value: str, typ: dict[str, Any], flags: int = 0) -> str:
        if self._t_kind(typ) == "dynval":
            # The dynamic value's dump walks the tagged tree (issue #1267);
            # the flag word is the typed writer's (sort keys, indent), so
            # the closed dumps subset rides the same way.
            if flags:
                self._abi_decl("ps_json_dumps_dyn_opts", "void", ["ptr", "i32", "ptr"])
                return self._buf_out("ps_json_dumps_dyn_opts", [f"ptr {value}", f"i32 {flags}"])
            self._abi_decl("ps_json_dumps_dyn", "void", ["ptr", "ptr"])
            return self._buf_out("ps_json_dumps_dyn", [f"ptr {value}"])
        symbol, root = self._json_global(typ)
        slot = self._json_write(typ, value)
        if flags:
            return self._buf_out("ps_json_dumps_opts", [f"ptr {symbol}", f"i32 {root}", f"ptr {slot}", f"i32 {flags}"])
        return self._buf_out("ps_json_dumps", [f"ptr {symbol}", f"i32 {root}", f"ptr {slot}"])

    def _json_global(self, typ: dict[str, Any]) -> tuple[str, int]:
        entries: list[tuple[int, int] | None] = []

        def add(node: dict[str, Any]) -> int:
            idx = len(entries)
            entries.append(None)
            kind = self._t_kind(node)
            if kind == "int":
                entries[idx] = (1, 0)
            elif kind == "bool":
                entries[idx] = (2, 0)
            elif kind == "str":
                entries[idx] = (3, 0)
            elif kind == "list":
                child = add(self._t_elem_of(node))
                entries[idx] = (4, child)
            elif kind == "dict":
                child = add(self._t_map_value(node))
                entries[idx] = (5, child)
            elif kind == "float":
                entries[idx] = (6, 0)
            elif kind == "dynval":
                # Issue #1267: a dynamic slot inside a typed shape (the
                # `dict[str, object]` value). The runtime cell already
                # carries the arm, so the table entry is the bare kind.
                entries[idx] = (8, 0)
            elif self._t_is_optional(node):
                child = add(self._t_elem_of(node))
                entries[idx] = (7, child)
            else:
                raise IRError(f"no JSON type for {kind}")
            return idx

        root = add(typ)
        parts: list[str] = []
        for index in range(len(entries)):
            entry = entries[index]
            if entry is None:
                continue
            kind = entry[0]
            child = entry[1]
            parts.append(f"%ps.json.ty {{ i32 {kind}, i32 {child} }}")
        typed = ", ".join(parts)
        symbol = f"@ps.json.{self.json_n}"
        self.json_n += 1
        if "%ps.json.ty = type { i32, i32 }" not in self.globals:
            self.globals.append("%ps.json.ty = type { i32, i32 }")
        self.globals.append(f"{symbol} = private constant [{len(entries)} x %ps.json.ty] [{typed}]")
        return symbol, root

    def _json_alloca(self, typ: dict[str, Any]) -> str:
        kind = self._t_kind(typ)
        if kind == "bool":
            return self._alloca("i32")
        if kind in {"list", "dict"}:
            return self._alloca("ptr")
        return self._alloca(_llvm_type(typ))

    def _json_read(self, typ: dict[str, Any], slot: str) -> str:
        kind = self._t_kind(typ)
        if kind == "bool":
            raw = self._load("i32", slot)
            reg = self.tmp()
            self.line(f"{reg} = trunc i32 {raw} to i1")
            return reg
        if kind in {"list", "dict"}:
            return self._load("ptr", slot)
        return self._load(_llvm_type(typ), slot)

    def _json_write(self, typ: dict[str, Any], value: str) -> str:
        slot = self._json_alloca(typ)
        kind = self._t_kind(typ)
        if kind == "bool":
            wide = self.tmp()
            self.line(f"{wide} = zext i1 {value} to i32")
            self.line(f"store i32 {wide}, ptr {slot}")
            return slot
        if kind in {"list", "dict"}:
            self.line(f"store ptr {value}, ptr {slot}")
            return slot
        self.line(f"store {_llvm_type(typ)} {value}, ptr {slot}")
        return slot

    def _emit_copy_deep(self, expr: dict[str, Any], values: list[str]) -> str:
        copy_args: list[dict[str, Any]] = _node_args(expr)
        symbol, root = self._copy_global(_type_of(copy_args[0]))
        self._abi_decl("ps_copy_deep", "ptr", ["ptr", "i32", "ptr"])
        return self._call_ret("ptr", "ps_copy_deep", [f"ptr {symbol}", f"i32 {root}", f"ptr {values[0]}"])

    def _copy_global(self, typ: dict[str, Any]) -> tuple[str, int]:
        entries: list[tuple[int, int] | None] = []

        def add(node: dict[str, Any]) -> int:
            idx = len(entries)
            entries.append(None)
            kind = self._t_kind(node)
            if kind == "int":
                entries[idx] = (1, 0)
            elif kind == "float":
                entries[idx] = (2, 0)
            elif kind == "str":
                entries[idx] = (3, 0)
            elif kind == "bytes":
                entries[idx] = (4, 0)
            elif kind == "list":
                child = add(self._t_elem_of(node))
                entries[idx] = (5, child)
            elif kind == "dict":
                child = add(self._t_map_value(node))
                entries[idx] = (6, child)
            else:
                raise IRError(f"no copy type for {kind}")
            return idx

        root = add(typ)
        parts: list[str] = []
        for index in range(len(entries)):
            entry = entries[index]
            if entry is None:
                continue
            kind = entry[0]
            child = entry[1]
            parts.append(f"%ps.copy.ty {{ i32 {kind}, i32 {child} }}")
        typed = ", ".join(parts)
        symbol = f"@ps.copy.{self.copy_n}"
        self.copy_n += 1
        if "%ps.copy.ty = type { i32, i32 }" not in self.globals:
            self.globals.append("%ps.copy.ty = type { i32, i32 }")
        self.globals.append(f"{symbol} = private constant [{len(entries)} x %ps.copy.ty] [{typed}]")
        return symbol, root
