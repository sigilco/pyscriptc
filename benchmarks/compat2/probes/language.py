"""Language profile probes: ast node types x context, each mapped to a Language Reference section.

Every probe is verified under CPython before it is emitted: it must run and return a deterministic int.
Snippets that fail verification are dropped and counted in the manifest, never emitted unchecked.
"""

from __future__ import annotations

import ast
import json
import sys

from benchmarks.compat2.outcomes import WEIGHTS, Probe

from . import engine
from .fold import describe

ORACLE = f"{sys.version_info[0]}.{sys.version_info[1]}"
REF = f"https://docs.python.org/{ORACLE}/reference/"
SECTIONS = {
    "lexical": "lexical_analysis.html",
    "datamodel": "datamodel.html",
    "expressions": "expressions.html",
    "simple": "simple_stmts.html",
    "compound": "compound_stmts.html",
}

# Abstract bases and parse-mode wrappers have no program-level construct to probe.
NOT_APPLICABLE = {
    "AST": "abstract",
    "mod": "abstract",
    "stmt": "abstract",
    "expr": "abstract",
    "expr_context": "abstract",
    "boolop": "abstract",
    "operator": "abstract",
    "unaryop": "abstract",
    "cmpop": "abstract",
    "excepthandler": "abstract",
    "pattern": "abstract",
    "type_ignore": "abstract",
    "type_param": "abstract",
    "slice": "abstract",
    "Interactive": "parse mode",
    "Expression": "parse mode",
    "FunctionType": "parse mode",
    "TypeIgnore": "type comment pragma",
    "AugLoad": "deprecated",
    "AugStore": "deprecated",
    "Index": "deprecated",
    "ExtSlice": "deprecated",
    "Param": "deprecated",
    "Suite": "deprecated",
}

PRELUDE = [
    "a: int = 7",
    "b: int = 3",
    "f: float = 2.5",
    "s: str = 'abc'",
    "t: bool = True",
    "xs: list[int] = [1, 2, 3]",
]

