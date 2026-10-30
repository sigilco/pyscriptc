"""The LLVM runtime declaration table and the checked-arithmetic helpers.

The `ps_*` declarations are the runtime ABI: the emitter declares only the
helpers a program actually uses. The helper bodies are plain LLVM text with
the exception codes filled in from the builtin table, so a renumber moves
them together.
"""

from __future__ import annotations

import re

from pyscriptc.builtins import EXCEPTIONS
from pyscriptc.ir import zdi_variants
from pyscriptc.stdlib_queue import MATH_FUNCTIONS


def _prebaked_declarations() -> list[str]:
    """Every symbol the declaration table can name, for the emitter's
    duplicate check: `_abi_decl` must not re-declare these."""

    found: list[str] = []
    for line in _declarations(set()) + _declarations(
        {
            "printf",
            "fprintf",
            "iadd",
            "isub",
            "imul",
            "ineg",
            "strconcat",
            "floordiv",
            "imod",
            "ishl",
            "ishr",
            "fdiv",
            "fmod",
            "ffloordiv",
            "fpow",
            "dbg",
            "str",
            "math",
            "exc",
            "alloc",
            "map",
            "seq",
            "b64",
            "hash",
            "json",
            "re",
            "struct",
            "url",
            "try",
        }
    ):
        match = re.search(r"@([A-Za-z_][\w.]*?)\(", line)
        if match is not None:
            found.append(match.group(1))
    return found


def _declarations(needed: set[str]) -> list[str]:
    lines: list[str] = []
    if "printf" in needed:
        lines.append("declare i32 @printf(ptr, ...)")
    if "fprintf" in needed:
        lines.append("declare i32 @fprintf(ptr, ptr, ...)")
        lines.append("declare ptr @ps_stderr_stream()")
    traps = needed & {
        "iadd",
        "isub",
        "imul",
        "ineg",
        "strconcat",
        "floordiv",
        "imod",
        "ishl",
        "ishr",
        "fdiv",
        "fmod",
        "ffloordiv",
        "fpow",
    }
    if traps and "exc" not in needed:
        lines.append("declare void @ps_raise_code(i32) noreturn")
    if traps:
        lines.append("declare void @ps_raise_zdi(i32) noreturn")
    if "iadd" in needed or "strconcat" in needed:
        lines.append("declare { i64, i1 } @llvm.sadd.with.overflow.i64(i64, i64)")
    if "isub" in needed or "ineg" in needed:
        lines.append("declare { i64, i1 } @llvm.ssub.with.overflow.i64(i64, i64)")
    if "imul" in needed:
        lines.append("declare { i64, i1 } @llvm.smul.with.overflow.i64(i64, i64)")
    if "ffloordiv" in needed:
        lines.append("declare double @llvm.floor.f64(double)")
    if "fpow" in needed:
        lines.append("declare double @llvm.pow.f64(double, double)")
    if "strconcat" in needed:
        lines.append("declare ptr @malloc(i64)")
        lines.append("declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)")
    if "dbg" in needed:
        lines.append("declare void @llvm.dbg.value(metadata, metadata, metadata)")
        lines.append("declare void @llvm.dbg.declare(metadata, metadata, metadata)")
    lines.extend(_runtime_declarations(needed))
    return lines


