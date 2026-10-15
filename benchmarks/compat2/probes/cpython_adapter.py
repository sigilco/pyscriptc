"""Adapt a small pure subset of CPython Lib/test cases into value probes.

Each `test_*` method contributes the longest prefix of statements that is pure (literals, arithmetic,
comparisons, a short builtin whitelist) and that assigns names of annotatable types. Assertions become
`if not (cond): return 0`; the probe ends with `return 1`. A method is kept only when the probe returns 1
under CPython, twice. Skipped and dropped counts go to the manifest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

from benchmarks.compat2.outcomes import WEIGHTS, Probe

from . import engine
from .fold import describe

TESTS = Path(__file__).resolve().parent / "cpython_tests"


def pinned_dir(oracle: str) -> Path:
    """The pinned directory whose tag is a release of `oracle`: cpython_tests/ holds 3.12, py314/ the secondary."""
    for candidate in (TESTS, *sorted(p for p in TESTS.iterdir() if p.is_dir() and (p / "PIN.json").exists())):
        pin = json.loads((candidate / "PIN.json").read_text(encoding="utf-8"))
        if pin["tag"].startswith(f"v{oracle}."):
            return candidate
    raise SystemExit(f"no pinned CPython tests for oracle {oracle}")


CAP = 80
SECTION = {
    "grammar": "lexical_analysis.html",
    "augassign": "simple_stmts.html#augmented-assignment-statements",
    "compare": "expressions.html#comparisons",
    "binop": "expressions.html#binary-arithmetic-operations",
    "unary": "expressions.html#unary-arithmetic-and-bitwise-operations",
    "int": "datamodel.html#the-standard-type-hierarchy",
    "float": "datamodel.html#the-standard-type-hierarchy",
    "bool": "datamodel.html#the-standard-type-hierarchy",
}
CALLS = {"len", "abs", "min", "max", "int", "float", "str", "bool", "divmod", "pow", "round", "sum"}
ASSERTS = {
    "assertEqual": "({0}) == ({1})",
    "assertEquals": "({0}) == ({1})",
    "assertNotEqual": "({0}) != ({1})",
    "assertTrue": "{0}",
    "assertFalse": "not ({0})",
    "assertIs": "({0}) is ({1})",
    "assertIsNot": "({0}) is not ({1})",
    "assertIn": "({0}) in ({1})",
    "assertNotIn": "({0}) not in ({1})",
    "assertGreater": "({0}) > ({1})",
    "assertGreaterEqual": "({0}) >= ({1})",
    "assertLess": "({0}) < ({1})",
    "assertLessEqual": "({0}) <= ({1})",
}
ARITY = {"assertTrue": 1, "assertFalse": 1}


def _pure(node: ast.AST, names: set[str]) -> bool:
    match node:
        case ast.Constant(value=v):
            return v is None or type(v) in (int, float, str, bytes, bool) and not (type(v) is int and abs(v) >= 2**62)
        case ast.Name(id=n, ctx=ast.Load()):
            return n in names
        case ast.BinOp(left=left, right=right):
            return _pure(left, names) and _pure(right, names)
        case ast.UnaryOp(operand=o):
            return _pure(o, names)
        case ast.BoolOp(values=vs):
            return all(_pure(v, names) for v in vs)
        case ast.Compare(left=left, comparators=cs):
            return _pure(left, names) and all(_pure(c, names) for c in cs)
        case ast.IfExp(test=t, body=b, orelse=o):
            return _pure(t, names) and _pure(b, names) and _pure(o, names)
        case ast.Tuple(elts=es) | ast.List(elts=es):
            return all(_pure(e, names) for e in es)
        case ast.Subscript(value=v, slice=s):
            return _pure(v, names) and _pure(s, names)
        case ast.Slice(lower=lo, upper=up, step=st):
            return all(p is None or _pure(p, names) for p in (lo, up, st))
        case ast.Call(func=ast.Name(id=n), args=args, keywords=[]):
            return n in CALLS and all(_pure(a, names) for a in args)
        case _:
            return False


def _convert(body: list[ast.stmt], loc: dict[str, object]) -> tuple[list[str], int]:
    """Longest convertible prefix. Returns (lines, statements taken)."""
    lines: list[str] = []
    typed: dict[str, str] = {}
    taken = 0
    for stmt in body:
        names = set(typed)
        match stmt:
            case ast.Assign(targets=[ast.Name(id=n)], value=v) if _pure(v, names):
                try:
                    value = eval(compile(ast.Expression(v), "<t>", "eval"), {}, dict(loc))  # noqa: S307
                except Exception:  # noqa: BLE001
                    break
                ann = describe(value).ann
                if not ann or (n in typed and typed[n] != ann):
                    break
                lines.append(f"{n} = {ast.unparse(v)}" if n in typed else f"{n}: {ann} = {ast.unparse(v)}")
                loc[n], typed[n] = value, ann
            case ast.AugAssign(target=ast.Name(id=n), value=v) if n in typed and _pure(v, names):
                lines.append(ast.unparse(stmt))
                try:
                    exec(ast.unparse(stmt), {}, loc)  # noqa: S102
                except Exception:  # noqa: BLE001
                    break
                if describe(loc[n]).ann != typed[n]:
                    break
            case ast.Expr(
                value=ast.Call(func=ast.Attribute(value=ast.Name(id="self"), attr=attr), args=args, keywords=[])
            ) if attr in ASSERTS and len(args) == ARITY.get(attr, 2) and all(_pure(a, names) for a in args):
                lines.append(f"if not ({ASSERTS[attr].format(*(ast.unparse(a) for a in args))}):")
                lines.append("    return 0")
            case _:
                break
        taken += 1
    return lines, taken


def _build(lines: list[str]) -> str:
    return "def probe() -> int:\n" + "\n".join("    " + line for line in [*lines, "return 1"]) + "\n"


def _ok(source: str) -> bool:
    first, second = (engine.run_forked(lambda: engine._exec_probe(source, [])) for _ in range(2))
    return first == ("ok", 1) and second == first


def generate() -> tuple[list[dict[str, object]], dict[str, object]]:
    tests_dir = pinned_dir(f"{sys.version_info[0]}.{sys.version_info[1]}")
    pin = json.loads((tests_dir / "PIN.json").read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    stats: dict[str, object] = {"tag": pin["tag"], "files": {}}
    for path in sorted(tests_dir.glob("test_*.py.txt")):
        text = path.read_bytes()
        stem = path.name.removeprefix("test_").removesuffix(".py.txt")
        if hashlib.sha256(text).hexdigest() != pin["files"][path.name]:
            raise SystemExit(f"{path.name} does not match PIN.json")
        tree = ast.parse(text)
        kept = methods = skipped_stmts = dropped = 0
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")):
                methods += 1
                lines, taken = _convert(fn.body, {})
                skipped_stmts += len(fn.body) - taken
                if not any(line.startswith("if not") for line in lines):
                    dropped += 1
                    continue
                source = _build(lines)
                if not _ok(source) or kept >= CAP:
                    dropped += 1
                    continue
                kept += 1
                pid = f"lang.cpython.{stem}.{cls.name}.{fn.name}"
                probe = Probe(pid, "language", "language", "value", source, weight=WEIGHTS["language"])
                meta = {"id": pid, "area": "language", "category": "language", "kind": "value", "fold": "cpython-test"}
                meta["section"] = (
                    f"https://docs.python.org/{sys.version_info[0]}.{sys.version_info[1]}/reference/" + SECTION[stem]
                )
                rows.append({"probe": probe.to_json(), "meta": meta})
        stats["files"][path.name] = {  # type: ignore[index]
            "methods": methods,
            "kept": kept,
            "dropped": dropped,
            "statements_not_adapted": skipped_stmts,
        }
    return rows, stats
