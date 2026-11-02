"""The CPython ctypes binding generator for compiled shared libraries.

`pyscriptc compile ... --emit binding` compiles the module plus runtime into a
shared library (`.so`, `.dylib`, `.dll`) and emits `<name>_binding.py` beside it.
The generated loader module uses `ctypes.CDLL` to expose the module's typed
public functions directly to CPython without requiring `Python.h` or CPython
extension modules.

First-cut scope: top-level functions with int, float, bool parameters and
returns. Any unsupported function, class, or method is skipped with a named note
in the generated module docstring and a nonzero count in the compile summary.
"""

from __future__ import annotations

import sys
from typing import Any

from pyscriptc.emit._ir import _fn_symbol
from pyscriptc.emit.llvm._tramp import needs_trampoline, trampoline_symbol

_SUPPORTED_PARAM_KINDS = frozenset({"int", "float", "bool", "str"})
_SUPPORTED_RETURN_KINDS = frozenset({"int", "float", "bool", "none", "str"})

_CTYPES_TYPE_MAP = {
    "int": "ctypes.c_int64",
    "float": "ctypes.c_double",
    "bool": "ctypes.c_bool",
    "none": "None",
}

_PY_TYPE_MAP = {
    "int": "int",
    "float": "float",
    "bool": "bool",
    "none": "None",
    "str": "str",
}

# The C types the shim wrapper and the guard callback use for each supported
# kind. They are the ABI spelling of the LLVM types (`i64`, `double`, `i1`,
# `void`): a C `bool` is i1 in the IR too, so a `_Bool` parameter matches the
# emitted signature. A `str` never crosses as a struct: the compiled
# function's own signature carries it as a 32-byte aggregate, and the C ABI
# and the IR lowering disagree about where an aggregate that size lives. The
# emitter writes a flat-ABI trampoline for every exported function that
# carries a str, and the shim calls that instead: a str argument arrives as
# a byte length and a data pointer, and a str result is written through two
# out-pointers into a buffer the caller owns.
_C_TYPE_MAP = {
    "int": "int64_t",
    "float": "double",
    "bool": "bool",
    "none": "void",
}

_C_ZERO_MAP = {
    "int": "0",
    "float": "0.0",
    "bool": "false",
}

# The loader-side ctypes spelling of a str argument: the byte length and
# the data pointer, the two halves the flat trampoline takes.
_CTYPES_STR = "ctypes.c_int64"
_CTYPES_STR_DATA = "ctypes.c_char_p"


def shared_library_suffix(target: str | None = None) -> str:
    """The shared library file extension for the host or target triple."""
    if target:
        if "windows" in target:
            return ".dll"
        if "macos" in target or "darwin" in target or "apple" in target:
            return ".dylib"
        return ".so"
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"


def shared_library_name(stem: str, target: str | None = None) -> str:
    """The default shared library filename for a module stem."""
    suffix = shared_library_suffix(target)
    if suffix == ".dll":
        return f"{stem}.dll"
    return f"lib{stem}{suffix}"


def _render_type(t: dict[str, Any]) -> str:
    """Render an IR type dictionary into human-readable type syntax."""
    kind = t.get("kind", "unknown")
    if kind == "int":
        return "int"
    if kind == "float":
        return "float"
    if kind == "bool":
        return "bool"
    if kind == "str":
        return "str"
    if kind == "bytes":
        return "bytes"
    if kind == "none":
        return "None"
    if kind == "list":
        return f"list[{_render_type(t.get('elem', {}))}]"
    if kind == "dict":
        return f"dict[{_render_type(t.get('key', {}))}, {_render_type(t.get('val', {}))}]"
    if kind == "class":
        return t.get("name", "class")
    return kind


def _python_const_repr(typ: dict[str, Any], raw_val: Any) -> str:
    """Format a default constant value for Python source code."""
    kind = typ.get("kind")
    if kind == "int":
        return repr(int(raw_val))
    if kind == "float":
        return repr(float(raw_val))
    if kind == "bool":
        if isinstance(raw_val, str):
            val = raw_val.lower() in ("true", "1")
        else:
            val = bool(raw_val)
        return repr(val)
    return repr(raw_val)