def _runtime_declarations(needed: set[str]) -> list[str]:
    lines: list[str] = []
    if "exc" in needed:
        lines.append("declare i32 @ps_try_call(ptr, ptr, ptr)")
        lines.append("declare void @ps_raise(i32) noreturn")
        lines.append("declare void @ps_raise_code(i32) noreturn")
        lines.append("declare void @ps_raise_msg(i32, i64, ptr) noreturn")
        lines.append("declare void @ps_reraise() noreturn")
        lines.append("declare i32 @ps_exc_code()")
        lines.append("declare i32 @ps_exc_isa(i32, i32)")
    if "alloc" in needed:
        lines.append("declare ptr @ps_alloc(i64)")
    if "seq" in needed:
        lines.extend(
            [
                "declare ptr @ps_seq_new(i64, i64)",
                "declare i64 @ps_seq_len(ptr)",
                "declare void @ps_seq_get(ptr, i64, ptr)",
                "declare void @ps_seq_set(ptr, i64, ptr)",
                "declare i32 @ps_seq_has_i64(ptr, i64)",
                "declare i32 @ps_seq_has_f64(ptr, double)",
                "declare i32 @ps_seq_has_buf(ptr, i64, ptr)",
            ]
        )
    if "map" in needed:
        lines.extend(
            [
                "declare ptr @ps_map_new(i32, i64, i64, i64)",
                "declare i64 @ps_map_len(ptr)",
                "declare void @ps_map_put(ptr, ptr, ptr)",
                "declare void @ps_map_get(ptr, ptr, ptr)",
                "declare i32 @ps_map_has(ptr, ptr)",
            ]
        )
    if "str" in needed:
        lines.extend(
            [
                "declare void @ps_str_get(i64, ptr, i64, ptr)",
                "declare i64 @ps_buf_at(i64, ptr, i64)",
                "declare void @ps_str_strip(i64, ptr, ptr)",
                "declare void @ps_str_lstrip(i64, ptr, ptr)",
                "declare void @ps_str_rstrip(i64, ptr, ptr)",
                "declare i32 @ps_str_startswith(i64, ptr, i64, ptr)",
                "declare i32 @ps_str_endswith(i64, ptr, i64, ptr)",
                "declare i64 @ps_str_find(i64, ptr, i64, ptr)",
                "declare i32 @ps_str_cmp(i64, ptr, i64, ptr)",
                "declare void @ps_str_upper(i64, ptr, ptr)",
                "declare void @ps_str_lower(i64, ptr, ptr)",
                "declare void @ps_str_replace(i64, ptr, i64, ptr, i64, ptr, ptr)",
            ]
        )
    if "math" in needed:
        lines.append("declare i64 @ps_float_to_int(double)")
        for spec in MATH_FUNCTIONS.values():
            symbol = spec["symbol"]
            if not symbol:
                continue
            params = ", ".join(["double"] * len(spec["params"]))
            lines.append(f"declare {spec['llvm']} @{symbol}({params})")
    if "fabs" in needed:
        lines.append("declare double @llvm.fabs.f64(double)")
    if "re" in needed:
        lines.extend(
            [
                "declare ptr @ps_re_findall(i64, ptr, i64, ptr)",
                "declare void @ps_re_sub(i64, ptr, i64, ptr, i64, ptr, ptr)",
                "declare ptr @ps_re_split(i64, ptr, i64, ptr)",
                "declare void @ps_re_escape(i64, ptr, ptr)",
            ]
        )
    if "json" in needed:
        lines.extend(
            [
                "declare void @ps_json_loads(i64, ptr, ptr, i32, ptr)",
                "declare void @ps_json_loads_bytes(i64, ptr, ptr, i32, ptr)",
                "declare void @ps_json_dumps(ptr, i32, ptr, ptr)",
                "declare void @ps_json_dumps_opts(ptr, i32, ptr, i32, ptr)",
            ]
        )
    if "url" in needed:
        lines.extend(
            [
                "declare void @ps_url_quote(i64, ptr, i64, ptr, ptr)",
                "declare void @ps_url_quote_plus(i64, ptr, i64, ptr, ptr)",
                "declare void @ps_url_unquote(i64, ptr, ptr)",
                "declare void @ps_url_unquote_plus(i64, ptr, ptr)",
                "declare void @ps_url_encode(ptr, ptr)",
                "declare ptr @ps_url_parse_qs(i64, ptr)",
                "declare void @ps_url_split(i64, ptr, ptr)",
            ]
        )
    if "b64" in needed:
        lines.extend(
            [
                "declare void @ps_b64_std_encode(i64, ptr, ptr)",
                "declare void @ps_b64_std_decode(i64, ptr, ptr)",
                "declare void @ps_b64_url_encode(i64, ptr, ptr)",
                "declare void @ps_b64_url_decode(i64, ptr, ptr)",
            ]
        )
    if "hash" in needed:
        lines.extend(
            [
                "declare ptr @ps_hash_md5(i64, ptr)",
                "declare ptr @ps_hash_sha1(i64, ptr)",
                "declare ptr @ps_hash_sha256(i64, ptr)",
                "declare ptr @ps_hash_sha384(i64, ptr)",
                "declare ptr @ps_hash_sha512(i64, ptr)",
                "declare void @ps_hash_hex(ptr, ptr)",
                "declare void @ps_hash_digest(ptr, ptr)",
            ]
        )
    if "struct" in needed:
        lines.extend(
            [
                "declare void @ps_struct_pack(i64, ptr, ptr, i64, ptr)",
                "declare ptr @ps_struct_unpack(i64, ptr, i64, ptr, i32)",
            ]
        )
    return lines