# (node, variant, section, expression)
EXPRS: list[tuple[str, str, str, str]] = [
    ("Constant", "int", "lexical", "a + 41"),
    ("Constant", "float", "lexical", "f * 1.5"),
    ("Constant", "imag", "lexical", "(a * 1j).imag"),
    ("Constant", "str", "lexical", "s + 'def'"),
    ("Constant", "bytes", "lexical", "b'abc' + b'def'"),
    ("Constant", "bool", "lexical", "t and True"),
    ("Constant", "none", "lexical", "None is None"),
    ("Constant", "ellipsis", "lexical", "... is ..."),
    ("JoinedStr", "", "lexical", "f'{a}-{s}'"),
    ("FormattedValue", "spec", "lexical", "f'{f:.2f}'"),
    ("FormattedValue", "conv", "lexical", "f'{s!r}'"),
    ("Name", "load", "expressions", "a"),
    ("BoolOp", "and", "expressions", "a > 1 and b > 1"),
    ("BoolOp", "or", "expressions", "a > 9 or b > 1"),
    ("NamedExpr", "", "expressions", "(q := a + 1) + q"),
    ("BinOp", "add", "expressions", "a + b"),
    ("BinOp", "sub", "expressions", "a - b"),
    ("BinOp", "mult", "expressions", "a * b"),
    ("BinOp", "div", "expressions", "a / b"),
    ("BinOp", "floordiv", "expressions", "a // b"),
    ("BinOp", "mod", "expressions", "a % b"),
    ("BinOp", "pow", "expressions", "a ** b"),
    ("BinOp", "lshift", "expressions", "a << b"),
    ("BinOp", "rshift", "expressions", "a >> 1"),
    ("BinOp", "bitor", "expressions", "a | b"),
    ("BinOp", "bitxor", "expressions", "a ^ b"),
    ("BinOp", "bitand", "expressions", "a & b"),
    ("UnaryOp", "invert", "expressions", "~a"),
    ("UnaryOp", "not", "expressions", "not t"),
    ("UnaryOp", "uadd", "expressions", "+a"),
    ("UnaryOp", "usub", "expressions", "-a"),
    ("Lambda", "", "expressions", "(lambda x: x + 1)(a)"),
    ("IfExp", "", "expressions", "a if t else b"),
    ("Dict", "", "expressions", "len({'a': a, 'b': b})"),
    ("Dict", "unpack", "expressions", "len({**{'a': a}, 'b': b})"),
    ("Set", "", "expressions", "len({a, b, a})"),
    ("ListComp", "", "expressions", "[x * 2 for x in xs]"),
    ("SetComp", "", "expressions", "len({x % 2 for x in xs})"),
    ("DictComp", "", "expressions", "len({x: x for x in xs})"),
    ("GeneratorExp", "", "expressions", "sum(x for x in xs)"),
    ("comprehension", "if", "expressions", "[x for x in xs if x > 1]"),
    ("comprehension", "nested", "expressions", "[x * y for x in xs for y in xs]"),
    ("Compare", "eq", "expressions", "a == b"),
    ("Compare", "noteq", "expressions", "a != b"),
    ("Compare", "lt", "expressions", "a < b"),
    ("Compare", "lte", "expressions", "a <= b"),
    ("Compare", "gt", "expressions", "a > b"),
    ("Compare", "gte", "expressions", "a >= b"),
    ("Compare", "is", "expressions", "xs is xs"),
    ("Compare", "isnot", "expressions", "xs is not []"),
    ("Compare", "in", "expressions", "a in xs"),
    ("Compare", "notin", "expressions", "a not in xs"),
    ("Compare", "chain", "expressions", "0 < b < a < 10"),
    ("Call", "positional", "expressions", "max(a, b)"),
    ("Call", "keyword", "expressions", "sorted(xs, reverse=True)"),
    ("Call", "star", "expressions", "max(*xs)"),
    ("keyword", "kwargs", "expressions", "dict(**{'a': a})"),
    ("Attribute", "load", "expressions", "s.upper()"),
    ("Subscript", "index", "expressions", "xs[1]"),
    ("Subscript", "negative", "expressions", "xs[-1]"),
    ("Slice", "", "expressions", "xs[0:2]"),
    ("Slice", "step", "expressions", "xs[::2]"),
    ("Starred", "", "expressions", "[*xs, a]"),
    ("List", "", "expressions", "[a, b]"),
    ("Tuple", "", "expressions", "(a, b)"),
    ("Tuple", "unpack", "expressions", "(a, *xs)"),
    ("Load", "", "expressions", "xs[0] + a"),
]

