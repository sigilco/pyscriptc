"""Typeshed stub reader: signatures and canonical argument expressions.

The stubs shipped with mypy are the source of argument types. Version and platform branches are
resolved for CPython 3.14 on Linux, so the chosen overloads match the oracle interpreter.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import mypy

STDLIB = Path(mypy.__file__).parent / "typeshed" / "stdlib"
PY_VERSION = (3, 14)
PLATFORM = "linux"

# Candidate expressions per simple type name, in preference order. Scalars (int, float, bool, str)
# become probe parameters so the compiler cannot fold them away; the rest are inlined.
SIMPLE: dict[str, list[str]] = {
    "int": ["3", "7", "0"],
    "float": ["0.5", "2.5", "1.0"],
    "bool": ["True", "False"],
    "str": ["'hello world'", "'a'", "'ab'", "''", "'a+'", "'{\"a\": 1}'", "'3'", "'<H'", "'%Y'"],
    "bytes": ["b'ab'", "b''"],
    "bytearray": ["bytearray(b'ab')"],
    "complex": ["complex(3, 4)"],
    "object": ["3"],
    "Any": ["3"],
    "None": ["None"],
    "NoneType": ["None"],
    "memoryview": ["memoryview(b'ab')"],
    "slice": ["slice(1, 3)"],
    "range": ["range(3)"],
    "type": ["int"],
}
ALIASES: dict[str, list[str]] = {
    "SupportsInt": SIMPLE["int"],
    "SupportsIndex": SIMPLE["int"],
    "ConvertibleToInt": SIMPLE["int"],
    "SupportsFloat": SIMPLE["float"],
    "ConvertibleToFloat": SIMPLE["float"],
    "StrPath": ["'ab/cd'"],
    "StrOrBytesPath": ["'ab/cd'"],
    "FileDescriptorOrPath": ["'ab/cd'"],
    "BytesPath": ["b'ab/cd'"],
    "PathLike": ["'ab/cd'"],
    "ReadableBuffer": SIMPLE["bytes"],
    "Buffer": SIMPLE["bytes"],
    "WriteableBuffer": SIMPLE["bytearray"],
    "SupportsRichComparison": SIMPLE["int"],
    "SupportsRichComparisonT": SIMPLE["int"],
    "SupportsAbs": SIMPLE["int"],
    "SupportsKeysAndGetItem": ["{'a': 1}"],
    "LiteralString": SIMPLE["str"],
    "Unused": ["None"],
    "Incomplete": SIMPLE["int"],
    "FileDescriptorLike": SIMPLE["int"],
    "Callable": ["abs"],
    "Hashable": SIMPLE["int"],
    "Sized": ["[1, 2]"],
    "SupportsWrite": ["io.StringIO()"],
    "SupportsRead": ["io.StringIO('ab')"],
    "SupportsReadline": ["io.StringIO('ab')"],
    "TextIO": ["io.StringIO('ab')"],
    "IO": ["io.StringIO('ab')"],
    "BinaryIO": ["io.BytesIO(b'ab')"],
    "StrOrLiteralStr": SIMPLE["str"],
    "_TranslateTable": ["{97: 98}"],
}
SEQ_LIKE = {"list", "List", "Sequence", "MutableSequence", "Iterable", "Collection", "Reversible", "Container"}
SET_LIKE = {"set", "Set", "AbstractSet", "MutableSet", "frozenset"}
MAP_LIKE = {"dict", "Dict", "Mapping", "MutableMapping", "SupportsKeysAndGetItem", "OrderedDict", "defaultdict"}
GENERIC_POOL = ["3", "'ab'", "2.5", "b'ab'", "[1, 2]", "None"]


@dataclass(frozen=True)
class Param:
    name: str
    ann: ast.expr | None
    required: bool
    kwonly: bool
    posonly: bool


@dataclass(frozen=True)
class Sig:
    params: tuple[Param, ...]  # self and cls removed


def _eval_test(test: ast.expr) -> bool | None:
    """Resolve `sys.version_info` and `sys.platform` guards. None means unknown."""
    if isinstance(test, ast.BoolOp):
        parts = [_eval_test(v) for v in test.values]
        if isinstance(test.op, ast.And):
            return False if False in parts else (None if None in parts else True)
        return True if True in parts else (None if None in parts else False)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _eval_test(test.operand)
        return None if inner is None else not inner
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left, op, right = test.left, test.ops[0], test.comparators[0]
        text = ast.unparse(left)
        if text == "sys.version_info" and isinstance(right, ast.Tuple):
            want = tuple(e.value for e in right.elts if isinstance(e, ast.Constant))
            have = PY_VERSION[: len(want)]
            table = {ast.GtE: have >= want, ast.Gt: have > want, ast.Lt: have < want, ast.LtE: have <= want}
            return table.get(type(op))
        if text == "sys.platform" and isinstance(right, ast.Constant):
            same = right.value == PLATFORM
            return same if isinstance(op, ast.Eq) else (not same if isinstance(op, ast.NotEq) else None)
        if text == "sys.platform" and isinstance(op, ast.In):
            return None
    if isinstance(test, ast.Call) and ast.unparse(test.func) == "sys.platform.startswith" and test.args:
        arg = test.args[0]
        return PLATFORM.startswith(arg.value) if isinstance(arg, ast.Constant) else None
    return None


def _flatten(body: list[ast.stmt]) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            verdict = _eval_test(stmt.test)
            if verdict is None or verdict:
                out.extend(_flatten(stmt.body))
            else:
                out.extend(_flatten(stmt.orelse))
        else:
            out.append(stmt)
    return out


@dataclass
class Stub:
    functions: dict[str, list[ast.FunctionDef]]
    classes: dict[str, ast.ClassDef]
    names: dict[str, str]  # public name -> "function" | "class" | "exception" | "constant"
    aliases: dict[str, ast.expr]


def _exception_like(node: ast.ClassDef) -> bool:
    return any(
        ast.unparse(base).split(".")[-1].endswith(("Error", "Exception", "Warning", "Exit")) for base in node.bases
    )


@cache
def load(module: str) -> Stub | None:
    base = STDLIB / Path(*module.split("."))
    path = base.with_suffix(".pyi")
    if not path.exists():
        path = base / "__init__.pyi"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    stub = Stub({}, {}, {}, {})
    for stmt in _flatten(tree.body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            stub.functions.setdefault(stmt.name, []).append(stmt)  # type: ignore[arg-type]
            stub.names[stmt.name] = "function"
        elif isinstance(stmt, ast.ClassDef):
            stub.classes[stmt.name] = stmt
            stub.names[stmt.name] = "exception" if _exception_like(stmt) else "class"
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if "TypeAlias" in ast.unparse(stmt.annotation) and stmt.value is not None:
                stub.aliases[stmt.target.id] = stmt.value
            else:
                stub.names.setdefault(stmt.target.id, "constant")
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    stub.aliases[target.id] = stmt.value
                    stub.names.setdefault(target.id, "constant")
    return stub


def class_methods(node: ast.ClassDef) -> dict[str, list[ast.FunctionDef]]:
    out: dict[str, list[ast.FunctionDef]] = {}
    for stmt in _flatten(node.body):
        if isinstance(stmt, ast.FunctionDef):
            out.setdefault(stmt.name, []).append(stmt)
    return out


def signature(fn: ast.FunctionDef, method: bool) -> Sig:
    args = fn.args
    params: list[Param] = []
    positional = [(a, True) for a in args.posonlyargs] + [(a, False) for a in args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, ((arg, posonly), default) in enumerate(zip(positional, defaults, strict=True)):
        if method and index == 0 and arg.arg in {"self", "cls"}:
            continue
        params.append(Param(arg.arg, arg.annotation, default is None, False, posonly))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params.append(Param(arg.arg, arg.annotation, default is None, True, False))
    return Sig(tuple(params))


def _decorators(fn: ast.FunctionDef) -> set[str]:
    return {ast.unparse(d).split(".")[-1] for d in fn.decorator_list}


def is_static_or_class(fn: ast.FunctionDef) -> bool:
    return bool(_decorators(fn) & {"staticmethod", "classmethod"})


def exprs_for(node: ast.expr | None, stub: Stub | None, depth: int = 0) -> list[str]:
    """Canonical argument expressions for an annotation, best first. Empty means unknown."""
    if node is None or depth > 4:
        return []
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            try:
                return exprs_for(ast.parse(node.value, mode="eval").body, stub, depth + 1)
            except SyntaxError:
                return []
        return ["None"] if node.value is None else []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = exprs_for(node.left, stub, depth + 1)
        right = exprs_for(node.right, stub, depth + 1)
        return [*left, *right]
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    if isinstance(node, ast.Subscript):
        head = ast.unparse(node.value).split(".")[-1]
        inner = node.slice
        elts = list(inner.elts) if isinstance(inner, ast.Tuple) else [inner]
        if head in {"Optional", "Union", "Annotated"}:
            return [e for sub in elts[: 1 if head == "Annotated" else None] for e in exprs_for(sub, stub, depth + 1)]
        if head == "Literal":
            return [ast.unparse(e) for e in elts if isinstance(e, ast.Constant)]
        if head in {"type", "Type"}:
            return ["int"]
        if head == "Callable":
            return ["abs"]
        if head in SEQ_LIKE | SET_LIKE | {"Iterator", "tuple", "Tuple"}:
            if head in {"tuple", "Tuple"} and not (len(elts) == 2 and ast.unparse(elts[1]) == "..."):
                parts = [(exprs_for(e, stub, depth + 1) or [None])[0] for e in elts]
                if any(p is None for p in parts):
                    return []
                return ["(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"]  # type: ignore[arg-type]
            inner_exprs = exprs_for(elts[0], stub, depth + 1)
            if not inner_exprs:
                return []
            pair = inner_exprs[:2] if len(inner_exprs) > 1 else inner_exprs * 2
            body = ", ".join(pair)
            if head in {"tuple", "Tuple"}:
                return [f"({body})"]
            if head in SET_LIKE:
                return ["{" + body + "}"]
            if head == "Iterator":
                return [f"iter([{body}])"]
            return [f"[{body}]"]
        if head in MAP_LIKE and len(elts) == 2:
            keys, values = exprs_for(elts[0], stub, depth + 1), exprs_for(elts[1], stub, depth + 1)
            return ["{" + f"{keys[0]}: {values[0]}" + "}"] if keys and values else []
        return exprs_for(node.value, stub, depth + 1)
    if name in SIMPLE:
        return list(SIMPLE[name])
    if name in ALIASES:
        return list(ALIASES[name])
    if name in SEQ_LIKE:
        return ["[3, 7]"]
    if name in SET_LIKE:
        return ["{3, 7}"]
    if name in MAP_LIKE:
        return ["{'a': 1}"]
    if name in {"Iterator"}:
        return ["iter([3, 7])"]
    if name in {"tuple", "Tuple"}:
        return ["(3, 7)"]
    if stub is not None and name in stub.aliases:
        return exprs_for(stub.aliases[name], stub, depth + 1)
    if name.startswith("_T") or name in {"T", "AnyStr", "_AnyStr", "Self"}:
        return list(SIMPLE["int"]) if name != "AnyStr" else list(SIMPLE["str"])
    return []


def ranked_product(pools: list[list[str]], cap: int) -> list[tuple[str, ...]]:
    """Combinations ordered by how far each pick is from the pool head, so varied inputs surface early."""
    width = 9 if len(pools) <= 1 else 4 if len(pools) == 2 else 3
    trimmed = [pool[:width] for pool in pools]
    combos = itertools.product(*[range(len(pool)) for pool in trimmed])
    ranked = sorted(combos, key=lambda idx: (sum(idx), idx))[:cap]
    return [tuple(trimmed[i][j] for i, j in enumerate(idx)) for idx in ranked]


def candidate_calls(
    sigs: list[Sig], stub: Stub | None, limit: int = 24, optional_first: bool = False
) -> list[tuple[list[str], dict[str, str]]]:
    """Argument lists (positional expressions, keyword expressions) to try, best first.

    With `optional_first`, a signature without required positional parameters is also tried with its first
    optional positional parameter filled, so constructors yield non-empty receivers.
    """
    out: list[tuple[list[str], dict[str, str]]] = []
    seen: set[str] = set()
    for sig in sigs:
        variants = [[p for p in sig.params if p.required]]
        if optional_first and not any(p.required and not p.kwonly for p in sig.params):
            first = next((p for p in sig.params if not p.required and not p.kwonly), None)
            if first is not None and exprs_for(first.ann, stub):
                variants.insert(0, [*variants[0], first])
        for chosen in variants:
            pools = [exprs_for(p.ann, stub) or GENERIC_POOL[:3] for p in chosen]
            for combo in ranked_product(pools, 24):
                positional = [expr for p, expr in zip(chosen, combo, strict=True) if not p.kwonly]
                keywords = {p.name: expr for p, expr in zip(chosen, combo, strict=True) if p.kwonly}
                key = repr((positional, sorted(keywords.items())))
                if key not in seen:
                    seen.add(key)
                    out.append((positional, keywords))
    return out[:limit]


def generic_calls(arities: tuple[int, ...] = (0, 1, 2, 3)) -> list[tuple[list[str], dict[str, str]]]:
    out: list[tuple[list[str], dict[str, str]]] = []
    for count in arities:
        for combo in itertools.islice(itertools.product(GENERIC_POOL[:3], repeat=count), 4):
            out.append((list(combo), {}))
    return out
