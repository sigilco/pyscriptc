"""Turn an oracle value into a typed local plus code that folds it to the int a probe returns."""

from __future__ import annotations

import importlib
from dataclasses import dataclass

INT_LIMIT = 2**62


@dataclass(frozen=True)
class Desc:
    ann: str  # annotation of the result local, "" when the result is not annotatable
    fold: str  # int | bool | float | str | bytes | seq-int | seq-str | len | handle | none | exec


def _ann(value: object, depth: int = 0) -> str:
    if depth > 3:
        return ""
    if value is None:
        return "None"
    kind = type(value)
    if kind in (bool, int, float, str, bytes, bytearray, complex, range, slice, memoryview):
        return kind.__name__
    if kind in (list, set, frozenset):
        items = list(value)  # type: ignore[call-overload]
        inner = {_ann(item, depth + 1) for item in items}
        if len(inner) > 1 or "" in inner:
            return ""
        return f"{kind.__name__}[{inner.pop() if inner else 'int'}]"
    if kind is tuple:
        items = list(value)  # type: ignore[call-overload]
        parts = [_ann(item, depth + 1) for item in items]
        if not items or "" in parts:
            return "tuple[int, ...]" if not items else ""
        return f"tuple[{', '.join(parts)}]"
    if kind is dict:
        items = list(value.items())  # type: ignore[attr-defined]
        keys = {_ann(k, depth + 1) for k, _ in items}
        values = {_ann(v, depth + 1) for _, v in items}
        if len(keys) > 1 or len(values) > 1 or "" in keys | values:
            return ""
        return f"dict[{keys.pop() if keys else 'str'}, {values.pop() if values else 'int'}]"
    module, qual = kind.__module__, kind.__qualname__
    if "<" in qual or qual.startswith("_"):
        return ""
    if module == "builtins":
        return qual
    try:
        target: object = importlib.import_module(module)
        for part in qual.split("."):
            target = getattr(target, part)
    except ImportError, AttributeError:
        return ""
    return f"{module}.{qual}" if target is kind else ""


def describe(value: object) -> Desc:
    ann = _ann(value)
    if value is None:
        return Desc("None", "none")
    if not ann:
        return Desc("", "exec")
    if isinstance(value, bool):
        return Desc(ann, "bool")
    if type(value) is int:
        return Desc(ann, "int")
    if type(value) is float:
        return Desc(ann, "float")
    if type(value) is str:
        return Desc(ann, "str")
    if type(value) is bytes:
        return Desc(ann, "bytes")
    if type(value) in (list, tuple) and value and all(type(x) is int and abs(x) < 10**9 for x in value):  # type: ignore[attr-defined]
        return Desc(ann, "seq-int")
    if type(value) in (list, tuple) and value and all(type(x) is str for x in value):  # type: ignore[attr-defined]
        return Desc(ann, "seq-str")
    if type(value) in (list, tuple, set, frozenset, dict):
        return Desc(ann, "len")
    return Desc(ann, "handle")


def _big(value: object) -> bool:
    return isinstance(value, int) and abs(value) >= INT_LIMIT


def fold_lines(desc: Desc, var: str, value: object) -> list[str]:
    """Statements (unindented) that return an int derived from `var`."""
    match desc.fold:
        case "int":
            return [f"return {var} % 1000003"] if _big(value) else [f"return {var}"]
        case "bool":
            return [f"if {var}:", "    return 1", "return 0"]
        case "float":
            return [
                f"if {var} != {var}:",
                "    return 2",
                f"if {var} > 1e15:",
                "    return 3",
                f"if {var} < -1e15:",
                "    return 4",
                f"return int({var} * 1000.0)",
            ]
        case "str":
            return [
                f"if len({var}) == 0:",
                "    return 0",
                f"return len({var}) * 2000000 + ord({var}[0]) * 31 + ord({var}[len({var}) - 1])",
            ]
        case "bytes":
            return [
                f"if len({var}) == 0:",
                "    return 0",
                f"return len({var}) * 1000 + {var}[0] * 7 + {var}[len({var}) - 1]",
            ]
        case "seq-int":
            return [f"return len({var}) * 1000003 + {var}[0] % 1000003"]
        case "seq-str":
            return [f"return len({var}) * 1000003 + len({var}[0])"]
        case "len":
            return [f"return len({var})"]
        case _:
            return ["return 1"]


def shape_lines(desc: Desc, var: str) -> list[str]:
    """Fallback fold for nondeterministic results: only the type is checked."""
    if desc.ann and desc.fold not in {"none", "exec"}:
        target = desc.ann.split("[")[0]
        return [f"if isinstance({var}, {target}):", "    return 1", "return 0"]
    return ["return 1"]