# (node, variant, section, body lines). Bodies end in `return <int>` and use PRELUDE locals.
STMTS: list[tuple[str, str, str, list[str]]] = [
    ("Assign", "", "simple", ["r: int = a + b", "return r"]),
    ("Assign", "multi", "simple", ["x = y = a", "return x + y"]),
    ("Assign", "tuple", "simple", ["x, y = a, b", "return x - y"]),
    ("Assign", "starred", "simple", ["x, *rest = xs", "return x + len(rest)"]),
    ("AugAssign", "add", "simple", ["r: int = a", "r += b", "return r"]),
    ("AugAssign", "mult", "simple", ["r: int = a", "r *= b", "return r"]),
    ("AugAssign", "floordiv", "simple", ["r: int = a", "r //= b", "return r"]),
    ("AugAssign", "pow", "simple", ["r: int = a", "r **= b", "return r"]),
    ("AugAssign", "shift", "simple", ["r: int = a", "r <<= b", "r >>= 1", "return r"]),
    ("AugAssign", "bitops", "simple", ["r: int = a", "r |= b", "r &= 6", "r ^= 1", "return r"]),
    ("AugAssign", "subscript", "simple", ["ys: list[int] = [1, 2]", "ys[0] += a", "return ys[0]"]),
    ("AnnAssign", "", "simple", ["r: int = a", "return r"]),
    ("AnnAssign", "bare", "simple", ["r: int", "r = b", "return r"]),
    ("Expr", "", "simple", ["xs.append(a)", "return len(xs)"]),
    ("Pass", "", "simple", ["pass", "return a"]),
    ("Delete", "name", "simple", ["x = a", "del x", "return b"]),
    ("Delete", "subscript", "simple", ["ys: list[int] = [1, 2, 3]", "del ys[0]", "return len(ys)"]),
    ("Delete", "attribute", "simple", ["d: dict[str, int] = {'k': a}", "del d['k']", "return len(d)"]),
    ("Assert", "", "simple", ["assert a > 0", "assert b > 0, 'msg'", "return a"]),
    ("Return", "", "simple", ["return a"]),
    ("Global", "", "simple", ["global G", "G = a", "return G"]),
    ("Raise", "", "simple", ["try:", "    raise ValueError('x')", "except ValueError:", "    return a", "return b"]),
    (
        "Raise",
        "from",
        "simple",
        ["try:", "    raise ValueError('x') from None", "except ValueError:", "    return a", "return b"],
    ),
    ("Break", "", "simple", ["for i in xs:", "    if i == 2:", "        break", "return i"]),
    (
        "Continue",
        "",
        "simple",
        ["n = 0", "for i in xs:", "    if i == 2:", "        continue", "    n += i", "return n"],
    ),
    ("Import", "", "simple", ["import math", "return math.isqrt(a)"]),
    ("ImportFrom", "", "simple", ["from math import isqrt", "return isqrt(a)"]),
    ("alias", "as", "simple", ["import math as m", "from math import isqrt as q", "return m.isqrt(a) + q(b)"]),
    ("If", "", "compound", ["if a > b:", "    return 1", "return 0"]),
    ("If", "elif", "compound", ["if a < b:", "    return 1", "elif a == 7:", "    return 2", "else:", "    return 3"]),
    ("While", "", "compound", ["n = 0", "while n < a:", "    n += 2", "return n"]),
    ("While", "else", "compound", ["n = 0", "while n < b:", "    n += 1", "else:", "    n += 10", "return n"]),
    ("For", "", "compound", ["n = 0", "for i in xs:", "    n += i", "return n"]),
    ("For", "else", "compound", ["n = 0", "for i in xs:", "    n += i", "else:", "    n += 100", "return n"]),
    ("For", "unpack", "compound", ["n = 0", "for i, j in [(1, 2), (3, 4)]:", "    n += i * j", "return n"]),
    ("Try", "except", "compound", ["try:", "    return xs[9]", "except IndexError:", "    return a"]),
    ("Try", "finally", "compound", ["n = 0", "try:", "    n = a", "finally:", "    n += 1", "return n"]),
    (
        "Try",
        "else",
        "compound",
        ["n = 0", "try:", "    n = 1", "except ValueError:", "    n = 2", "else:", "    n += 10", "return n"],
    ),
    (
        "ExceptHandler",
        "as",
        "compound",
        ["try:", "    int('x')", "except ValueError as e:", "    return len(str(e))", "return 0"],
    ),
    (
        "ExceptHandler",
        "tuple",
        "compound",
        ["try:", "    xs[9]", "except (KeyError, IndexError):", "    return a", "return 0"],
    ),
    (
        "TryStar",
        "",
        "compound",
        ["n = 0", "try:", "    raise ValueError('x')", "except* ValueError:", "    n = a", "return n"],
    ),
    ("With", "", "compound", ["import io", "with io.StringIO('abc') as fh:", "    return len(fh.read())"]),
    (
        "With",
        "multi",
        "compound",
        ["import io", "with io.StringIO('ab') as p, io.StringIO('cd') as q:", "    return len(p.read() + q.read())"],
    ),
    ("withitem", "bare", "compound", ["import io", "with io.StringIO('abc'):", "    return a"]),
    ("FunctionDef", "", "compound", ["def g(x: int) -> int:", "    return x + 1", "return g(a)"]),
    ("FunctionDef", "defaults", "compound", ["def g(x: int, y: int = 2) -> int:", "    return x + y", "return g(a)"]),
    ("FunctionDef", "closure", "compound", ["def g() -> int:", "    return a + b", "return g()"]),
    (
        "FunctionDef",
        "nonlocal",
        "compound",
        ["n = 0", "def g() -> None:", "    nonlocal n", "    n = a", "g()", "return n"],
    ),
    (
        "arguments",
        "varargs",
        "compound",
        ["def g(*args: int, **kw: int) -> int:", "    return len(args) + len(kw)", "return g(1, 2, k=3)"],
    ),
    (
        "arguments",
        "kwonly",
        "compound",
        ["def g(x: int, *, y: int = 2) -> int:", "    return x * y", "return g(a, y=b)"],
    ),
    ("arguments", "posonly", "compound", ["def g(x: int, /, y: int) -> int:", "    return x - y", "return g(a, y=b)"]),
    ("arg", "annotated", "compound", ["def g(x: list[int]) -> int:", "    return len(x)", "return g(xs)"]),
    ("Yield", "", "expressions", ["def g():", "    yield 1", "    yield 2", "return sum(g())"]),
    ("YieldFrom", "", "expressions", ["def g():", "    yield 1", "    yield from [2, 3]", "return sum(g())"]),
    (
        "Await",
        "",
        "expressions",
        [
            "import asyncio",
            "async def g() -> int:",
            "    return a",
            "async def h() -> int:",
            "    return await g() + 1",
            "return asyncio.run(h())",
        ],
    ),
    (
        "AsyncFunctionDef",
        "",
        "compound",
        ["import asyncio", "async def g() -> int:", "    return a", "return asyncio.run(g())"],
    ),
    (
        "AsyncFor",
        "",
        "compound",
        [
            "import asyncio",
            "async def gen():",
            "    yield 1",
            "    yield 2",
            "async def h() -> int:",
            "    n = 0",
            "    async for i in gen():",
            "        n += i",
            "    return n",
            "return asyncio.run(h())",
        ],
    ),
    (
        "AsyncWith",
        "",
        "compound",
        [
            "import asyncio, contextlib",
            "@contextlib.asynccontextmanager",
            "async def cm():",
            "    yield 5",
            "async def h() -> int:",
            "    async with cm() as v:",
            "        return v",
            "return asyncio.run(h())",
        ],
    ),
    ("ClassDef", "", "compound", ["class C:", "    def m(self) -> int:", "        return 5", "return C().m()"]),
    (
        "ClassDef",
        "inherit",
        "compound",
        ["class B:", "    def m(self) -> int:", "        return 5", "class C(B):", "    pass", "return C().m()"],
    ),
    (
        "ClassDef",
        "decorated",
        "compound",
        ["import dataclasses", "@dataclasses.dataclass", "class C:", "    x: int", "return C(a).x"],
    ),
    ("Match", "value", "compound", ["match a:", "    case 7:", "        return 1", "    case _:", "        return 0"]),
    (
        "match_case",
        "guard",
        "compound",
        ["match a:", "    case n if n > 5:", "        return n", "    case _:", "        return 0"],
    ),
    (
        "MatchValue",
        "",
        "compound",
        ["match s:", "    case 'abc':", "        return 1", "    case _:", "        return 0"],
    ),
    (
        "MatchSingleton",
        "",
        "compound",
        ["match None:", "    case None:", "        return 1", "    case _:", "        return 0"],
    ),
    (
        "MatchSequence",
        "",
        "compound",
        ["match xs:", "    case [x, y, z]:", "        return x + y + z", "    case _:", "        return 0"],
    ),
    (
        "MatchStar",
        "",
        "compound",
        ["match xs:", "    case [x, *rest]:", "        return x + len(rest)", "    case _:", "        return 0"],
    ),
    (
        "MatchMapping",
        "",
        "compound",
        [
            "d: dict[str, int] = {'k': a}",
            "match d:",
            "    case {'k': v}:",
            "        return v",
            "    case _:",
            "        return 0",
        ],
    ),
    (
        "MatchClass",
        "",
        "compound",
        ["match a:", "    case int():", "        return 1", "    case _:", "        return 0"],
    ),
    (
        "MatchAs",
        "",
        "compound",
        ["match a:", "    case int() as n:", "        return n", "    case _:", "        return 0"],
    ),
    ("MatchOr", "", "compound", ["match a:", "    case 1 | 7:", "        return 1", "    case _:", "        return 0"]),
    (
        "BinOp",
        "matmult",
        "expressions",
        ["class M:", "    def __matmul__(self, o: 'M') -> int:", "        return 9", "return M() @ M()"],
    ),
    ("TypeAlias", "", "compound", ["type Pair = tuple[int, int]", "p: Pair = (a, b)", "return p[0]"]),
    ("TypeVar", "", "compound", ["def g[T](x: T) -> T:", "    return x", "return g(a)"]),
    ("ParamSpec", "", "compound", ["def g[**P](x: int) -> int:", "    return x", "return g(a)"]),
    ("TypeVarTuple", "", "compound", ["def g[*Ts](x: int) -> int:", "    return x", "return g(a)"]),
    ("TemplateStr", "", "lexical", ["t = t'{a}'", "return len(t.strings) + len(t.interpolations)"]),
    ("Interpolation", "", "lexical", ["t = t'{a!r:>4}'", "return t.interpolations[0].value"]),
]