def _helpers(needed: set[str]) -> list[str]:
    chunks: list[str] = []
    if "iadd" in needed:
        chunks.append(_overflow_helper("iadd", "sadd"))
    if "isub" in needed:
        chunks.append(_overflow_helper("isub", "ssub"))
    if "imul" in needed:
        chunks.append(_overflow_helper("imul", "smul"))
    if "ineg" in needed:
        chunks.append(
            """define internal i64 @ps.ineg(i64 %a) {
entry:
  %r = call { i64, i1 } @llvm.ssub.with.overflow.i64(i64 0, i64 %a)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 @OVF@) noreturn ; OverflowError
  unreachable
}
"""
        )
    if "floordiv" in needed:
        chunks.append(
            """define internal i64 @ps.floordiv(i64 %a, i64 %b) {
entry:
  %zero = icmp eq i64 %b, 0
  br i1 %zero, label %zdiv, label %nonzero
nonzero:
  %min = icmp eq i64 %a, -9223372036854775808
  %neg1 = icmp eq i64 %b, -1
  %bad = and i1 %min, %neg1
  br i1 %bad, label %ovf, label %divide
divide:
  %q = sdiv i64 %a, %b
  %r = srem i64 %a, %b
  %rnz = icmp ne i64 %r, 0
  %aneg = icmp slt i64 %a, 0
  %bneg = icmp slt i64 %b, 0
  %diff = icmp ne i1 %aneg, %bneg
  %adjust = and i1 %rnz, %diff
  br i1 %adjust, label %dec, label %keep
dec:
  %q2 = sub i64 %q, 1
  ret i64 %q2
keep:
  ret i64 %q
zdiv:
  call void @ps_raise_zdi(i32 @ZDI_idiv@) noreturn ; ZeroDivisionError
  unreachable
ovf:
  call void @ps_raise_code(i32 @OVF@) noreturn ; OverflowError
  unreachable
}
"""
        )
    if "imod" in needed:
        chunks.append(
            """define internal i64 @ps.imod(i64 %a, i64 %b) {
entry:
  %zero = icmp eq i64 %b, 0
  br i1 %zero, label %trap, label %nonzero
nonzero:
  %min = icmp eq i64 %a, -9223372036854775808
  %neg1 = icmp eq i64 %b, -1
  %bad = and i1 %min, %neg1
  br i1 %bad, label %zeroret, label %divide
zeroret:
  ret i64 0
divide:
  %r = srem i64 %a, %b
  %rnz = icmp ne i64 %r, 0
  %rneg = icmp slt i64 %r, 0
  %bneg = icmp slt i64 %b, 0
  %diff = icmp ne i1 %rneg, %bneg
  %adjust = and i1 %rnz, %diff
  %r2 = add i64 %r, %b
  %out = select i1 %adjust, i64 %r2, i64 %r
  ret i64 %out
trap:
  call void @ps_raise_zdi(i32 @ZDI_imod@) noreturn ; ZeroDivisionError
  unreachable
}
"""
        )
    if "ishl" in needed:
        chunks.append(
            """define internal i64 @ps.ishl(i64 %a, i64 %b) {
entry:
  %neg = icmp slt i64 %b, 0
  br i1 %neg, label %negshift, label %ok
ok:
  %big = icmp uge i64 %b, 64
  br i1 %big, label %wide, label %shift
wide:
  %is0 = icmp eq i64 %a, 0
  br i1 %is0, label %zret, label %ovf
zret:
  ret i64 0
shift:
  %r = shl i64 %a, %b
  %back = ashr i64 %r, %b
  %fit = icmp eq i64 %back, %a
  br i1 %fit, label %good, label %ovf
good:
  ret i64 %r
negshift:
  call void @ps_raise_code(i32 @VAL@) noreturn ; ValueError
  unreachable
ovf:
  call void @ps_raise_code(i32 @OVF@) noreturn ; OverflowError
  unreachable
}
"""
        )
    if "ishr" in needed:
        chunks.append(
            """define internal i64 @ps.ishr(i64 %a, i64 %b) {
entry:
  %neg = icmp slt i64 %b, 0
  br i1 %neg, label %trap, label %ok
ok:
  %big = icmp sge i64 %b, 64
  %c = select i1 %big, i64 63, i64 %b
  %r = ashr i64 %a, %c
  ret i64 %r
trap:
  call void @ps_raise_code(i32 @VAL@) noreturn ; ValueError
  unreachable
}
"""
        )
    if "fdiv" in needed:
        chunks.append(
            """define internal double @ps.fdiv(double %a, double %b) {
entry:
  %z = fcmp oeq double %b, 0.0
  br i1 %z, label %trap, label %ok
ok:
  %q = fdiv double %a, %b
  ret double %q
trap:
  call void @ps_raise_zdi(i32 @ZDI_fdiv@) noreturn ; ZeroDivisionError
  unreachable
}
"""
        )
    if "fmod" in needed:
        chunks.append(
            """define internal double @ps.fmod(double %a, double %b) {
entry:
  %z = fcmp oeq double %b, 0.0
  br i1 %z, label %trap, label %ok
ok:
  %r = frem double %a, %b
  %rnz = fcmp one double %r, 0.0
  %rneg = fcmp olt double %r, 0.0
  %bneg = fcmp olt double %b, 0.0
  %diff = icmp ne i1 %rneg, %bneg
  %adjust = and i1 %rnz, %diff
  %r2 = fadd double %r, %b
  %out = select i1 %adjust, double %r2, double %r
  ret double %out
trap:
  call void @ps_raise_zdi(i32 @ZDI_fmod@) noreturn ; ZeroDivisionError
  unreachable
}
"""
        )
    if "ffloordiv" in needed:
        chunks.append(
            """define internal double @ps.ffloordiv(double %a, double %b) {
entry:
  %z = fcmp oeq double %b, 0.0
  br i1 %z, label %trap, label %ok
ok:
  %q = fdiv double %a, %b
  %f = call double @llvm.floor.f64(double %q)
  ret double %f
trap:
  call void @ps_raise_zdi(i32 @ZDI_ffloordiv@) noreturn ; ZeroDivisionError
  unreachable
}
"""
        )
    if "fpow" in needed:
        chunks.append(
            """define internal double @ps.fpow(double %a, double %b) {
entry:
  %z = fcmp oeq double %a, 0.0
  %n = fcmp olt double %b, 0.0
  %bad = and i1 %z, %n
  br i1 %bad, label %trap, label %ok
ok:
  %r = call double @llvm.pow.f64(double %a, double %b)
  ret double %r
trap:
  call void @ps_raise_zdi(i32 @ZDI_fpow@) noreturn ; ZeroDivisionError
  unreachable
}
"""
        )
    if "strconcat" in needed:
        chunks.append(
            """define internal %ps.str @ps.strconcat(%ps.str %a, %ps.str %b) {
entry:
  %na = extractvalue %ps.str %a, 0
  %pa = extractvalue %ps.str %a, 1
  %ca = extractvalue %ps.str %a, 2
  %fa = extractvalue %ps.str %a, 3
  %nb = extractvalue %ps.str %b, 0
  %pb = extractvalue %ps.str %b, 1
  %cb = extractvalue %ps.str %b, 2
  %fb = extractvalue %ps.str %b, 3
  %sum = call { i64, i1 } @llvm.sadd.with.overflow.i64(i64 %na, i64 %nb)
  %ov = extractvalue { i64, i1 } %sum, 1
  br i1 %ov, label %trap, label %sized
sized:
  %n = extractvalue { i64, i1 } %sum, 0
  %atmax = icmp eq i64 %n, 9223372036854775807
  br i1 %atmax, label %trap, label %alloc
alloc:
  %n1 = add i64 %n, 1
  %mem = call ptr @malloc(i64 %n1)
  %null = icmp eq ptr %mem, null
  br i1 %null, label %nomem, label %copy
copy:
  call void @llvm.memcpy.p0.p0.i64(ptr %mem, ptr %pa, i64 %na, i1 false)
  %dst2 = getelementptr i8, ptr %mem, i64 %na
  call void @llvm.memcpy.p0.p0.i64(ptr %dst2, ptr %pb, i64 %nb, i1 false)
  %end = getelementptr i8, ptr %mem, i64 %n
  store i8 0, ptr %end
  %csum = call { i64, i1 } @llvm.sadd.with.overflow.i64(i64 %ca, i64 %cb)
  %cov = extractvalue { i64, i1 } %csum, 1
  br i1 %cov, label %trap, label %counts
counts:
  %c = extractvalue { i64, i1 } %csum, 0
  %andf = and i32 %fa, %fb
  %s0 = insertvalue %ps.str poison, i64 %n, 0
  %s1 = insertvalue %ps.str %s0, ptr %mem, 1
  %s2 = insertvalue %ps.str %s1, i64 %c, 2
  %s3 = insertvalue %ps.str %s2, i32 %andf, 3
  ret %ps.str %s3
trap:
  call void @ps_raise_code(i32 @OVF@) noreturn ; OverflowError
  unreachable
nomem:
  call void @ps_raise_code(i32 @RUN@) noreturn ; RuntimeError
  unreachable
}
"""
        )
    return [_exc_codes(chunk) for chunk in chunks]


