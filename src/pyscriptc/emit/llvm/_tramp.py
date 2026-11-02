"""The flat-ABI trampolines that front the exported functions of a binding.

A compiled function carries a `str` as a 32-byte aggregate (`%ps.str`). The
two ABIs disagree about where an aggregate that size lives: the C ABI passes
a struct bigger than two eightbytes in memory, and the LLVM IR lowering
passes it in registers (the return through an sret pointer, the argument
split into its fields). A C caller cannot be written against the IR
convention, so a caller that follows the C convention reads the wrong
registers and the host crashes.

The trampoline is the C-facing entry point. It speaks only scalars and
pointers, so both sides agree, and it calls the real function at IR level,
where the aggregate convention is consistent on both sides.

The flat shape of one exported function:

- a `str` parameter arrives as `i64` byte length and `ptr` data;
- a `str` return is written through two out-pointers (`i64` byte length
  and `ptr` data), and the buffer is one the caller owns;
- every other parameter keeps its own ABI (`i64`, `double`, `i1`);
- a function without a `str` anywhere needs no trampoline: its scalars
  already match the C ABI.

The result buffer is a fresh copy, so the caller frees it exactly once and
the compiled side never hands out a pointer into its own heap.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _fn_symbol, _llvm_type, _safe


def needs_trampoline(fn: dict[str, Any]) -> bool:
    """Whether the exported function must enter through its trampoline.

    A str anywhere in the signature makes the aggregate-vs-register ABI
    mismatch fatal, so the trampoline is mandatory. But the shim routes
    every export through its trampoline regardless: binding mode keeps the
    definition symbols hidden so a compiled function cannot interpose a
    libc name it shares (`stat`, `read`, `send` would otherwise capture
    the libc calls of the whole shared object), and a hidden symbol is not
    dlsym-able. The uniform route also gives the loader one calling
    convention to bind.
    """
    return True


def trampoline_symbol(name: str) -> str:
    """The symbol of the trampoline that fronts an exported function."""
    return f"psb.{_safe(name)}"


def trampoline_params(fn: dict[str, Any]) -> list[tuple[str, str]]:
    """The flat parameter list of the trampoline: (llvm type, name) pairs.

    A `str` parameter becomes two parameters, the byte length and the data
    pointer; the code-point count and the ASCII flag are recomputed by the
    trampoline from the bytes themselves.
    """
    flat: list[tuple[str, str]] = []
    for param in fn.get("params", []):
        kind = param["type"]["kind"]
        name = _safe(param["name"])
        if kind == "str":
            flat.append(("i64", f"{name}_len"))
            flat.append(("ptr", f"{name}_data"))
        else:
            flat.append((_llvm_type(param["type"]), name))
    return flat


def render_trampoline(fn: dict[str, Any]) -> str:
    """The IR text of the trampoline that fronts one exported function."""
    name = fn["name"]
    symbol = _fn_symbol(name)
    flat = trampoline_params(fn)
    plist = [f"{llvm_t} %{n}" for llvm_t, n in flat]
    ret_kind = fn.get("returnType", {}).get("kind", "none")
    if ret_kind == "str":
        plist.append("ptr %out_len")
        plist.append("ptr %out_data")

    # A str result leaves through the out-pointers, so the trampoline
    # itself returns nothing; every other result is the function's own
    # return type, and the trampoline hands it straight back.
    tramp_ret = "void" if ret_kind == "str" else _llvm_type(fn["returnType"])

    lines: list[str] = []
    lines.append(f"define {tramp_ret} @{trampoline_symbol(name)}({', '.join(plist)}) {{")
    lines.append("entry:")

    # Rebuild each str argument: the payload carries the byte length, the
    # data pointer, the code-point count and the ASCII flag. All four fields
    # come from loaded scalar values, because an `insertvalue poison`
    # placeholder the optimizer cannot see a store to poisons the whole
    # aggregate: a field whose value flows from poison stays poison, and the
    # compiled function would read a poisoned count.
    call_args: list[str] = []
    for index, param in enumerate(fn.get("params", [])):
        kind = param["type"]["kind"]
        pname = _safe(param["name"])
        if kind != "str":
            call_args.append(f"%{pname}")
            continue
        buf = f"%psb.b{index}"
        nchars_ptr = f"%psb.n{index}"
        flags_ptr = f"%psb.t{index}"
        len_ptr = f"%psb.l{index}"
        data_ptr = f"%psb.d{index}"
        nchars = f"%psb.m{index}"
        flags = f"%psb.g{index}"
        s0 = f"%psb.a{index}.0"
        s1 = f"%psb.a{index}.1"
        s2 = f"%psb.a{index}.2"
        s3 = f"%psb.a{index}.3"
        # ps_str_header copies the byte length and the data pointer into the
        # 16-byte buffer prefix and fills the code-point count and the ASCII
        # flag through its own out-pointers.
        lines.append(f"  {buf} = alloca %ps.buf")
        lines.append(f"  {nchars_ptr} = alloca i64")
        lines.append(f"  {flags_ptr} = alloca i32")
        lines.append(
            f"  call void @ps_str_header(i64 %{pname}_len, ptr %{pname}_data, "
            f"ptr {nchars_ptr}, ptr {flags_ptr}, ptr {buf})"
        )
        lines.append(f"  {len_ptr} = load i64, ptr {buf}")
        lines.append(f"  {data_ptr} = getelementptr %ps.buf, ptr {buf}, i32 0, i32 1")
        data_val = f"%psb.e{index}"
        lines.append(f"  {data_val} = load ptr, ptr {data_ptr}")
        lines.append(f"  {nchars} = load i64, ptr {nchars_ptr}")
        lines.append(f"  {flags} = load i32, ptr {flags_ptr}")
        lines.append(f"  {s0} = insertvalue %ps.str poison, i64 {len_ptr}, 0")
        lines.append(f"  {s1} = insertvalue %ps.str {s0}, ptr {data_val}, 1")
        lines.append(f"  {s2} = insertvalue %ps.str {s1}, i64 {nchars}, 2")
        lines.append(f"  {s3} = insertvalue %ps.str {s2}, i32 {flags}, 3")
        call_args.append(f"%ps.str {s3}")

    # The call. The real function keeps its own signature; the call is made
    # at IR level, so the aggregate convention matches on both sides. The
    # argument types are spelled out at the call site: against a hidden
    # callee the parser does not infer them from the definition, so a bare
    # `%name` fails to parse for an `i1` (and reads wrong for anything the
    # literal-vs-value distinction touches).
    arg_tys = {name: llvm_t for llvm_t, name in flat}
    typed_args = []
    for arg in call_args:
        if arg.startswith("%ps.str "):
            typed_args.append(arg)
        else:
            pname = arg.lstrip("%")
            typed_args.append(f"{arg_tys.get(pname, 'i64')} {arg}")
    ret = _llvm_type(fn["returnType"])
    if ret == "void":
        lines.append(f"  call void @{symbol}({', '.join(typed_args)})")
    else:
        lines.append(f"  %psb.r = call {ret} @{symbol}({', '.join(typed_args)})")

    if ret_kind == "str":
        # The result is copied into a buffer of its own, so the caller frees
        # exactly one allocation and the compiled side never hands out a
        # pointer into its own heap.
        lines.append("  %psb.rlen = extractvalue %ps.str %psb.r, 0")
        lines.append("  %psb.rdata = extractvalue %ps.str %psb.r, 1")
        lines.append("  %psb.rbytes = add i64 %psb.rlen, 1")
        lines.append("  %psb.rbuf = call ptr @malloc(i64 %psb.rbytes)")
        lines.append("  %psb.rnull = icmp eq ptr %psb.rbuf, null")
        lines.append("  br i1 %psb.rnull, label %psb.nomem, label %psb.copy")
        lines.append("psb.nomem:")
        lines.append("  store i64 0, ptr %out_len")
        lines.append("  store ptr null, ptr %out_data")
        lines.append("  br label %psb.done")
        lines.append("psb.copy:")
        lines.append("  %psb.rpos = icmp sgt i64 %psb.rlen, 0")
        lines.append("  br i1 %psb.rpos, label %psb.memcpy, label %psb.nocopy")
        lines.append("psb.memcpy:")
        lines.append("  call void @llvm.memcpy.p0.p0.i64(ptr %psb.rbuf, ptr %psb.rdata, i64 %psb.rlen, i1 false)")
        lines.append("  br label %psb.nocopy")
        lines.append("psb.nocopy:")
        lines.append("  %psb.rend = getelementptr i8, ptr %psb.rbuf, i64 %psb.rlen")
        lines.append("  store i8 0, ptr %psb.rend")
        lines.append("  store i64 %psb.rlen, ptr %out_len")
        lines.append("  store ptr %psb.rbuf, ptr %out_data")
        lines.append("  br label %psb.done")
        lines.append("psb.done:")
    if tramp_ret == "void":
        lines.append("  ret void")
    else:
        lines.append(f"  ret {tramp_ret} %psb.r")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