CONTEXTS_EXPR = ("assign", "arg", "cond", "loop")
CONTEXTS_STMT = ("function", "method")


def _meta(probe: Probe, fold: str) -> dict[str, object]:
    return {"id": probe.id, "area": "language", "category": "language", "kind": probe.kind, "fold": fold}


def _emit(rows: list[dict[str, object]], pid: str, source: str, fold: str, section: str) -> None:
    probe = Probe(pid, "language", "language", "value", source, weight=WEIGHTS["language"])
    row = {"probe": probe.to_json(), "meta": {**_meta(probe, fold), "section": REF + SECTIONS[section]}}
    rows.append(row)


def _verified(source: str) -> bool:
    status_a, value_a = engine.run_forked(lambda: engine._exec_probe(source, []))
    status_b, value_b = engine.run_forked(lambda: engine._exec_probe(source, []))
    return status_a == "ok" and (status_a, value_a) == (status_b, value_b) and isinstance(value_a, int)


def _globals_for(body: list[str]) -> list[str]:
    return ["G: int = 0"] if any("global G" in line for line in body) else []


def _stmt_source(body: list[str], context: str) -> str:
    head = _globals_for(body)
    prelude = [*PRELUDE, *body]
    if context == "function":
        lines = [*head, "", "def probe() -> int:", *("    " + line for line in prelude)]
    else:
        lines = [
            *head,
            "",
            "class Host:",
            "    def run(self) -> int:",
            *("        " + line for line in prelude),
            "",
            "def probe() -> int:",
            "    return Host().run()",
        ]
    return "\n".join(lines) + "\n"