def _exc_codes(chunk: str) -> str:
    """Fill the exception-code placeholders in a runtime helper.

    The helpers are plain strings so the LLVM braces stay literal; the codes
    come from the builtin table, so a renumber moves them together. The
    ZeroDivisionError message variants track the running interpreter's
    CPython: 3.13 and earlier name the operation, 3.14 unified them into
    "division by zero" (the same interpreter the oracle runs on).
    """

    zdi = zdi_variants()
    for token, value in (
        ("@OVF@", str(EXCEPTIONS["OverflowError"])),
        ("@ZDI@", str(EXCEPTIONS["ZeroDivisionError"])),
        ("@VAL@", str(EXCEPTIONS["ValueError"])),
        ("@RUN@", str(EXCEPTIONS["RuntimeError"])),
        ("@ZDI_idiv@", str(zdi["idiv"])),
        ("@ZDI_imod@", str(zdi["imod"])),
        ("@ZDI_fdiv@", str(zdi["fdiv"])),
        ("@ZDI_fmod@", str(zdi["fmod"])),
        ("@ZDI_ffloordiv@", str(zdi["ffloordiv"])),
        ("@ZDI_fpow@", str(zdi["fpow"])),
    ):
        chunk = chunk.replace(token, value)
    return chunk


def _overflow_helper(name: str, intrinsic: str) -> str:
    return _exc_codes(f"""define internal i64 @ps.{name}(i64 %a, i64 %b) {{
entry:
  %r = call {{ i64, i1 }} @llvm.{intrinsic}.with.overflow.i64(i64 %a, i64 %b)
  %ov = extractvalue {{ i64, i1 }} %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue {{ i64, i1 }} %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 @OVF@) noreturn ; OverflowError
  unreachable
}}
""")
