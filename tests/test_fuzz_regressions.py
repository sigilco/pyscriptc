"""Must-fence fuzzing regression corpus (issue #587, test strategy 3.2).

Tricky AST syntax must either lower cleanly or produce a well-formed
diagnostic fence - never an uncaught compiler exception. Continuous
grammar-directed fuzzing runs out of band; this corpus is the checked-in
regression set that keeps every seed found so far fenced (or accepted) in
PR CI, where the full fuzzer cannot run.

Two layers:

- `MUST_FENCE` / `CLEAN`: pinned cases. Each is lowered directly and the
  outcome is asserted exactly: the fence codes (a subset - a case may gain
  secondary fences without being wrong) or the absence of fences. Every
  fence must be well-formed: a `PSxxxx` code, a non-empty message naming a
  known family, and a span pointing inside the source.
- `test_corpus_never_crashes` and `test_generated_forms_never_crash`: the
  same property over the whole corpus plus a deterministic bounded grammar
  generator, so a new interaction between constructs surfaces as a crash
  the moment it appears, not only when a case is pinned.

The corpus pins outcomes through the in-process lowerer (`lower_source`).
Syntax errors are `ast.parse` failures: they are CPython's own diagnostics
(`SyntaxError`), not compiler crashes, so the corpus also pins that the
parser - not the lowerer - rejects them.
"""

from __future__ import annotations

import ast
import random
import re

import pytest

from pyscriptc.coverage import family_of
from pyscriptc.lower import MAX_LOWER_DEPTH, _ast_depth, _deepest_node, lower_source

# ---------------------------------------------------------------------------
# Pinned corpus
# ---------------------------------------------------------------------------

# `code` is the required fence-code subset (the case must carry at least
# these, in this order among its first fences). `""` pins a clean lower.
MUST_FENCE: list[tuple[str, str, tuple[str, ...]]] = [
    # -- annotation edge cases (PS1001/PS1101-PS1106) ------------------------
    ("ann-call-expression", "def f(x: 1 + 2) -> int:\n    return x\n", ("PS1001",)),
    ("ann-string-literal", "def f(x: 'int') -> int:\n    return x\n", ("PS1105",)),
    ("ann-non-name-subscript", "def f(x: list[0]) -> int:\n    return 0\n", ("PS1001",)),
    ("ann-union-of-three", "def f(x: int | str | None) -> int:\n    return 0\n", ("PS1102",)),
    ("ann-optional", "def f(x: int | None) -> int:\n    return 0\n", ()),
    ("ann-ellipsis-return", "def f() -> ...:\n    return 1\n", ("PS1001",)),
    ("ann-none-return-is-clean", "def f(x: int) -> None:\n    pass\n", ()),
    # -- statement forms (PS1201-PS1206) -------------------------------------
    (
        "match-literal",
        "def f(x: int) -> int:\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 1\n"
        "        case _:\n"
        "            return 0\n",
        (),
    ),
    (
        "match-star-pattern",
        "def f(xs: list[int]) -> int:\n"
        "    match xs:\n"
        "        case [a, *rest]:\n"
        "            return a\n"
        "        case _:\n"
        "            return 0\n",
        ("PS2002",),
    ),
    (
        "match-capture-in-or",
        "def f(x: int) -> int:\n"
        "    match x:\n"
        "        case 1 | other:\n"
        "            return other\n"
        "        case _:\n"
        "            return 0\n",
        ("PS2002",),
    ),
    (
        "try-star",
        "def f() -> int:\n    try:\n        return 1\n    except* ValueError:\n        return 2\n",
        ("PS2002",),
    ),
    ("type-alias-stmt", "type X = int\n\ndef f() -> int:\n    return 1\n", ("PS2002",)),
    ("class-metaclass", "class A(metaclass=type):\n    pass\n", ("PS1203",)),
    ("nested-class", "class A:\n    class B:\n        pass\n", ("PS1204",)),
    # -- expression forms (PS2002/PS2003/PS2004) -----------------------------
    ("walrus-in-call", "def f() -> int:\n    x: int = int(y := 3)\n    return x\n", ("PS2002",)),
    ("starred-call", "def f() -> int:\n    return print(*[1, 2])\n", ("PS2002",)),
    ("double-star-call", "def f() -> int:\n    return print(**{'a': 1})\n", ("PS2002",)),
    ("lambda-bound-to-ann-local", "def f() -> int:\n    g: int = lambda x: x\n    return g\n", ("PS1001",)),
    ("await-in-sync", "def f() -> int:\n    return await g()\n", ("PS2002",)),
    ("module-level-await", "await f()\n\n\ndef f() -> int:\n    return 1\n", ("PS2002",)),
    ("chained-uneven-compare", "def f(a: int, b: int) -> bool:\n    return a < b != a\n", ()),
    ("fstring-self-nesting", "def f() -> str:\n    return f'{f\"{1}\"}'\n", ()),
    ("fstring-debug-equals", "def f() -> str:\n    x: int = 3\n    return f'{x=}'\n", ()),
    # -- imports and module statements (PS2001/PS2002) -----------------------
    ("import-star", "from os import *\n\n\ndef f() -> int:\n    return 1\n", ("PS2002",)),
    ("import-shadow-builtin", "import math\n\n\ndef f() -> float:\n    return math.sqrt(4.0)\n", ()),
    ("exec-call", "def f() -> int:\n    exec('x = 1')\n    return 1\n", ("PS2002",)),
    ("return-outside-function", "return 1\n", ("PS1003",)),
    ("bare-async-for", "async for i in y:\n    pass\n", ("PS2002",)),
    # -- boundary conditions -------------------------------------------------
    ("deep-parens-parse-ok", "def f() -> int:\n    return " + "(" * 50 + "1" + ")" * 50 + "\n", ()),
    ("deep-attr-chain-fenced", "def f() -> int:\n    return " + "x" + ".y" * 300 + "\n", ("PS3002",)),
    ("long-binary-chain", "def f() -> int:\n    return " + " + ".join(["1"] * 150) + "\n", ()),
    ("empty-module", "", ()),
    ("comment-only", "# nothing here\n", ()),
    ("negative-zero-int", "def f() -> int:\n    return -0\n", ()),
    ("huge-int-literal", "def f() -> int:\n    return 1 << 1000\n", ()),
    ("unicode-identifier", "def f() -> int:\n    caf\u00e9: int = 1\n    return caf\u00e9\n", ()),
    ("lone-surrogate-string", "def f() -> str:\n    return '\\ud800'.upper()\n", ("PS2005",)),
    ("kwonly-and-star-args", "def f(a: int, *args: int, b: int = 1, **kw: int) -> int:\n    return a + b\n", ()),
    ("default-calls-lowered-builtin", "def f(x: int = len([1])) -> int:\n    return x\n", ()),
]