def _in_oracle(node: str) -> bool:
    """Table entries are named after ast classes. Snippets for newer nodes are skipped on an older oracle."""
    return hasattr(ast, node)


def _expr_rows(rows: list[dict[str, object]], dropped: list[str], absent: list[str]) -> None:
    for node, variant, section, expr in EXPRS:
        stem = f"lang.{node}" + (f".{variant}" if variant else "")
        if not _in_oracle(node):
            absent.append(stem)
            continue
        for context in CONTEXTS_EXPR:
            source = _expr_source(expr, context)
            if source is None or not _verified(source):
                dropped.append(f"{stem}.{context}")
                continue
            _emit(rows, f"{stem}.{context}", source, f"expr-{context}", section)


def _expr_source(expr: str, context: str) -> str | None:
    value = _evaluate(expr)
    if value is None:
        return None
    desc = describe(value[0])
    ann = desc.ann
    if not ann or desc.fold in {"none", "exec", "handle"}:
        return None
    helper = f"def ident(v: {ann}) -> {ann}:\n    return v\n\n\n"
    folded = {
        "assign": [f"r: {ann} = {expr}"],
        "arg": [f"r: {ann} = ident({expr})"],
        "cond": [f"r: {ann} = {expr} if t else {expr}"],
        "loop": [f"r: {ann} = {expr}", "for _ in range(2):", f"    r = {expr}"],
    }[context]
    from .fold import fold_lines

    tail = fold_lines(desc, "r", value[0])
    body = [*PRELUDE, *folded, *tail]
    head = helper if context == "arg" else ""
    return head + "def probe() -> int:\n" + "\n".join("    " + line for line in body) + "\n"