def analyze_binding(
    module: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Inspect lowered module functions and classes for binding compatibility.

    Returns `(emitted_functions, skipped_items)`, where each skipped item is a
    pair of `(name, reason)`.
    """
    emitted: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    entry = module.get("entry")

    for fn in module.get("functions", []):
        name = fn["name"]
        # Skip compiler-synthesized standalone entry functions without params
        if name in ("main", "__entry__") and name == entry and not fn.get("params"):
            continue

        # Nested functions have an environment pointer or a dot in the name
        if "env" in fn or "." in name:
            skipped.append((name, "nested function cannot be exported as a top-level binding"))
            continue

        reasons: list[str] = []
        for param in fn.get("params", []):
            pkind = param.get("type", {}).get("kind")
            if pkind not in _SUPPORTED_PARAM_KINDS:
                rendered = _render_type(param.get("type", {}))
                reasons.append(f"parameter {param['name']!r} has unsupported type '{rendered}'")

        rkind = fn.get("returnType", {}).get("kind")
        if rkind not in _SUPPORTED_RETURN_KINDS:
            rendered = _render_type(fn.get("returnType", {}))
            reasons.append(f"return type has unsupported type '{rendered}'")

        if reasons:
            skipped.append((name, "; ".join(reasons)))
        else:
            emitted.append(fn)

    for cls in module.get("classes", []):
        if cls.get("name") in ("BytesIO", "TextIO"):
            continue
        skipped.append((cls["name"], "classes are not exported in the first-cut ctypes binding"))

    return emitted, skipped


def shim_symbol(name: str) -> str:
    """The C symbol of the guarded wrapper for an exported function.

    The wrapper carries its own name so the emitted function keeps its symbol
    (and any declared FFI caller keeps resolving); the loader module calls this
    one instead. The index-free spelling keeps the name greppable in the
    library, and a name that is not a C identifier (a Unicode Python
    identifier) is hex-escaped byte by byte.
    """
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch == "_") else f"_u{ord(ch):x}_" for ch in name)
    return f"ps_bind__{safe}"


def render_binding_shim(module_name: str, emitted: list[dict[str, Any]]) -> str:
    """Generate the C shim that runs every exported call under an exception guard.

    A raise inside a compiled function unwinds with `longjmp`. Entered through
    ctypes there is no handler on the far side of the C ABI, so the runtime
    reports the exception and exits the host process. Each exported function
    gets a wrapper whose body is a single call to `ps_bind_guard`, which runs
    the real function under a `setjmp` frame and parks the record when it
    raises; the loader module then raises the matching Python exception.

    A `str` never crosses this shim as a struct. The emitter writes a
    flat-ABI trampoline (`psb.<name>`) for every exported function that
    carries one: the trampoline takes a byte length and a data pointer per
    str argument and two out-pointers for a str result, so the C side and
    the IR side agree on every parameter. The wrapper passes the host's
    buffer straight through (the compiled code must not retain a str
    argument past the call) and reads the result out of the out-pointers;
    the loader frees that buffer through `ps_bind_str_free`.
    """
    lines: list[str] = []
    lines.append("/* The C ABI exception guard for a pyscriptc shared library.")
    lines.append("")
    lines.append(f"   Generated by `pyscriptc compile --emit binding` for {module_name}.")
    lines.append("   Each exported function is wrapped: the call runs under a setjmp frame")
    lines.append("   in the runtime, so a raise unwinds to the guard at the ABI edge instead")
    lines.append("   of through the ctypes frame, where no handler exists and the process")
    lines.append("   would report the exception and exit. The guard parks the record and")
    lines.append(f"   {module_name}_binding.py turns it into a Python exception.")
    lines.append("*/")
    lines.append("")
    lines.append("#include <stdbool.h>")
    lines.append("#include <stddef.h>")
    lines.append("#include <stdint.h>")
    lines.append("#include <stdlib.h>")
    lines.append("")
    lines.append('#include "ps_rt.h"')
    lines.append("")
    lines.append("")
    lines.append("/* The module entry, guarded like every exported call: a raise in a")
    lines.append("   module-level statement must reach the importer, not kill the host. */")
    lines.append("extern int32_t pyscriptc_main(void);")
    lines.append("")
    lines.append("static int32_t ps_bind_call_init(void *env) {")
    lines.append("    (void)env;")
    lines.append("    return pyscriptc_main();")
    lines.append("}")
    lines.append("")
    lines.append("int32_t ps_bind_init(void) {")
    lines.append("    ps_bind_guard(ps_bind_call_init, NULL);")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")

    has_str = any(needs_trampoline(fn) for fn in emitted)
    if has_str:
        lines.append("/* The buffer of one str result, freed after the loader reads it. */")
        lines.append("void ps_bind_str_free(void *p) {")
        lines.append("    free(p);")
        lines.append("}")
        lines.append("")

    for index, fn in enumerate(emitted):
        name = fn["name"]
        params = fn.get("params", [])
        ret_kind = fn.get("returnType", {}).get("kind", "none")
        ret_is_str = ret_kind == "str"
        flat = needs_trampoline(fn)
        target = f"ps_bind_target_{index}"
        struct = f"ps_bind_args_{index}"

        # The flat parameter list of the target: a str argument is a length
        # and a data pointer, and a str result adds two out-pointers.
        target_types: list[str] = []
        arg_fields: list[str] = []
        call_args: list[str] = []
        for i, param in enumerate(params):
            kind = param["type"]["kind"]
            if flat and kind == "str":
                target_types.extend(["int64_t", "char *"])
                arg_fields.append(f"    int64_t p{i}_len;")
                arg_fields.append(f"    char *p{i}_data;")
                call_args.append(f"args->p{i}_len")
                call_args.append(f"args->p{i}_data")
            else:
                c_type = _C_TYPE_MAP[kind]
                target_types.append(c_type)
                arg_fields.append(f"    {c_type} p{i};")
                call_args.append(f"args->p{i}")
        if flat and ret_is_str:
            target_types.extend(["int64_t *", "char **"])
            arg_fields.append("    int64_t out_len;")
            arg_fields.append("    char *out_data;")
            call_args.append("&args->out_len")
            call_args.append("&args->out_data")
        c_ret = "void" if (flat and ret_is_str) else _C_TYPE_MAP[ret_kind]
        # On the flat path the target keeps the function's own return type,
        # except for a str result, which leaves through the out-pointers.

        # The target comes in as a pointer the loader resolved with dlsym on
        # the library itself, not as a call to a symbol: a compiled function
        # named `read` or `write` would otherwise be interposed by the libc
        # symbol already in the process' global scope.
        lines.append(f"/* {name} */")
        rendered_types = ", ".join(target_types) or "void"
        lines.append(f"typedef {c_ret} (*{target})({rendered_types});")
        lines.append("")
        lines.append("typedef struct {")
        lines.append(f"    {target} target;")
        lines.extend(arg_fields)
        if ret_kind != "none" and not (flat and ret_is_str):
            lines.append(f"    {_C_TYPE_MAP[ret_kind]} result;")
        lines.append(f"}} {struct};")
        lines.append("")
        lines.append(f"static int32_t ps_bind_call_{index}(void *env) {{")
        lines.append(f"    {struct} *args = ({struct} *)env;")
        call_str = ", ".join(call_args)
        if ret_kind == "none":
            lines.append(f"    args->target({call_str});")
        elif flat and ret_is_str:
            lines.append(f"    args->target({call_str});")
        else:
            lines.append(f"    args->result = args->target({call_str});")
        lines.append("    return 0;")
        lines.append("}")
        lines.append("")

        # The wrapper: the target pointer first, then the parameters. A str
        # argument arrives as a length and a data pointer, and a str result
        # leaves through the two out-pointers.
        wrap_types = [f"{target} target"]
        for i, param in enumerate(params):
            kind = param["type"]["kind"]
            if flat and kind == "str":
                wrap_types.append(f"int64_t p{i}_len")
                wrap_types.append(f"char *p{i}_data")
            else:
                wrap_types.append(f"{_C_TYPE_MAP[kind]} p{i}")
        if flat and ret_is_str:
            wrap_types.append("int64_t *out_len")
            wrap_types.append("char **out_data")
        lines.append(f"{c_ret} {shim_symbol(name)}({', '.join(wrap_types)}) {{")
        lines.append(f"    {struct} args;")
        lines.append("    args.target = target;")
        for i, param in enumerate(params):
            kind = param["type"]["kind"]
            if flat and kind == "str":
                lines.append(f"    args.p{i}_len = p{i}_len;")
                lines.append(f"    args.p{i}_data = p{i}_data;")
            else:
                lines.append(f"    args.p{i} = p{i};")
        if flat and ret_is_str:
            lines.append("    args.out_len = 0;")
            lines.append("    args.out_data = NULL;")
            lines.append(f"    if (ps_bind_guard(ps_bind_call_{index}, &args) != 0) {{")
            lines.append("        *out_len = 0;")
            lines.append("        *out_data = NULL;")
            lines.append("        return;")
            lines.append("    }")
            lines.append("    *out_len = args.out_len;")
            lines.append("    *out_data = args.out_data;")
            lines.append("    return;")
        elif ret_kind != "none":
            assert ret_kind in _C_ZERO_MAP, ret_kind
            lines.append(f"    args.result = {_C_ZERO_MAP[ret_kind]};")
            lines.append(f"    if (ps_bind_guard(ps_bind_call_{index}, &args) != 0) {{")
            lines.append(f"        return {_C_ZERO_MAP[ret_kind]};")
            lines.append("    }")
            lines.append("    return args.result;")
        lines.append(f"    ps_bind_guard(ps_bind_call_{index}, &args);")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def render_binding_module(
    module_name: str,
    shlib_filename: str,
    emitted: list[dict[str, Any]],
    skipped: list[tuple[str, str]],
) -> str:
    """Generate the Python loader module text."""
    lines: list[str] = []

    # Module docstring
    lines.append(f'"""Bindings for {module_name}.')
    lines.append("")
    lines.append("An exception raised inside a compiled function is re-raised in the")
    lines.append("caller as the Python class of the same name (`ZeroDivisionError`,")
    lines.append("`ValueError`, ...), or as `CompiledError` with `compiled_class` naming")
    lines.append("the compiled class when it has no builtin counterpart.")
    if skipped:
        lines.append("")
        lines.append("Skipped:")
        for name, reason in skipped:
            lines.append(f"- {name}: {reason}")
    lines.append('"""')

    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("import builtins")
    lines.append("import ctypes")
    lines.append("import sys")
    lines.append("from pathlib import Path")
    lines.append("")
    lines.append("")
    lines.append("class CompiledError(RuntimeError):")
    lines.append('    """Raised when a compiled function encounters an error."""')
    lines.append("")
    lines.append("")
    lines.append("_LIB_DIR = Path(__file__).resolve().parent")
    lines.append('if sys.platform == "darwin":')
    lines.append("    _LIB_CANDIDATES = [")
    lines.append(f"        {repr(shlib_filename)},")
    lines.append(f'        "lib{module_name}.dylib",')
    lines.append(f'        "{module_name}.dylib",')
    lines.append(f'        "lib{module_name}.so",')
    lines.append(f'        "{module_name}.so",')
    lines.append("    ]")
    lines.append('elif sys.platform == "win32":')
    lines.append("    _LIB_CANDIDATES = [")
    lines.append(f"        {repr(shlib_filename)},")
    lines.append(f'        "{module_name}.dll",')
    lines.append(f'        "lib{module_name}.dll",')
    lines.append("    ]")
    lines.append("else:")
    lines.append("    _LIB_CANDIDATES = [")
    lines.append(f"        {repr(shlib_filename)},")
    lines.append(f'        "lib{module_name}.so",')
    lines.append(f'        "{module_name}.so",')
    lines.append(f'        "lib{module_name}.dylib",')
    lines.append(f'        "{module_name}.dylib",')
    lines.append("    ]")
    lines.append("")
    lines.append("_LIB_PATH = None")
    lines.append("for _cand in _LIB_CANDIDATES:")
    lines.append("    _p = _LIB_DIR / _cand")
    lines.append("    if _p.is_file():")
    lines.append("        _LIB_PATH = _p")
    lines.append("        break")
    lines.append("")
    lines.append("if _LIB_PATH is None:")
    lines.append("    _LIB_PATH = _LIB_DIR / _LIB_CANDIDATES[0]")
    lines.append("")
    lines.append("_lib = ctypes.CDLL(str(_LIB_PATH))")
    lines.append("")
    lines.append("# The exception guard the shim parks a raise in; see CompiledError.")
    lines.append("_bind_pending = _lib.ps_bind_pending")
    lines.append("_bind_pending.argtypes = []")
    lines.append("_bind_pending.restype = ctypes.c_int32")
    lines.append("_bind_name = _lib.ps_bind_name")
    lines.append("_bind_name.argtypes = []")
    lines.append("_bind_name.restype = ctypes.c_char_p")
    lines.append("_bind_message = _lib.ps_bind_message")
    lines.append("_bind_message.argtypes = []")
    lines.append("_bind_message.restype = ctypes.c_char_p")
    lines.append("_bind_clear = _lib.ps_bind_clear")
    lines.append("_bind_clear.argtypes = []")
    lines.append("_bind_clear.restype = None")
    lines.append("")
    lines.append("")
    lines.append("def _raise_compiled_error() -> None:")
    lines.append('    """Raise the exception the last compiled call parked at the C ABI."""')
    lines.append('    name = (_bind_name() or b"").decode("utf-8", "replace")')
    lines.append('    message = (_bind_message() or b"").decode("utf-8", "replace")')
    lines.append("    _bind_clear()")
    lines.append("    cls = getattr(builtins, name, None)")
    lines.append("    if isinstance(cls, type) and issubclass(cls, BaseException):")
    lines.append("        raise cls(message) if message else cls()")
    lines.append("    error = CompiledError(message)")
    lines.append("    error.compiled_class = name")
    lines.append("    raise error")
    lines.append("")
    lines.append("")
    lines.append("def _check() -> None:")
    lines.append("    if _bind_pending():")
    lines.append("        _raise_compiled_error()")
    lines.append("")
    lines.append("")
    lines.append("# Initialize runtime state and module globals")
    lines.append("_lib.ps_bind_init.argtypes = []")
    lines.append("_lib.ps_bind_init.restype = ctypes.c_int32")
    lines.append("_lib.ps_bind_init()")
    lines.append("_check()")
    lines.append("")

    all_names = ["CompiledError"]

    needs_str = any(needs_trampoline(fn) for fn in emitted)
    if needs_str:
        # A str never crosses the ABI as a struct. The compiled function's
        # own signature carries it as a 32-byte aggregate, and the C ABI and
        # the IR lowering disagree about where an aggregate that size lives;
        # the emitter writes a flat-ABI trampoline (`psb.<name>`) for every
        # exported function that carries one, and the loader calls that: a
        # str argument is a byte length and a data pointer, a str result is
        # read out of two out-pointers, and the result buffer is freed
        # through the shim's ps_bind_str_free.
        lines.append("_str_free = _lib.ps_bind_str_free")
        lines.append("_str_free.argtypes = [ctypes.c_void_p]")
        lines.append("_str_free.restype = None")
        lines.append("")
        lines.append("")
        lines.append("def _unpack_str(payload_len: int, payload_data: int | None) -> str:")
        lines.append('    """The str one result buffer holds, its memory freed after the read."""')
        lines.append("    if not payload_data:")
        lines.append('        return ""')
        lines.append("    data = ctypes.string_at(payload_data, payload_len) if payload_len > 0 else b\"\"")
        lines.append("    _str_free(payload_data)")
        lines.append('    return data.decode("utf-8")')
        lines.append("")
        lines.append("")

    for fn in emitted:
        name = fn["name"]
        params = fn.get("params", [])
        ret_type = fn.get("returnType", {})
        ret_kind = ret_type.get("kind", "none")
        flat = needs_trampoline(fn)
        target_symbol = trampoline_symbol(name) if flat else _fn_symbol(name)

        # The target signature: a str argument is the byte length and the
        # data pointer on the flat path, otherwise the logical ctypes type.
        argtypes = []
        for p in params:
            if flat and p["type"]["kind"] == "str":
                argtypes.extend(["ctypes.c_int64", "ctypes.c_char_p"])
            else:
                argtypes.append(_CTYPES_TYPE_MAP[p["type"]["kind"]])
        restype = "None" if (flat and ret_kind == "str") else _CTYPES_TYPE_MAP[ret_kind]

        # The guarded wrapper takes the resolved function pointer first, so
        # its ctypes shape is the logical one with a leading c_void_p.
        bind_argtypes = ["ctypes.c_void_p"]
        for p in params:
            if flat and p["type"]["kind"] == "str":
                bind_argtypes.extend(["ctypes.c_int64", "ctypes.c_char_p"])
            else:
                bind_argtypes.append(_CTYPES_TYPE_MAP[p["type"]["kind"]])
        if flat and ret_kind == "str":
            # The result buffer is a plain address, not a c_char_p: ctypes
            # would turn a c_char_p into a bytes copy and the address the
            # compiled side wrote would be lost.
            bind_argtypes.extend(["ctypes.POINTER(ctypes.c_int64)", "ctypes.POINTER(ctypes.c_void_p)"])
        bind_restype = restype

        sig_parts: list[str] = []
        call_parts: list[str] = []
        pack_parts: list[str] = []
        keep_parts: list[str] = []
        for p in params:
            pname = p["name"]
            kind = p["type"]["kind"]
            ptype_str = _PY_TYPE_MAP[kind]
            default = p.get("default")
            if isinstance(default, dict) and default.get("kind") == "const":
                val_repr = _python_const_repr(p["type"], default.get("value"))
                sig_parts.append(f"{pname}: {ptype_str} = {val_repr}")
            else:
                sig_parts.append(f"{pname}: {ptype_str}")
            if kind == "str":
                pack_parts.append(f"    __{pname} = {pname}.encode('utf-8')")
                call_parts.append(f"len(__{pname})")
                call_parts.append(f"__{pname}")
                # ctypes keeps a c_char_p alive through the call: the bytes
                # object stays referenced by the local, and the compiled
                # code must not retain the pointer past the call.
                keep_parts.append(f"    del __{pname}")
            else:
                call_parts.append(pname)

        ret_annot = _PY_TYPE_MAP[ret_kind]
        sig_str = ", ".join(sig_parts)
        call_str = ", ".join(call_parts)

        lines.append(f"# {name}")
        lines.append(f"_{name}_impl = getattr(_lib, {repr(target_symbol)})")
        lines.append(f"_{name}_impl.argtypes = [{', '.join(argtypes)}]")
        lines.append(f"_{name}_impl.restype = {restype}")
        lines.append(f"_{name}_ptr = ctypes.cast(_{name}_impl, ctypes.c_void_p)")
        lines.append(f"_{name} = getattr(_lib, {repr(shim_symbol(name))})")
        lines.append(f"_{name}.argtypes = [{', '.join(bind_argtypes)}]")
        lines.append(f"_{name}.restype = {bind_restype}")
        lines.append("")
        lines.append("")
        lines.append(f"def {name}({sig_str}) -> {ret_annot}:")
        for pack_line in pack_parts:
            lines.append(pack_line)
        args = ", ".join([f"_{name}_ptr", call_str]) if call_str else f"_{name}_ptr"
        if ret_kind == "none":
            lines.append(f"    _{name}({args})")
            lines.append("    _check()")
        elif flat and ret_kind == "str":
            lines.append("    _out_len = ctypes.c_int64(0)")
            lines.append("    _out_data = ctypes.c_void_p(None)")
            lines.append(f"    _{name}({args}, ctypes.byref(_out_len), ctypes.byref(_out_data))")
            lines.append("    _check()")
            lines.append("    return _unpack_str(_out_len.value, _out_data.value)")
        elif ret_kind == "str":
            lines.append(f"    _result = _{name}({args})")
            lines.append("    _check()")
            lines.append("    return _unpack_str(_result)")
        else:
            lines.append(f"    _result = _{name}({args})")
            lines.append("    _check()")
            lines.append("    return _result")
        lines.append("")
        lines.append(f"{name}.argtypes = [{', '.join(argtypes)}]")
        lines.append(f"{name}.restype = {restype}")
        lines.append(f"{name}.raw = _{name}_impl")
        lines.append("")
        all_names.append(name)

    lines.append("__all__ = [")
    for item in all_names:
        lines.append(f"    {repr(item)},")
    lines.append("]")
    lines.append("")

    return "\n".join(lines)