# Sources CPython's own parser rejects. The lowerer must never see them:
# `lower_source` propagates the `SyntaxError` instead of a compiler crash.
SYNTAX_ERRORS: list[tuple[str, str]] = [
    ("broken-def", "def f( -> int:\n    return 1\n"),
    ("annotated-walrus-stmt", "def f() -> int:\n    (x: int) = 1\n    return x\n"),
    ("duplicate-kwargs-name", "def f(**kw, **kw) -> int:\n    return 1\n"),
    ("match-missing-case", "def f(x: int) -> int:\n    match x:\n        1:\n            return 1\n"),
    ("fstring-unterminated", "def f() -> str:\n    return f'{x\n"),
]

CODE_RE = re.compile(r"^PS\d{4}$")


def _codes(module: dict) -> list[str]:
    return [fence["code"] for fence in module["fences"]]


def _assert_well_formed(module: dict, source: str) -> None:
    """Every fence carries a PS code, a message, a known family and an in-source span."""
    lines = source.splitlines()
    for fence in module["fences"]:
        assert CODE_RE.match(fence["code"]), fence
        assert fence["message"], fence
        assert family_of(fence["code"], fence["message"]), fence
        span = fence["span"]
        assert span["file"] == "case.py"
        assert 1 <= span["line"] <= max(len(lines), 1), (fence, source)
        assert span["col"] >= 1
        assert span["endLine"] >= span["line"]
        assert span["endCol"] >= 1


@pytest.mark.parametrize(("name", "source", "expected"), MUST_FENCE, ids=[name for name, _, _ in MUST_FENCE])
def test_must_fence_case(name: str, source: str, expected: tuple[str, ...]) -> None:
    module = lower_source(source, "case.py")
    _assert_well_formed(module, source)
    got = _codes(module)
    assert got[: len(expected)] == list(expected), (name, got, module["fences"][: len(expected)])


@pytest.mark.parametrize(("name", "source"), SYNTAX_ERRORS, ids=[name for name, _ in SYNTAX_ERRORS])
def test_syntax_error_rejected_by_the_parser(name: str, source: str) -> None:
    with pytest.raises(SyntaxError):
        lower_source(source, "case.py")
    with pytest.raises(SyntaxError):
        ast.parse(source)


# ---------------------------------------------------------------------------
# Crash fence: the same property over the corpus and generated forms
# ---------------------------------------------------------------------------

CRASH_NOTE = (
    "lowering raised instead of compiling or fencing - pin the new construct "
    "in MUST_FENCE with its diagnostic code, or fix the lowerer"
)


@pytest.mark.parametrize(("name", "source", "_"), MUST_FENCE, ids=[name for name, _, _ in MUST_FENCE])
def test_corpus_never_crashes(name: str, source: str, _: tuple[str, ...]) -> None:
    try:
        module = lower_source(source, "case.py")
    except SyntaxError:
        pytest.fail(f"{name}: a pinned corpus case must parse: {source!r}")
    except Exception as exc:
        pytest.fail(f"{name}: {CRASH_NOTE}: {type(exc).__name__}: {exc}")
    _assert_well_formed(module, source)