def _evaluate(expr: str) -> tuple[object] | None:
    loc = {"a": 7, "b": 3, "f": 2.5, "s": "abc", "t": True, "xs": [1, 2, 3]}
    try:
        return (eval(expr, {}, loc),)  # noqa: S307  # trusted literals from the tables above
    except Exception:  # noqa: BLE001
        return None


def _stmt_rows(rows: list[dict[str, object]], dropped: list[str], absent: list[str]) -> None:
    for node, variant, section, body in STMTS:
        stem = f"lang.{node}" + (f".{variant}" if variant else "")
        if not _in_oracle(node):
            absent.append(stem)
            continue
        for context in CONTEXTS_STMT:
            source = _stmt_source(body, context)
            if not _verified(source):
                dropped.append(f"{stem}.{context}")
                continue
            _emit(rows, f"{stem}.{context}", source, f"stmt-{context}", section)


def node_denominator() -> dict[str, list[str]]:
    """Concrete ast node types in the running CPython, and the ones that are not applicable."""
    concrete = []
    for name in sorted(dir(ast)):
        cls = getattr(ast, name)
        if (
            isinstance(cls, type)
            and issubclass(cls, ast.AST)
            and name not in NOT_APPLICABLE
            and cls._fields is not None
        ):
            if not name.startswith("_") and cls.__module__ == "ast" and cls.__base__ is not object:
                concrete.append(name)
    return {"concrete": concrete, "not_applicable": sorted(NOT_APPLICABLE)}


def generate() -> tuple[list[dict[str, object]], dict[str, object]]:
    from . import cpython_adapter

    rows: list[dict[str, object]] = []
    dropped: list[str] = []
    absent: list[str] = []  # snippets for ast nodes the oracle does not have: kept for newer oracles only
    _expr_rows(rows, dropped, absent)
    _stmt_rows(rows, dropped, absent)
    adapted, adapter_stats = cpython_adapter.generate()
    rows.extend(adapted)
    denominator = node_denominator()
    covered = {
        type(node).__name__
        for row in rows
        if not row["probe"]["id"].startswith("lang.cpython")  # type: ignore[index]
        for node in ast.walk(ast.parse(row["probe"]["source"]))  # type: ignore[index]
    }
    info = {
        "nodes": len(denominator["concrete"]),
        "nodes_uncovered": sorted(set(denominator["concrete"]) - covered),
        "not_applicable": denominator["not_applicable"],
        "dropped_unverified": sorted(dropped),
        "absent_in_oracle": sorted(absent),
        "cpython_tests": adapter_stats,
    }
    return rows, info


if __name__ == "__main__":
    # Worker entry: runs on the oracle interpreter and prints one JSON document for the orchestrator.
    generated, details = generate()
    print(json.dumps({"rows": generated, "info": details, "ast_nodes": sorted(node_denominator()["concrete"])}))
