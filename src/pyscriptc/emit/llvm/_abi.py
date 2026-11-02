"""Runtime ABI emission: the `ps_*` call shapes, the class layout, the thread
and exit paths, and the `functools.partial` shims.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.emit._ir import _elem_size, _fn_symbol, _llvm_type, _memory_type
from pyscriptc.emit.llvm._runtime import _prebaked_declarations
from pyscriptc.errors import IRError
from pyscriptc.stdmore import COMPARE_CLASSES

# The symbols `_declarations` already emits when their `needed` group is
# active. A second declaration would be an invalid LLVM redefinition.
_PREBAKED_DECLS = frozenset(_prebaked_declarations())


class AbiMixin:
    def _ensure_arg_type(self) -> None:
        line = "%ps.arg = type { i32, i32, i64, double, %ps.buf }"
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
        if typ.get("island"):
            return 7
        return self._ISLAND_ARG_CODES[typ["kind"]]

    def _island_pad(self, want: dict[str, Any]) -> int:
        kind = want.get("kind")
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
        kind = typ["kind"]
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
        if typ.get("island"):
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
        name = expr["island"]["name"]
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
                starred = bool(pos_args[index].get("island_starred"))
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
                if k_arg.get("island_kwargs"):
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
        npos = expr.get("island_npos", len(values) - 1)
        nkw = expr.get("island_nkw", 0)
        pos_values = values[1 : 1 + npos]
        pos_args = expr["args"][1 : 1 + npos]
        kw_values = values[1 + npos :]
        kw_args = expr["args"][1 + npos :]
        pos_ptr, kw_ptr = self._prepare_island_call_args(pos_values, pos_args, kw_values, kw_args)
        size, symbol = self._island_name(expr)
        if expr["type"].get("island"):
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
        want = expr["island"]["want"]
        code = self._island_code(want)
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

        The per-module plane split: a definition that rides another
        module's island floor is called through
        `ps_island_mod_fn_invoke(module, name, ...)`, the same seam with
        the defining module's name in front of the callee key.
        """
        npos = expr.get("island_npos", len(values))
        nkw = expr.get("island_nkw", 0)
        pos_values = values[:npos]
        pos_args = expr["args"][:npos]
        kw_values = values[npos:]
        kw_args = expr["args"][npos:]
        pos_ptr, kw_ptr = self._prepare_island_call_args(pos_values, pos_args, kw_values, kw_args)
        fn = expr["island_fn"]
        name = fn["name"]
        data = name.encode("utf-8")
        size = len(data)
        symbol = self.format_global(f"island.fn.{name}", data)
        module = fn.get("module")
        if module is not None:
            mod_data = module.encode("utf-8")
            mod_size = len(mod_data)
            mod_symbol = self.format_global(f"island.mod.{module}", mod_data)
            want = fn.get("want")
            if want is not None:
                code = self._island_code(want)
                pad = self._island_pad(want)
                out = self._alloca("%ps.arg")
                self._abi_decl(
                    "ps_island_mod_fn_invoke_value",
                    "void",
                    ["i64", "ptr", "i64", "ptr", "i64", "ptr", "i64", "ptr", "i32", "i32", "ptr"],
                )
                self._call_void(
                    "ps_island_mod_fn_invoke_value",
                    [
                        f"i64 {mod_size}",
                        f"ptr {mod_symbol}",
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
            self._abi_decl(
                "ps_island_mod_fn_invoke",
                "ptr",
                ["i64", "ptr", "i64", "ptr", "i64", "ptr", "i64", "ptr"],
            )
            return self._call_ret(
                "ptr",
                "ps_island_mod_fn_invoke",
                [
                    f"i64 {mod_size}",
                    f"ptr {mod_symbol}",
                    f"i64 {size}",
                    f"ptr {symbol}",
                    f"i64 {npos}",
                    f"ptr {pos_ptr}",
                    f"i64 {nkw}",
                    f"ptr {kw_ptr}",
                ],
            )
        want = fn.get("want")
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
        want = expr["island"]["want"]
        code = self._island_code(want)
        pad = self._island_pad(want)
        key = expr["island"]["key"]
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
        source = self.module.get("islandSource")
        if source:
            self._emit_island_seed(source)
        # The per-module plane split: every program module's island floor
        # seed runs beside the single-namespace seed, in the merge's module
        # order (the import order the merge recorded).
        modules = self.module.get("islandModules") or {}
        for mod_name, mod_source in modules.items():
            data = mod_name.encode("utf-8")
            mod_size = len(data)
            mod_symbol = self.format_global(f"island.mod.{mod_name}", data)
            src = mod_source.encode("utf-8")
            src_size = len(src)
            src_symbol = self.format_global(f"island.seed.{mod_name}", src)
            self._abi_decl("ps_island_mod_seed", "void", ["i64", "ptr", "i64", "ptr"])
            self._call_void(
                "ps_island_mod_seed",
                [f"i64 {mod_size}", f"ptr {mod_symbol}", f"i64 {src_size}", f"ptr {src_symbol}"],
            )

    # boxed:end

    def _emit_island_getattr(self, expr: dict[str, Any], values: list[str]) -> str:
        """One attribute read: `island_getattr(handle, name, want, &out)`."""
        self._ensure_arg_type()
        want = expr["island"]["want"]
        code = self._island_code(want)
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
        want = expr["island"]["want"]
        code = self._island_code(want)
        pad = self._island_pad(want)
        out = self._alloca("%ps.arg")
        self._abi_decl("ps_island_unmarshal", "void", ["ptr", "i32", "i32", "ptr"])
        self._call_void(
            "ps_island_unmarshal",
            [f"ptr {values[0]}", f"i32 {code}", f"i32 {pad}", f"ptr {out}"],
        )
        return self._load_island_result(out, want)

    def _load_island_result(self, out: str, want: dict[str, Any]) -> str:
        kind = want["kind"]
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
        if kind == "list":
            self.needed.add("seq")
            slot = self.tmp()
            self.line(f"{slot} = getelementptr %ps.arg, ptr {out}, i32 0, i32 2")
            return self._load("ptr", slot)
        if kind == "dict":
            self.needed.add("map")
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
        self.line(f"call void @{symbol}({', '.join([*args, f'ptr {out}'])})")
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
        if kind in {"list", "dict", "func", "exc"} or (
            kind == "class"
            and (
                ret.get("name")
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
                self._abi_decl("ps_global_get_path", "void", [*types, "ptr"])
                self._call_void("ps_global_get_path", [*rendered, f"ptr {out}"])
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

    def _emit_to_thread_new(self, expr: dict[str, Any], values: list[str]) -> str:
        """`asyncio.to_thread(fn, ...)`: the record that owns the result.

        The size is the result slot's size, so the runtime copies exactly
        the value's bytes. The callable pair and the worker start ride on
        the `ps_to_thread_start` call that follows.
        """
        meta = expr["thread"]
        size = meta["size"]
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
        pair = self.emit_closure(expr["args"][1])
        self._abi_decl("ps_to_thread_start", "void", ["ptr", "ptr"])
        self._call_void("ps_to_thread_start", [f"ptr {record}", f"ptr {pair}"])
        return ""

    def _emit_list_push(self, expr: dict[str, Any], values: list[str]) -> str:
        seq, elem = values
        slot = self._stash_cell(expr["args"][1]["type"], elem)
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

    def _emit_exit_message(self, message: str) -> str:
        """`sys.exit(str)`: CPython raises `SystemExit(str)`, so the
        uncaught handler writes the message to stderr and exits 1, and an
        `except SystemExit` still catches it (issue #645). The SystemExit
        raise path carries both rules.
        """
        self.needed.add("exc")
        self._abi_decl("ps_raise_msg", "void", ["i32", "i64", "ptr"])
        length, ptr = self._buf_parts(message)
        self.line(f"call void @ps_raise_msg(i32 {EXCEPTIONS['SystemExit']}, i64 {length}, ptr {ptr}) noreturn")
        self.line("unreachable")
        self.terminated = True
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
        slot = self._stash_cell(expr["args"][1]["type"], values[1])
        symbol = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {slot}"])
        return ""

    def _emit_deque_load(self, expr: dict[str, Any], values: list[str]) -> str:
        out = self._alloca(_memory_type(expr["type"]))
        symbol = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"ptr {out}"])
        return self._widen_cell(expr["type"], out)

    def _emit_class_cmp(self, expr: dict[str, Any], left: str, right: str) -> str:
        name = expr["left"]["type"]["name"]
        op = expr["op"]
        if name == "Path":
            left_ptr = self.tmp()
            right_ptr = self.tmp()
            self.line(f"{left_ptr} = getelementptr %cls.Path, ptr {left}, i32 0, i32 0")
            self.line(f"{right_ptr} = getelementptr %cls.Path, ptr {right}, i32 0, i32 0")
            cmp = self._buf_cmp_cells(self._load("%ps.buf", left_ptr), self._load("%ps.buf", right_ptr))
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
        out = self._alloca(_memory_type(elem))
        self.line(f"call void @ps_seq_get(ptr {seq}, i64 {index}, ptr {out})")
        return self._widen_cell(elem, out)

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

    def _thread_symbol(self, spec: dict[str, Any]) -> str:
        """The env-first stub a `to_thread` worker runs.

        The stub is the partial shim with one more job: it stores the
        callable's return value into the thread record, so the loop can
        hand it back to the awaiting frame.
        """
        key = repr((spec["bound"], spec["params"], spec["return"]))
        symbol = self.thread_keys.get(key)
        if symbol is None:
            symbol = f"ps.thread.{self.thread_n}"
            self.thread_n += 1
            self.thread_keys[key] = symbol
            self.threads[symbol] = spec
        return symbol

    def _emit_thread_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        """`ps.thread.N(env, p...)`: the partial shim plus the result store.

        The env carries the bound arguments, the inner pair and the thread
        record. The stub calls the inner impl env-first with the bound
        arguments prepended, then copies the result into the record.
        """
        bound = spec["bound"]
        params = spec["params"]
        ret = _llvm_type(spec["return"])
        plist = ["ptr %env"] + [f"{_llvm_type(p)} %p{index}" for index, p in enumerate(params)]
        # The stub is emitted outside the abi machinery, so it declares the
        # runtime call it makes itself.
        self._abi_decl("ps_to_thread_store", "void", ["ptr", "ptr", "i64"])
        self.out.append(f"define internal {ret} @{symbol}({', '.join(plist)}) {{")
        self.out.append("entry:")
        args: list[str] = []
        for index, typ in enumerate(bound):
            if index == 0:
                self.out.append("  %b0 = load ptr, ptr %env")
            else:
                self.out.append(f"  %g{index} = getelementptr ptr, ptr %env, i64 {index}")
                self.out.append(f"  %b{index} = load ptr, ptr %g{index}")
            self.out.append(f"  %v{index} = load {_llvm_type(typ)}, ptr %b{index}")
            args.append(f"{_llvm_type(typ)} %v{index}")
        # The inner pair sits after the bound arguments.
        self.out.append(f"  %ip = getelementptr ptr, ptr %env, i64 {len(bound)}")
        self.out.append("  %pc = load ptr, ptr %ip")
        self.out.append("  %pp = load ptr, ptr %pc")
        self.out.append("  %impl = load ptr, ptr %pp")
        self.out.append("  %ip2 = getelementptr ptr, ptr %pp, i64 1")
        self.out.append("  %env2 = load ptr, ptr %ip2")
        # The thread record sits after the inner pair. The env entry packs
        # the record into a heap cell, so the stub loads it out of the cell.
        self.out.append(f"  %ip3 = getelementptr ptr, ptr %env, i64 {len(bound) + 1}")
        self.out.append("  %reccell = load ptr, ptr %ip3")
        self.out.append("  %rec = load ptr, ptr %reccell")
        call_args = ["ptr %env2", *args] + [f"{_llvm_type(p)} %p{index}" for index, p in enumerate(params)]
        if ret == "void":
            self.out.append(f"  call void %impl({', '.join(call_args)})")
            self.out.append("  ret void")
        else:
            self.out.append(f"  %r = call {ret} %impl({', '.join(call_args)})")
            # The result travels into the record: the worker stores it, the
            # loop hands it back to the awaiting frame. A str result narrows
            # to the 16-byte prefix the record cell holds.
            if spec["return"]["kind"] in {"str", "bytes"}:
                self.out.append("  %slot = alloca %ps.buf")
                self.out.append("  %rn = extractvalue %ps.str %r, 0")
                self.out.append("  %rp = extractvalue %ps.str %r, 1")
                self.out.append("  %slen = getelementptr %ps.buf, ptr %slot, i32 0, i32 0")
                self.out.append("  store i64 %rn, ptr %slen")
                self.out.append("  %sdata = getelementptr %ps.buf, ptr %slot, i32 0, i32 1")
                self.out.append("  store ptr %rp, ptr %sdata")
            else:
                self.out.append(f"  %slot = alloca {ret}")
                self.out.append(f"  store {ret} %r, ptr %slot")
            self.out.append(f"  call void @ps_to_thread_store(ptr %rec, ptr %slot, i64 {_elem_size(spec['return'])})")
            self.out.append(f"  ret {ret} %r")
        self.out.append("}")
        self.out.append("")

    def _heap_cell(self, typ: dict[str, Any], value: str) -> str:
        self.needed.add("alloc")
        cell = self._call_ret("ptr", "ps_alloc", [f"i64 {_elem_size(typ)}"])
        if typ["kind"] in {"str", "bytes"}:
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
        elem = expr["args"][0]["type"]["elem"]
        out = self._alloca(_memory_type(elem))
        self._abi_decl("ps_task_failure", "void", ["ptr"])
        self._call_void("ps_task_failure", [f"ptr {values[0]}"])
        symbol = expr["symbol"]
        self._abi_decl(symbol, "void", ["ptr", "i64", "ptr"])
        self._call_void(symbol, [f"ptr {values[0]}", f"i64 {_elem_size(elem)}", f"ptr {out}"])
        return self._widen_cell(elem, out)
