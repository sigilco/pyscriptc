"""Runtime ABI emission: the `ps_*` call shapes, the class layout, the thread
and exit paths, and the `functools.partial` shims.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.emit._ir import _elem_size, _fn_symbol, _llvm_type, _memory_type
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.emit.llvm._runtime import _prebaked_declarations
from pyscriptc.errors import IRError
from pyscriptc.stdmore import COMPARE_CLASSES

# The symbols `_declarations` already emits when their `needed` group is
# active. A second declaration would be an invalid LLVM redefinition.
_PREBAKED_DECLS = list(_prebaked_declarations())


class AbiMixin(EmitterBase):
    def _ensure_arg_type(self) -> None:
        line = "%ps.arg = type { i32, i32, i64, double, %ps.buf }"
        globals_lines = self.globals
        if line not in globals_lines:
            globals_lines.append(line)

    def _store_ps_arg(self, base: str, value: str, typ: dict[str, Any]) -> None:
        kind: str = typ["kind"]
        code: int = {"int": 1, "float": 2}.get(kind, 0)
        fmt: bool = bool(typ.get("fmt", None))
        if code == 0:
            code = {"str": 3, "bool": 4, "bytes": 6, "dict": 7}.get(kind, 3) if fmt else 3
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
            value_type: dict[str, Any] = typ["value"]
            value_kind: str = value_type["kind"]
            vkind: int = {"int": 1, "float": 2, "str": 3, "bytes": 6}[value_kind]
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            self.line(f"store i64 {vkind}, ptr {slot}")
            b_ptr = self.tmp()
            self.line(f"{b_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 4")
            data = self.tmp()
            self.line(f"{data} = getelementptr %ps.buf, ptr {b_ptr}, i32 0, i32 1")
            self.line(f"store ptr {value}, ptr {data}")
            return
        slot = self.tmp()
        self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 4")
        if kind in {"str", "bytes"}:
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
            return
        self.line(f"store {_llvm_type(typ)} {value}, ptr {slot}")

    # boxed:begin
    # Island trampoline marshalling (issue #553). The kind codes match
    # `ps_island.c`; `pad` carries the element kind for a list.
    _ISLAND_ARG_CODES = {"int": 1, "float": 2, "str": 3, "bool": 4, "list": 5, "bytes": 6, "dict": 8}

    def _island_code(self, typ: dict[str, Any]) -> int:
        island: bool = bool(typ.get("island", None))
        if island:
            return 7
        codes: dict[str, int] = self._ISLAND_ARG_CODES
        kind: str = typ["kind"]
        return codes[kind]

    def _island_pad(self, want: dict[str, Any]) -> int:
        kind: str | None = want.get("kind", None)
        if kind == "list":
            return self._island_code(want["elem"])
        if kind == "dict":
            return (self._island_code(want["key"]) << 16) | self._island_code(want["value"])
        return 0

    def _store_island_arg(self, base: str, value: str, typ: dict[str, Any], starred: bool = False) -> None:
        code = self._island_code(typ) | (0x100 if starred else 0)
        kind_ptr = self.tmp()
        self.line(f"{kind_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 0")
        self.line(f"store i32 {code}, ptr {kind_ptr}")
        kind: str = typ["kind"]
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
        if kind == "list":
            pad_ptr = self.tmp()
            self.line(f"{pad_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 1")
            self.line(f"store i32 {self._island_code(typ['elem'])}, ptr {pad_ptr}")
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            raw = self.tmp()
            self.line(f"{raw} = ptrtoint ptr {value} to i64")
            self.line(f"store i64 {raw}, ptr {slot}")
            return
        if kind == "dict":
            pad_ptr = self.tmp()
            self.line(f"{pad_ptr} = getelementptr %ps.arg, ptr {base}, i32 0, i32 1")
            pad_val = (self._island_code(typ["key"]) << 16) | self._island_code(typ["value"])
            self.line(f"store i32 {pad_val}, ptr {pad_ptr}")
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            raw = self.tmp()
            self.line(f"{raw} = ptrtoint ptr {value} to i64")
            self.line(f"store i64 {raw}, ptr {slot}")
            return
        island: bool = bool(typ.get("island", None))
        if island:
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 2")
            raw = self.tmp()
            self.line(f"{raw} = ptrtoint ptr {value} to i64")
            self.line(f"store i64 {raw}, ptr {slot}")
            return
        if kind in {"str", "bytes"}:
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {base}, i32 0, i32 4")
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
            return
        raise IRError(f"cannot marshal {kind} to the island")

    def _island_name(self, expr: dict[str, Any]) -> tuple[int, str]:
        island: dict[str, Any] = expr["island"]
        name: str = island["name"]
        data = name.encode("utf-8")
        return len(data), self.format_global(f"island.{name}", data)

    def _prepare_island_call_args(
        self,
        pos_values: list[str],
        pos_args: list[dict[str, Any]],
        kw_values: list[str],
        kw_args: list[dict[str, Any]],
    ) -> tuple[str, str]:
        self._ensure_arg_type()
        npos = len(pos_values)
        if npos:
            slot = self.tmp()
            self.line(f"{slot} = alloca [{npos} x %ps.arg]")
            for index in range(npos):
                base = self.tmp()
                self.line(f"{base} = getelementptr [{npos} x %ps.arg], ptr {slot}, i64 0, i64 {index}")
                starred: bool = bool(pos_args[index].get("island_starred", None))
                self._store_island_arg(base, pos_values[index], pos_args[index]["type"], starred=starred)
            pos_ptr = self.tmp()
            self.line(f"{pos_ptr} = getelementptr [{npos} x %ps.arg], ptr {slot}, i64 0, i64 0")
        else:
            pos_ptr = "null"

        nkw_items = len(kw_values) // 2
        if nkw_items:
            total_kw = nkw_items * 2
            slot = self.tmp()
            self.line(f"{slot} = alloca [{total_kw} x %ps.arg]")
            for index in range(nkw_items):
                base_k = self.tmp()
                base_v = self.tmp()
                self.line(f"{base_k} = getelementptr [{total_kw} x %ps.arg], ptr {slot}, i64 0, i64 {2 * index}")
                self.line(f"{base_v} = getelementptr [{total_kw} x %ps.arg], ptr {slot}, i64 0, i64 {2 * index + 1}")
                k_arg = kw_args[2 * index]
                v_arg = kw_args[2 * index + 1]
                if k_arg.get("island_kwargs", None):
                    kind_ptr = self.tmp()
                    self.line(f"{kind_ptr} = getelementptr %ps.arg, ptr {base_k}, i32 0, i32 0")
                    self.line(f"store i32 0, ptr {kind_ptr}")
                else:
                    self._store_island_arg(base_k, kw_values[2 * index], k_arg["type"])
                self._store_island_arg(base_v, kw_values[2 * index + 1], v_arg["type"])
            kw_ptr = self.tmp()
            self.line(f"{kw_ptr} = getelementptr [{total_kw} x %ps.arg], ptr {slot}, i64 0, i64 0")
        else:
            kw_ptr = "null"
        return pos_ptr, kw_ptr

    def _emit_island_call(self, expr: dict[str, Any], values: list[str]) -> str:
        """One trampoline call: `island_call(handle, name, args...) -> handle or value`."""
        target = values[0]
        npos: int = expr.get("island_npos", len(values) - 1) or 0
        nkw: int = expr.get("island_nkw", None) or 0
        pos_values: list[str] = values[1 : 1 + npos]
        all_args: list[dict[str, Any]] = expr["args"]
        pos_args: list[dict[str, Any]] = all_args[1 : 1 + npos]
        kw_values: list[str] = values[1 + npos :]
        kw_args: list[dict[str, Any]] = all_args[1 + npos :]
        pos_ptr, kw_ptr = self._prepare_island_call_args(pos_values, pos_args, kw_values, kw_args)
        size, symbol = self._island_name(expr)
        type_island: bool = bool(expr["type"].get("island", None))
        if type_island:
            self._abi_decl("ps_island_call", "ptr", ["ptr", "i64", "ptr", "i64", "ptr", "i64", "ptr"])
            return self._call_ret(
                "ptr",
                "ps_island_call",
                [
                    f"ptr {target}",
                    f"i64 {size}",
                    f"ptr {symbol}",
                    f"i64 {npos}",
                    f"ptr {pos_ptr}",
                    f"i64 {nkw}",
                    f"ptr {kw_ptr}",
                ],
            )
        island: dict[str, Any] = expr["island"]
        want: dict[str, Any] = island["want"]
        code: int = self._island_code(want)
        pad = self._island_pad(want)
        out = self._alloca("%ps.arg")
        self._abi_decl(
            "ps_island_call_value",
            "void",
            ["ptr", "i64", "ptr", "i64", "ptr", "i64", "ptr", "i32", "i32", "ptr"],
        )
        self._call_void(
            "ps_island_call_value",
            [
                f"ptr {target}",
                f"i64 {size}",
                f"ptr {symbol}",
                f"i64 {npos}",
                f"ptr {pos_ptr}",
                f"i64 {nkw}",
                f"ptr {kw_ptr}",
                f"i32 {code}",
                f"i32 {pad}",
                f"ptr {out}",
            ],
        )
        return self._load_island_result(out, want)

    # boxed:begin
    def _emit_island_fn_call(self, expr: dict[str, Any], values: list[str]) -> str:
        """One call into the boxed island namespace (issue #612).

        `ps_island_fn_invoke(name, nargs, args, nkw, kws)` returns the boxed
        result as a handle; `ps_island_fn_invoke_value(name, nargs, args,
        nkw, kws, want, pad, &out)` unboxes through the same `want`
        mechanism as `ps_island_call_value`. The namespace is seeded once
        with the module's own source before `main` runs.
        """
        npos: int = expr.get("island_npos", len(values)) or 0
        nkw: int = expr.get("island_nkw", None) or 0
        pos_values: list[str] = values[:npos]
        all_args: list[dict[str, Any]] = expr["args"]
        pos_args: list[dict[str, Any]] = all_args[:npos]
        kw_values: list[str] = values[npos:]
        kw_args: list[dict[str, Any]] = all_args[npos:]
        pos_ptr, kw_ptr = self._prepare_island_call_args(pos_values, pos_args, kw_values, kw_args)
        fn: dict[str, Any] = expr["island_fn"]
        name: str = fn["name"]
        data = name.encode("utf-8")
        size = len(data)
        symbol = self.format_global(f"island.fn.{name}", data)
        want: dict[str, Any] | None = fn.get("want", None)
        if want is not None:
            code = self._island_code(want)
            pad = self._island_pad(want)
            out = self._alloca("%ps.arg")
            self._abi_decl(
                "ps_island_fn_invoke_value",
                "void",
                ["i64", "ptr", "i64", "ptr", "i64", "ptr", "i32", "i32", "ptr"],
            )
            self._call_void(
                "ps_island_fn_invoke_value",
                [
                    f"i64 {size}",
                    f"ptr {symbol}",
                    f"i64 {npos}",
                    f"ptr {pos_ptr}",
                    f"i64 {nkw}",
                    f"ptr {kw_ptr}",
                    f"i32 {code}",
                    f"i32 {pad}",
                    f"ptr {out}",
                ],
            )
            return self._load_island_result(out, want)
        self._abi_decl("ps_island_fn_invoke", "ptr", ["i64", "ptr", "i64", "ptr", "i64", "ptr"])
        return self._call_ret(
            "ptr",
            "ps_island_fn_invoke",
            [f"i64 {size}", f"ptr {symbol}", f"i64 {npos}", f"ptr {pos_ptr}", f"i64 {nkw}", f"ptr {kw_ptr}"],
        )

    def _emit_island_getitem(self, expr: dict[str, Any], values: list[str]) -> str:
        """One subscript into an island handle: `island_getitem(handle, key, want, &out)`."""
        self._ensure_arg_type()
        island: dict[str, Any] = expr["island"]
        want: dict[str, Any] = island["want"]
        code: int = self._island_code(want)
        pad = self._island_pad(want)
        key: str = island["key"]
        data = key.encode("utf-8")
        size = len(data)
        symbol = self.format_global(f"island.key.{data.hex()}", data)
        out = self._alloca("%ps.arg")
        self._abi_decl("ps_island_getitem", "void", ["ptr", "i64", "ptr", "i32", "i32", "ptr"])
        self._call_void(
            "ps_island_getitem",
            [f"ptr {values[0]}", f"i64 {size}", f"ptr {symbol}", f"i32 {code}", f"i32 {pad}", f"ptr {out}"],
        )
        return self._load_island_result(out, want)

    def _emit_island_seed(self, source: str) -> None:
        """The once-per-binary namespace seed: the module's own source text.

        Emitted at the top of `main` when the module carries an island
        plane; the trampoline execs the text with `__name__` closed so the
        script's main guard does not fire inside the island.
        """
        data = source.encode("utf-8")
        size = len(data)
        symbol = self.format_global("island.seed", data)
        self._abi_decl("ps_island_fn_seed", "void", ["i64", "ptr"])
        self._call_void("ps_island_fn_seed", [f"i64 {size}", f"ptr {symbol}"])

    def _emit_island_def_handle(self, expr: dict[str, Any]) -> str:
        """A handle to one object the island namespace defines (issue #628).

        `ps_island_def_handle(name)` returns a new reference to the object
        under that name, which is a class when a class definition was planned
        island-side. The class object crosses as an opaque handle: attribute
        reads go through `ps_island_getattr` and instantiations through
        `ps_island_fn_invoke`, the same path an island function result takes.
        """
        size, symbol = self._island_name(expr)
        self._abi_decl("ps_island_def_handle", "ptr", ["i64", "ptr"])
        return self._call_ret("ptr", "ps_island_def_handle", [f"i64 {size}", f"ptr {symbol}"])

    def _emit_island_seed_global(self) -> None:
        """The main-prologue seed, when the module plans island definitions."""
        source: str | None = self.module.get("islandSource", None)
        if source:
            self._emit_island_seed(source)

    # boxed:end

    def _emit_island_getattr(self, expr: dict[str, Any], values: list[str]) -> str:
        """One attribute read: `island_getattr(handle, name, want, &out)`."""
        self._ensure_arg_type()
        island: dict[str, Any] = expr["island"]
        want: dict[str, Any] = island["want"]
        code: int = self._island_code(want)
        pad = self._island_pad(want)
        size, symbol = self._island_name(expr)
        out = self._alloca("%ps.arg")
        self._abi_decl("ps_island_getattr", "void", ["ptr", "i64", "ptr", "i32", "i32", "ptr"])
        self._call_void(
            "ps_island_getattr",
            [f"ptr {values[0]}", f"i64 {size}", f"ptr {symbol}", f"i32 {code}", f"i32 {pad}", f"ptr {out}"],
        )
        return self._load_island_result(out, want)

    def _emit_island_unmarshal(self, expr: dict[str, Any], values: list[str]) -> str:
        """One unmarshal: `island_unmarshal(handle, want, pad, &out)`."""
        self._ensure_arg_type()
        island: dict[str, Any] = expr["island"]
        want: dict[str, Any] = island["want"]
        code: int = self._island_code(want)
        pad = self._island_pad(want)
        out = self._alloca("%ps.arg")
        self._abi_decl("ps_island_unmarshal", "void", ["ptr", "i32", "i32", "ptr"])
        self._call_void(
            "ps_island_unmarshal",
            [f"ptr {values[0]}", f"i32 {code}", f"i32 {pad}", f"ptr {out}"],
        )
        return self._load_island_result(out, want)

    def _load_island_result(self, out: str, want: dict[str, Any]) -> str:
        kind: str = want["kind"]
        if kind == "int":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 2")
            return self._load("i64", slot)
        if kind == "float":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 3")
            return self._load("double", slot)
        if kind == "bool":
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 2")
            wide = self._load("i64", slot)
            reg = self.tmp()
            self.line(f"{reg} = trunc i64 {wide} to i1")
            return reg
        needed = self.needed
        if kind == "list":
            needed.add("seq")
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 2")
            return self._load("ptr", slot)
        if kind == "dict":
            needed.add("map")
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 2")
            return self._load("ptr", slot)
        if kind in {"str", "bytes"}:
            buf = self.tmp()
            self.line(f"{buf} = getelementptr %ps.arg, ptr {out}, i32 0, i32 4")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {buf}, i32 0, i32 0")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {buf}, i32 0, i32 1")
            length = self._load("i64", len_ptr)
            data = self._load("ptr", data_ptr)
            str_slot = self._alloca("%ps.str")
            self.line(f"store i64 {length}, ptr {str_slot}")
            data_slot = self.tmp()
            self.line(f"{data_slot} = getelementptr %ps.str, ptr {str_slot}, i32 0, i32 1")
            self.line(f"store ptr {data}, ptr {data_slot}")
            count_ptr = self.tmp()
            flags_ptr = self.tmp()
            self.line(f"{count_ptr} = getelementptr %ps.str, ptr {str_slot}, i32 0, i32 2")
            self.line(f"{flags_ptr} = getelementptr %ps.str, ptr {str_slot}, i32 0, i32 3")
            self._abi_decl("ps_str_counts", "void", ["i64", "ptr", "ptr", "ptr"])
            self.line(f"call void @ps_str_counts(i64 {length}, ptr {data}, ptr {count_ptr}, ptr {flags_ptr})")
            return self._load("%ps.str", str_slot)
        raise IRError(f"cannot read an island value of kind {kind}")

    # boxed:end

    def _buf_out(self, symbol: str, args: list[str]) -> str:
        out = self._alloca("%ps.str")
        # The runtime writes the 16-byte PsBuf prefix (len, data). S2 fills
        # fields 2 and 3 after the call: a `ps_str_counts` scan derives the
        # code-point count and the ASCII flag from the bytes the call
        # produced. The scan is the price of not threading the header
        # through every runtime constructor; the constant path (_print.py)
        # computes both fields at emit time instead.
        call_args: list[str] = list(args)
        call_args.append(f"ptr {out}")
        self.line(f"call void @{symbol}({', '.join(call_args)})")
        count_ptr = self.tmp()
        flags_ptr = self.tmp()
        length = self.tmp()
        raw_ptr = self.tmp()
        data_ptr = self.tmp()
        self.line(f"{length} = load i64, ptr {out}")
        self.line(f"{raw_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 1")
        self.line(f"{data_ptr} = load ptr, ptr {raw_ptr}")
        self.line(f"{count_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 2")
        self.line(f"{flags_ptr} = getelementptr %ps.str, ptr {out}, i32 0, i32 3")
        self._abi_decl("ps_str_counts", "void", ["i64", "ptr", "ptr", "ptr"])
        self.line(f"call void @ps_str_counts(i64 {length}, ptr {data_ptr}, ptr {count_ptr}, ptr {flags_ptr})")
        return self._load("%ps.str", out)

    def _abi_decl(self, symbol: str, ret: str, params: list[str]) -> None:
        # A symbol already pre-baked in the `_declarations` table keeps that
        # spelling; re-declaring it here would be an invalid redefinition.
        if symbol in _PREBAKED_DECLS:
            return
        self.abi_decls[symbol] = (ret, params)

    def _abi_lines(self) -> list[str]:
        lines: list[str] = []
        for item in self.abi_decls.items():
            symbol = item[0]
            ret = item[1][0]
            params = item[1][1]
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
        needed = self.needed
        needed.add("alloc")
        size_ptr = self.tmp()
        size = self.tmp()
        self.line(f"{size_ptr} = getelementptr %cls.{name}, ptr null, i64 1")
        self.line(f"{size} = ptrtoint ptr {size_ptr} to i64")
        return self._call_ret("ptr", "ps_alloc", [f"i64 {size}"])

    def _c_pieces(self, value: str, typ: dict[str, Any]) -> list[tuple[str, str]]:
        kind: str = typ["kind"]
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
        symbol: str = expr["symbol"]
        rendered: list[str] = []
        types: list[str] = []
        arg_index = 0
        for value in values:
            all_args: list[dict[str, Any]] = expr["args"]
            arg: dict[str, Any] = all_args[arg_index]
            arg_index = arg_index + 1
            for piece, typ in self._c_pieces(value, arg["type"]):
                rendered.append(piece)
                types.append(typ)
        ret: dict[str, Any] = expr["type"]
        kind: str = ret["kind"]
        if kind in {"str", "bytes"}:
            abi_params: list[str] = list(types)
            abi_params.append("ptr")
            self._abi_decl(symbol, "void", abi_params)
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
        if kind in {"list", "dict", "func", "exc"} or (
            kind == "class"
            and (
                ret.get("name", None)
                in {
                    "Deque",
                    "TextIO",
                    "BytesIO",
                    "CsvWriter",
                    "Lock",
                    "Queue",
                    "HttpResponse",
                    "HttpRequest",
                    "AsyncLock",
                    "AsyncEvent",
                    "AsyncSemaphore",
                    "AsyncQueue",
                    "ThreadCall",
                }
                or symbol.startswith("ps_ffi_")
                # boxed:begin
                or symbol.startswith("ps_island_")
                # boxed:end
            )
        ):
            self._abi_decl(symbol, "ptr", types)
            return self._call_ret("ptr", symbol, rendered)
        if kind == "class":
            if symbol == "ps_global_get_path":
                # A Path module slot stores its bytes in the slot buffer
                # (`ps_global_set_path`); the read copies them out through the
                # (i64, ptr, ptr) shape every buf producer uses, not a record
                # pointer: the runtime writes into the caller's PsPath and
                # returns void, so a ptr-returning call reads a stale register
                # and str() of the slot dereferenced it (certifi.where()).
                out = self._alloca("%ps.buf")
                get_path_params: list[str] = list(types)
                get_path_params.append("ptr")
                self._abi_decl("ps_global_get_path", "void", get_path_params)
                get_path_args: list[str] = list(rendered)
                get_path_args.append(f"ptr {out}")
                self._call_void("ps_global_get_path", get_path_args)
                length = self.tmp()
                data = self.tmp()
                self.line(f"{length} = load i64, ptr {out}")
                self.line(f"{data} = getelementptr %ps.buf, ptr {out}, i32 0, i32 1")
                data_ptr = self.tmp()
                self.line(f"{data_ptr} = load ptr, ptr {data}")
                mem = self._alloc_class(ret["name"])
                self._abi_decl("ps_path_new", "void", ["i64", "ptr", "ptr"])
                self._call_void("ps_path_new", [f"i64 {length}", f"ptr {data_ptr}", f"ptr {mem}"])
                return mem
            if symbol in {"ps_global_get_ptr"} or symbol in {
                "ps_event_new",
                "ps_sem_new",
                "ps_task_new",
                "ps_alock_new",
                "ps_aevent_new",
                "ps_asem_new",
                "ps_aqueue_new",
                "ps_to_thread_new",
            }:
                # The runtime owns the record: the value is the record pointer.
                self._abi_decl(symbol, "ptr", types)
                return self._call_ret("ptr", symbol, rendered)
            tail_params: list[str] = list(types)
            tail_params.append("ptr")
            self._abi_decl(symbol, "void", tail_params)
            mem = self._alloc_class(ret["name"])
            tail_args: list[str] = list(rendered)
            tail_args.append(f"ptr {mem}")
            self._call_void(symbol, tail_args)
            return mem
        raise IRError(f"cannot emit {symbol}")

    def _emit_thread_new(self, expr: dict[str, Any]) -> str:
        """`Thread(...)`: build the target's callable pair (bound args inside
        the env), then hand it to the runtime with the name and daemon bits."""
        meta: dict[str, Any] = expr["thread"]
        pair = self.emit_closure(meta["closure"])
        self._abi_decl("ps_thread_new", "ptr", ["ptr", "i64", "ptr"])
        thread_name: str | None = meta.get("name", None)
        name = self.string_value(thread_name if thread_name is not None else "")
        name_len, name_ptr = self._buf_parts(name)
        daemon: int = meta.get("daemon", 0) or 0
        return self._call_ret("ptr", "ps_thread_new", [f"ptr {pair}", f"i64 {daemon}", f"ptr {name_ptr}"])

    def _emit_to_thread_new(self, expr: dict[str, Any], values: list[str]) -> str:
        """`asyncio.to_thread(fn, ...)`: the record that owns the result.

        The size is the result slot's size, so the runtime copies exactly
        the value's bytes. The callable pair and the worker start ride on
        the `ps_to_thread_start` call that follows.
        """
        meta: dict[str, Any] = expr["thread"]
        size: int = meta["size"]
        self._abi_decl("ps_to_thread_new", "ptr", ["i64"])
        return self._call_ret("ptr", "ps_to_thread_new", [f"i64 {size}"])

    def _emit_to_thread_start(self, expr: dict[str, Any], values: list[str]) -> str:
        """`ps_to_thread_start(record, pair)`: build the pair and start the
        worker.

        The pair carries the bound arguments, the inner callable and the
        record, so the worker stub finds the record and stores the result
        into it.
        """
        record = values[0]
        all_args: list[dict[str, Any]] = expr["args"]
        arg1: dict[str, Any] = all_args[1]
        pair = self.emit_closure(arg1)
        self._abi_decl("ps_to_thread_start", "void", ["ptr", "ptr"])
        self._call_void("ps_to_thread_start", [f"ptr {record}", f"ptr {pair}"])
        return ""

    def _emit_list_push(self, expr: dict[str, Any], values: list[str]) -> str:
        seq, elem = values
        all_args: list[dict[str, Any]] = expr["args"]
        arg1: dict[str, Any] = all_args[1]
        slot = self._stash_cell(arg1["type"], elem)
        self._abi_decl("ps_seq_push", "void", ["ptr", "ptr"])
        self._call_void("ps_seq_push", [f"ptr {seq}", f"ptr {slot}"])
        return ""

    def _emit_atexit(self, expr: dict[str, Any]) -> str:
        all_args: list[dict[str, Any]] = expr["args"]
        name: str = all_args[0]["value"]
        fn = self.functions.get(name)
        if fn is None:
            raise IRError(f"atexit.register target {name} was not lowered")
        symbol = _fn_symbol(name)
        wrap = f"ps.atexit.{self.atexit_n}"
        self.atexit_n = self.atexit_n + 1
        ret: dict[str, Any] = fn["returnType"]
        ret_kind: str = ret["kind"]
        if ret_kind == "none":
            body = f"  call void @{symbol}()\n  ret void\n"
        else:
            body = f"  call {_llvm_type(ret)} @{symbol}()\n  ret void\n"
        outline_lines = self.outline_lines
        outline_lines.append(f"define internal void @{wrap}() {{\nentry:\n{body}}}")
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
        self.terminated = 1
        return ""

    def _emit_exit_message(self, message: str) -> str:
        """`sys.exit(str)`: CPython raises `SystemExit(str)`, so the
        uncaught handler writes the message to stderr and exits 1, and an
        `except SystemExit` still catches it (issue #645). The SystemExit
        raise path carries both rules.
        """
        needed = self.needed
        needed.add("exc")
        self._abi_decl("ps_raise_msg", "void", ["i32", "i64", "ptr"])
        length, ptr = self._buf_parts(message)
        self.line(f"call void @ps_raise_msg(i32 {EXCEPTIONS['SystemExit']}, i64 {length}, ptr {ptr}) noreturn")
        self.line("unreachable")
        self.terminated = 1
        return ""

    def _emit_join(self, values: list[str]) -> str:
        count = len(values)
        slot = self.tmp()
        self.line(f"{slot} = alloca [{count} x %ps.buf]")
        for index, value in enumerate(values):
            dest = self.tmp()
            self.line(f"{dest} = getelementptr [{count} x %ps.buf], ptr {slot}, i64 0, i64 {index}")
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {dest}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {dest}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
        first = self.tmp()
        self.line(f"{first} = getelementptr [{count} x %ps.buf], ptr {slot}, i64 0, i64 0")
        self._abi_decl("ps_path_join", "void", ["ptr", "i64", "ptr"])
        return self._buf_out("ps_path_join", [f"ptr {first}", f"i64 {count}"])

    def _emit_deque_store(self, expr: dict[str, Any], values: list[str]) -> str:
        all_args: list[dict[str, Any]] = expr["args"]
        arg1: dict[str, Any] = all_args[1]
        slot = self._stash_cell(arg1["type"], values[1])
        symbol: str = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {slot}"])
        return ""

    def _emit_deque_load(self, expr: dict[str, Any], values: list[str]) -> str:
        out = self._alloca(_memory_type(expr["type"]))
        symbol: str = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {out}"])
        return self._widen_cell(expr["type"], out)

    def _emit_class_cmp(self, expr: dict[str, Any], left: str, right: str) -> str:
        left_arg: dict[str, Any] = expr["left"]
        left_type: dict[str, Any] = left_arg["type"]
        name: str = left_type["name"]
        op: str = expr["op"]
        if name == "Path":
            left_ptr = self.tmp()
            right_ptr = self.tmp()
            self.line(f"{left_ptr} = getelementptr %cls.Path, ptr {left}, i32 0, i32 0")
            self.line(f"{right_ptr} = getelementptr %cls.Path, ptr {right}, i32 0, i32 0")
            cmp = self._buf_cmp_cells(self._load("%ps.buf", left_ptr), self._load("%ps.buf", right_ptr))
        elif name in COMPARE_CLASSES:
            fields: list[dict[str, Any]] = self.class_index[name]["fields"]
            count: int = len(fields)
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
        left_arg: dict[str, Any] = expr["left"]
        left_type: dict[str, Any] = left_arg["type"]
        left_name: str = left_type["name"]
        right_arg: dict[str, Any] = expr["right"]
        right_type: dict[str, Any] = right_arg["type"]
        right_name: str = right_type["name"]
        out_type: dict[str, Any] = expr["type"]
        out_name: str = out_type["name"]
        mem = self._alloc_class(out_name)
        op: str = expr["op"]
        sign: str = "1" if op == "+" else "-1"
        if left_name in {"Date", "DateTime"} and right_name == "TimeDelta":
            symbol = "ps_date_add" if left_name == "Date" else "ps_dt_add"
            self._abi_decl(symbol, "void", ["ptr", "ptr", "i32", "ptr"])
            self._call_void(symbol, [f"ptr {left}", f"ptr {right}", f"i32 {sign}", f"ptr {mem}"])
            return mem
        if left_name == "TimeDelta" and right_name == "TimeDelta":
            self._abi_decl("ps_delta_add", "void", ["ptr", "ptr", "i32", "ptr"])
            self._call_void("ps_delta_add", [f"ptr {left}", f"ptr {right}", f"i32 {sign}", f"ptr {mem}"])
            return mem
        if op == "-" and left_name == right_name and left_name in {"Date", "DateTime"}:
            symbol = "ps_date_diff" if left_name == "Date" else "ps_dt_diff"
            self._abi_decl(symbol, "void", ["ptr", "ptr", "ptr"])
            self._call_void(symbol, [f"ptr {left}", f"ptr {right}", f"ptr {mem}"])
            return mem
        raise IRError(f"cannot emit {op} on {left_name}")

    def _emit_minmax(self, name: str, values: list[str], arg_types: list[dict[str, Any]]) -> str:
        kind: str = arg_types[0]["kind"]
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
        all_args: list[dict[str, Any]] = expr["args"]
        arg0: dict[str, Any] = all_args[0]
        seq = self.emit_expr(arg0)
        arg0_type: dict[str, Any] = arg0["type"]
        elem: dict[str, Any] = arg0_type["elem"]
        llvm_t: str = _llvm_type(elem)
        needed = self.needed
        needed.add("seq")
        length = self._call_ret("i64", "ps_seq_len", [f"ptr {seq}"])
        acc_slot = self._alloca(llvm_t)
        idx_slot = self._alloca("i64")
        if len(all_args) == 2:
            arg1: dict[str, Any] = all_args[1]
            init = self.emit_expr(arg1)
            self.line(f"store {llvm_t} {init}, ptr {acc_slot}")
            self.line(f"store i64 0, ptr {idx_slot}")
        else:
            empty = self.tmp()
            self.line(f"{empty} = icmp eq i64 {length}, 0")
            bad = self.fresh("red.empty")
            ok = self.fresh("red.start")
            self.line(f"br i1 {empty}, label %{bad}, label %{ok}")
            self.terminated = 1
            self.block(bad)
            needed = self.needed
            needed.add("exc")
            self.line(f"call void @ps_raise_code(i32 {EXCEPTIONS['ValueError']}) noreturn")
            self.line("unreachable")
            self.terminated = 1
            self.block(ok)
            first = self._seq_at(seq, "0", elem)
            self.line(f"store {llvm_t} {first}, ptr {acc_slot}")
            self.line(f"store i64 1, ptr {idx_slot}")
        cond_l = self.fresh("red.cond")
        body_l = self.fresh("red.body")
        end_l = self.fresh("red.end")
        self.line(f"br label %{cond_l}")
        self.terminated = 1
        self.block(cond_l)
        idx = self._load("i64", idx_slot)
        cmp = self.tmp()
        self.line(f"{cmp} = icmp slt i64 {idx}, {length}")
        self.line(f"br i1 {cmp}, label %{body_l}, label %{end_l}")
        self.terminated = 1
        self.block(body_l)
        current = self._load(llvm_t, acc_slot)
        item = self._seq_at(seq, idx, elem)
        fn_name: str = expr["fn"]
        fn: dict[str, Any] = self.functions[fn_name]
        nxt = self.call_function(fn, [current, item])
        self.line(f"store {llvm_t} {nxt}, ptr {acc_slot}")
        bumped = self.tmp()
        self.line(f"{bumped} = add i64 {idx}, 1")
        self.line(f"store i64 {bumped}, ptr {idx_slot}")
        self.line(f"br label %{cond_l}")
        self.terminated = 1
        self.block(end_l)
        return self._load(llvm_t, acc_slot)

    def _seq_at(self, seq: str, index: str, elem: dict[str, Any]) -> str:
        needed = self.needed
        needed.add("seq")
        out = self._alloca(_memory_type(elem))
        self.line(f"call void @ps_seq_get(ptr {seq}, i64 {index}, ptr {out})")
        return self._widen_cell(elem, out)

    def _emit_partial_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        """`pa.partial.N(env, p...)`: load the bound arguments and the inner
        pair out of the env, then call the inner impl env-first with the
        bound arguments prepended."""
        bound: list[dict[str, Any]] = spec["bound"]
        params: list[dict[str, Any]] = spec["params"]
        spec_return: dict[str, Any] = spec["return"]
        ret = _llvm_type(spec_return)
        plist: list[str] = ["ptr %env"]
        for index in range(len(params)):
            plist.append(f"{_llvm_type(params[index])} %p{index}")
        out_lines = self.out
        out_lines.append(f"define internal {ret} @{symbol}({', '.join(plist)}) {{")
        out_lines.append("entry:")
        args: list[str] = []
        for index in range(len(bound)):
            typ: dict[str, Any] = bound[index]
            if index == 0:
                out_lines.append(f"  %b{index} = load ptr, ptr %env")
            else:
                out_lines.append(f"  %g{index} = getelementptr ptr, ptr %env, i64 {index}")
                out_lines.append(f"  %b{index} = load ptr, ptr %g{index}")
            out_lines.append(f"  %v{index} = load {_llvm_type(typ)}, ptr %b{index}")
            args.append(f"{_llvm_type(typ)} %v{index}")
        out_lines.append(f"  %ip = getelementptr ptr, ptr %env, i64 {len(bound)}")
        out_lines.append("  %pc = load ptr, ptr %ip")
        out_lines.append("  %pp = load ptr, ptr %pc")
        out_lines.append("  %impl = load ptr, ptr %pp")
        out_lines.append("  %ip2 = getelementptr ptr, ptr %pp, i64 1")
        out_lines.append("  %env2 = load ptr, ptr %ip2")
        call_args: list[str] = ["ptr %env2"]
        call_args.extend(args)
        for index in range(len(params)):
            call_args.append(f"{_llvm_type(params[index])} %p{index}")
        if ret == "void":
            out_lines.append(f"  call void %impl({', '.join(call_args)})")
            out_lines.append("  ret void")
        else:
            out_lines.append(f"  %r = call {ret} %impl({', '.join(call_args)})")
            out_lines.append(f"  ret {ret} %r")
        out_lines.append("}")
        out_lines.append("")

    def _partial_symbol(self, spec: dict[str, Any]) -> str:
        spec_bound: list[dict[str, Any]] = spec["bound"]
        spec_params: list[dict[str, Any]] = spec["params"]
        spec_return: dict[str, Any] = spec["return"]
        key: str = repr((spec_bound, spec_params, spec_return["kind"]))
        symbol: str | None = self.partial_keys.get(key, None)
        if symbol is None:
            symbol = f"pa.partial.{self.partial_n}"
            self.partial_n = self.partial_n + 1
            self.partial_keys[key] = symbol
            self.partials[symbol] = spec
        return symbol

    def _thread_symbol(self, spec: dict[str, Any]) -> str:
        """The env-first stub a `to_thread` worker runs.

        The stub is the partial shim with one more job: it stores the
        callable's return value into the thread record, so the loop can
        hand it back to the awaiting frame.
        """
        spec_bound: list[dict[str, Any]] = spec["bound"]
        spec_params: list[dict[str, Any]] = spec["params"]
        spec_return: dict[str, Any] = spec["return"]
        key: str = repr((spec_bound, spec_params, spec_return["kind"]))
        symbol: str | None = self.thread_keys.get(key, None)
        if symbol is None:
            symbol = f"ps.thread.{self.thread_n}"
            self.thread_n = self.thread_n + 1
            self.thread_keys[key] = symbol
            self.threads[symbol] = spec
        return symbol

    def _emit_thread_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        """`ps.thread.N(env, p...)`: the partial shim plus the result store.

        The env carries the bound arguments, the inner pair and the thread
        record. The stub calls the inner impl env-first with the bound
        arguments prepended, then copies the result into the record.
        """
        bound: list[dict[str, Any]] = spec["bound"]
        params: list[dict[str, Any]] = spec["params"]
        spec_return: dict[str, Any] = spec["return"]
        ret = _llvm_type(spec_return)
        plist: list[str] = ["ptr %env"]
        for index in range(len(params)):
            plist.append(f"{_llvm_type(params[index])} %p{index}")
        # The stub is emitted outside the abi machinery, so it declares the
        # runtime call it makes itself.
        self._abi_decl("ps_to_thread_store", "void", ["ptr", "ptr", "i64"])
        out_lines = self.out
        out_lines.append(f"define internal {ret} @{symbol}({', '.join(plist)}) {{")
        out_lines.append("entry:")
        args: list[str] = []
        for index in range(len(bound)):
            typ: dict[str, Any] = bound[index]
            if index == 0:
                out_lines.append("  %b0 = load ptr, ptr %env")
            else:
                out_lines.append(f"  %g{index} = getelementptr ptr, ptr %env, i64 {index}")
                out_lines.append(f"  %b{index} = load ptr, ptr %g{index}")
            out_lines.append(f"  %v{index} = load {_llvm_type(typ)}, ptr %b{index}")
            args.append(f"{_llvm_type(typ)} %v{index}")
        # The inner pair sits after the bound arguments.
        out_lines.append(f"  %ip = getelementptr ptr, ptr %env, i64 {len(bound)}")
        out_lines.append("  %pc = load ptr, ptr %ip")
        out_lines.append("  %pp = load ptr, ptr %pc")
        out_lines.append("  %impl = load ptr, ptr %pp")
        out_lines.append("  %ip2 = getelementptr ptr, ptr %pp, i64 1")
        out_lines.append("  %env2 = load ptr, ptr %ip2")
        # The thread record sits after the inner pair. The env entry packs
        # the record into a heap cell, so the stub loads it out of the cell.
        out_lines.append(f"  %ip3 = getelementptr ptr, ptr %env, i64 {len(bound) + 1}")
        out_lines.append("  %reccell = load ptr, ptr %ip3")
        out_lines.append("  %rec = load ptr, ptr %reccell")
        call_args: list[str] = ["ptr %env2"]
        call_args.extend(args)
        for index in range(len(params)):
            call_args.append(f"{_llvm_type(params[index])} %p{index}")
        if ret == "void":
            out_lines.append(f"  call void %impl({', '.join(call_args)})")
            out_lines.append("  ret void")
        else:
            out_lines.append(f"  %r = call {ret} %impl({', '.join(call_args)})")
            # The result travels into the record: the worker stores it, the
            # loop hands it back to the awaiting frame. A str result narrows
            # to the 16-byte prefix the record cell holds.
            spec_return_kind: str = spec_return["kind"]
            if spec_return_kind in {"str", "bytes"}:
                out_lines.append("  %slot = alloca %ps.buf")
                out_lines.append("  %rn = extractvalue %ps.str %r, 0")
                out_lines.append("  %rp = extractvalue %ps.str %r, 1")
                out_lines.append("  %slen = getelementptr %ps.buf, ptr %slot, i32 0, i32 0")
                out_lines.append("  store i64 %rn, ptr %slen")
                out_lines.append("  %sdata = getelementptr %ps.buf, ptr %slot, i32 0, i32 1")
                out_lines.append("  store ptr %rp, ptr %sdata")
            else:
                out_lines.append(f"  %slot = alloca {ret}")
                out_lines.append(f"  store {ret} %r, ptr %slot")
            out_lines.append(f"  call void @ps_to_thread_store(ptr %rec, ptr %slot, i64 {_elem_size(spec['return'])})")
            out_lines.append(f"  ret {ret} %r")
        out_lines.append("}")
        out_lines.append("")

    def _heap_cell(self, typ: dict[str, Any], value: str) -> str:
        needed = self.needed
        needed.add("alloc")
        cell = self._call_ret("ptr", "ps_alloc", [f"i64 {_elem_size(typ)}"])
        kind: str = typ["kind"]
        if kind in {"str", "bytes"}:
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {cell}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {cell}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
            return cell
        self.line(f"store {_llvm_type(typ)} {value}, ptr {cell}")
        return cell

    def _emit_task_result(self, expr: dict[str, Any], values: list[str]) -> str:
        """`ps_task_result(task, size)`: read the coroutine's result field.

        The result lives at offset 16 in the task's frame (field 0
        `.ps_state`, field 1 `.ps_task`, the layout coro.py guarantees); the
        size is the element size the awaited type carries. The read goes
        straight out of the frame, so the task record copies nothing.

        A failed task has no result: the read is preceded by the failure
        check (`ps_task_failure` re-raises the record's exception), so
        `task.result()` on a failed task raises, the way CPython's does.
        """
        all_args: list[dict[str, Any]] = expr["args"]
        arg0: dict[str, Any] = all_args[0]
        arg0_type: dict[str, Any] = arg0["type"]
        elem: dict[str, Any] = arg0_type["elem"]
        out = self._alloca(_memory_type(elem))
        self._abi_decl("ps_task_failure", "void", ["ptr"])
        self._call_void("ps_task_failure", [f"ptr {values[0]}"])
        symbol: str = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "i64", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
        return self._widen_cell(elem, out)