# Deterministic grammar-directed generator (strategy 3.2, the CI-side
# sibling of the out-of-band fuzzer). A fixed seed keeps the run bounded and
# reproducible; extend EXPR_FORMS / STMT_FORMS when the out-of-band fuzzer
# finds a new crash seed, and pin the seed in MUST_FENCE.
EXPR_FORMS: tuple[str, ...] = (
    "x",
    "y",
    "1",
    "1.5",
    "'s'",
    "b'b'",
    "True",
    "None",
    "...",
    "x + y",
    "x - 1",
    "x * y",
    "x / y",
    "x // 2",
    "x % 2",
    "x ** 2",
    "x << 1",
    "x >> 1",
    "x & y",
    "x | y",
    "x ^ y",
    "x @ y",
    "not x",
    "-x",
    "~x",
    "x < y",
    "x == y",
    "x in y",
    "x is y",
    "x and y",
    "x or y",
    "x if y else 1",
    "x[0]",
    "x[1:2]",
    "x[::2]",
    "x.n",
    "f(x)",
    "f(x, 1)",
    "f(x=1)",
    "f(*y)",
    "f(**y)",
    "[1, 2]",
    "[]",
    "(1, 2)",
    "{1: 2}",
    "{1, 2}",
    "[x for x in y]",
    "{x: 1 for x in y}",
    "{x for x in y}",
    "(x for x in y)",
    "[*y]",
    "{**y}",
    "(x := 1)",
    "lambda a: a",
    "f'{x}'",
    "f'{x=}'",
    "f'{x!r:>10}'",
    "int('3')",
    "str(x)",
    "len([])",
    "(yield)",
)

STMT_FORMS: tuple[str, ...] = (
    "pass",
    "x = {e}",
    "x: int = {e}",
    "x += {e}",
    "del x",
    "return {e}",
    "assert {e}",
    "assert {e}, 'msg'",
    "raise ValueError('x')",
    "print({e})",
    "if {e}:\n    pass",
    "if {e}:\n    pass\nelse:\n    pass",
    "while {e}:\n    break",
    "for i in y:\n    pass",
    "with open('f') as g:\n    pass",
    "try:\n    pass\nexcept ValueError:\n    pass",
    "def g() -> int:\n    return {e}",
    "class C:\n    pass",
    "x = [{e}, {e}]",
    "x: list[int] = [{e}]",
    "x: dict[str, int] = {{'a': {e}}}",
    "if {e}:\n    pass\nelif y:\n    pass\nelse:\n    pass",
    "match x:\n    case 1:\n        pass\n    case _:\n        pass",
)

GENERATOR_SEED = 20261027
GENERATOR_CASES = 600


def _expand(form: str, rng: random.Random, depth: int) -> str:
    """Fill `{e}` slots; nested expressions stop at depth 2."""
    while "{e}" in form:
        if depth >= 2:
            form = form.replace("{e}", "1", 1)
        else:
            form = form.replace("{e}", _expand(rng.choice(EXPR_FORMS), rng, depth + 1), 1)
    return form


def _generated_sources() -> list[str]:
    rng = random.Random(GENERATOR_SEED)
    sources = []
    for _ in range(GENERATOR_CASES):
        body = "\n".join(_expand(rng.choice(STMT_FORMS), rng, 0) for _ in range(rng.randint(1, 3)))
        indented = "\n".join(f"    {line}" if line else line for line in body.split("\n"))
        source = f"def f(x: int, y: int) -> int:\n{indented}\n"
        try:
            ast.parse(source)
        except SyntaxError:
            continue
        sources.append(source)
    return sources


# ---------------------------------------------------------------------------
# The nesting guard (issue #587)
# ---------------------------------------------------------------------------


def test_ast_depth_measures_the_deepest_path() -> None:
    tree = ast.parse("def f() -> int:\n    return 1 + 2\n")
    # module -> FunctionDef -> Return -> BinOp -> Constant
    assert _ast_depth(tree) == 4
    assert _deepest_node(tree).col_offset >= 0


def test_ast_depth_survives_a_pathological_tree() -> None:
    """The guard measures iteratively: a deep tree must not blow the stack."""
    source = "x = " + " + ".join(["1"] * 5000)
    tree = ast.parse(source)
    assert _ast_depth(tree) > MAX_LOWER_DEPTH


def test_deep_nesting_fences_instead_of_crashing() -> None:
    """A source CPython parses but the lowerer cannot recurse into fences."""
    source = "def f() -> int:\n    return " + " + ".join(["1"] * 300) + "\n"
    module = lower_source(source, "case.py")
    _assert_well_formed(module, source)
    assert [fence["code"] for fence in module["fences"]] == ["PS3002"]
    assert module["functions"] == []


def test_shallow_nesting_still_lowers_native() -> None:
    """The guard must not fence a program that lowers fine."""
    source = "def f() -> int:\n    return " + " + ".join(["1"] * 50) + "\n"
    module = lower_source(source, "case.py")
    assert module["fences"] == []
    assert module["functions"]


def test_generated_forms_never_crash() -> None:
    sources = _generated_sources()
    assert sources, "the generator produced no parsable forms"
    for source in sources:
        try:
            module = lower_source(source, "case.py")
        except SyntaxError:
            continue
        except Exception as exc:
            pytest.fail(f"{CRASH_NOTE}: {type(exc).__name__}: {exc}\n---\n{source}")
        _assert_well_formed(module, source)
