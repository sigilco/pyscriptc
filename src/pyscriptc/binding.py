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

_SUPPORTED_PARAM_KINDS = frozenset({"int", "float", "bool"})
_SUPPORTED_RETURN_KINDS = frozenset({"int", "float", "bool", "none"})

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
}


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
                if pkind in ("str", "bytes"):
                    reasons.append(
                        f"parameter {param['name']!r} has unsupported type '{rendered}' "
                        "(str/bytes C ABI not stabilized for bindings)"
                    )
                else:
                    reasons.append(f"parameter {param['name']!r} has unsupported type '{rendered}'")

        rkind = fn.get("returnType", {}).get("kind")
        if rkind not in _SUPPORTED_RETURN_KINDS:
            rendered = _render_type(fn.get("returnType", {}))
            if rkind in ("str", "bytes"):
                reasons.append(
                    f"return type has unsupported type '{rendered}' (str/bytes C ABI not stabilized for bindings)"
                )
            else:
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


def render_binding_module(
    module_name: str,
    shlib_filename: str,
    emitted: list[dict[str, Any]],
    skipped: list[tuple[str, str]],
) -> str:
    """Generate the Python loader module text."""
    lines: list[str] = []

    # Module docstring
    if skipped:
        lines.append(f'"""Bindings for {module_name}.')
        lines.append("")
        lines.append("Skipped:")
        for name, reason in skipped:
            lines.append(f"- {name}: {reason}")
        lines.append('"""')
    else:
        lines.append(f'"""Bindings for {module_name}."""')

    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
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
    lines.append("# Initialize runtime state and module globals")
    lines.append('if hasattr(_lib, "pyscriptc_main"):')
    lines.append("    _lib.pyscriptc_main.argtypes = []")
    lines.append("    _lib.pyscriptc_main.restype = ctypes.c_int32")
    lines.append("    _lib.pyscriptc_main()")
    lines.append('elif hasattr(_lib, "pyscriptc_init"):')
    lines.append("    _lib.pyscriptc_init.argtypes = []")
    lines.append("    _lib.pyscriptc_init.restype = ctypes.c_int32")
    lines.append("    _lib.pyscriptc_init()")
    lines.append("")

    all_names = ["CompiledError"]

    for fn in emitted:
        name = fn["name"]
        symbol = _fn_symbol(name)
        params = fn.get("params", [])
        ret_type = fn.get("returnType", {})

        argtypes = [_CTYPES_TYPE_MAP[p["type"]["kind"]] for p in params]
        restype = _CTYPES_TYPE_MAP[ret_type.get("kind", "none")]

        sig_parts: list[str] = []
        call_parts: list[str] = []
        for p in params:
            pname = p["name"]
            ptype_str = _PY_TYPE_MAP[p["type"]["kind"]]
            default = p.get("default")
            if isinstance(default, dict) and default.get("kind") == "const":
                val_repr = _python_const_repr(p["type"], default.get("value"))
                sig_parts.append(f"{pname}: {ptype_str} = {val_repr}")
            else:
                sig_parts.append(f"{pname}: {ptype_str}")
            call_parts.append(pname)

        ret_annot = _PY_TYPE_MAP[ret_type.get("kind", "none")]
        sig_str = ", ".join(sig_parts)
        call_str = ", ".join(call_parts)

        lines.append(f"# {name}")
        lines.append(f"_{name} = getattr(_lib, {repr(symbol)})")
        lines.append(f"_{name}.argtypes = [{', '.join(argtypes)}]")
        lines.append(f"_{name}.restype = {restype}")
        lines.append("")
        lines.append("")
        lines.append(f"def {name}({sig_str}) -> {ret_annot}:")
        lines.append(f"    return _{name}({call_str})")
        lines.append("")
        lines.append(f"{name}.argtypes = _{name}.argtypes")
        lines.append(f"{name}.restype = _{name}.restype")
        lines.append(f"{name}.raw = _{name}")
        lines.append("")
        all_names.append(name)

    lines.append("__all__ = [")
    for item in all_names:
        lines.append(f"    {repr(item)},")
    lines.append("]")
    lines.append("")

    return "\n".join(lines)
