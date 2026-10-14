"""Classify the v1-lowered fence rows into ``probes/TRIAGE.md``.

Reads the frozen calibration report, the probe JSONL, ``continuity/v1equiv.jsonl``, and the newest
frozen v1 iteration. It does not run ``pyscriptc``.

The calibration file stores aggregate outcomes (189 ``fence``, 1 ``build-error``) and one combined
not-passing id list. Per-id outcomes are not stored. Each not-passing row must match exactly one
rule below. The ``out of scope`` rule is the build-error and is not one of the three fence buckets.
Zero matches or two matches is unclassified, and the process exits non-zero.

Rules, in the order a reader should apply them. The predicates are disjoint; order is documentation.

out of scope
    out-of-scope-getenv. A value probe whose body is ``os.getenv(<one parameter>)`` then ``return 1``.
    The recorded argument is not a set variable, while the v1-equivalent probe reads ``PATH``.
    ``calibrate.run_one`` records a non-zero exit other than 3 as ``build-error`` for a value probe.
    The only calibration cell that contains ``build-error`` is ``surface/function/value``, this row's
    area, category, and kind. Not placed in a fence bucket.

probe wrong (a later generator change, not a probe edit in this task)
    raises-fallback. ``kind`` is ``raises`` and ``expect_exc`` is set. Argument search kept a call
    that raises. v1 had lowered the name. When ``v1equiv.jsonl`` has no ``<id>.v1`` row, v1 did not
    execute the name to an int (``sys.exit``).
    private-annotation. The result annotation starts with ``_`` or is ``builtin_function_or_method``.
    That names an extension type or a non-type. The v1-equivalent probe folded the name to an int.
    seq-int-remainder. The source contains the seq-int fold ``% 1000003`` (integer remainder). v1
    returned an element.
    int-operand. ``getattr``, ``hasattr``, or ``shutil.copyfile`` receives an ``int`` parameter.
    v1 passed an object or a path string. ``open`` with an int path is private-annotation instead,
    because its result annotation is ``_io.TextIOWrapper``.
    bisect-str-haystack. ``bisect_left`` or ``bisect_right`` is called with a str haystack. v1 passed
    a list of ints. The recorded ``lo`` and ``hi`` are equal, so the call returns the bound.
    sum-bools. ``sum`` of a list of bool literals. v1 summed a list of ints.
    listdir-elem. ``os.listdir()`` with no path, annotated ``list[int]``. An empty listing becomes
    the generator's empty-list default. listdir returns names. v1 listed ``'/tmp'``.
    parse-qs-elem. ``urllib.parse.parse_qs`` annotated ``dict[str, int]``. The function returns
    ``dict[str, list[str]]``. The recorded argument has no query pair, so the result was empty.
    struct-format-parameter. ``struct.pack`` or ``struct.unpack`` takes its format from a parameter.
    The recorded format is empty. v1 inlined a format literal.
    empty-tuple-annotation. ``tuple()`` annotated ``tuple[int, ...]``, the generator's empty-tuple
    default. v1 indexed a two-int tuple literal.
    rmtree-unchecked. ``shutil.rmtree`` called with two arguments and the result discarded. v1 built
    a tree, removed it, and checked that the path was gone.
    posixpath-concrete-class. Annotation ``pathlib.PosixPath``, the platform subclass ``describe``
    saw. The public constructor is ``pathlib.Path``. v1 folded ``Path(...).name``.

stricter than v1 (kept)
    enum-constant. Category ``constant``. The body binds a dotted class annotation and ``return 1``.
    The v1-equivalent probe returns that same attribute: an IntEnum or IntFlag is an int, so v1
    lowered the integer. v2 requires the enum class and does not recheck the number.

compiler gap (kept)
    zero-arg-constructor. ``bool``, ``dict``, ``float``, ``int``, ``list``, ``str``, or ``math.hypot``
    called with no arguments. v1 called the same name with arguments. The zero-argument form stays.
    iterator-object. ``enumerate``, ``range``, or ``zip`` annotated as that type and returning 1.
    v1 only used the name as the iterator of a ``for``. The object stays.
    container-class. ``collections.OrderedDict()`` or ``collections.deque()`` annotated as that class
    and returning 1. v1 checked ``len`` or ``pop``. The constructor probe stays.
    split-tuple. ``posixpath.split`` or ``posixpath.splitext`` annotated ``tuple[str, str]``, which
    is what CPython returns. v1 folded ``len``. The tuple annotation stays.
    result-class. ``re.compile`` annotated ``re.Pattern``, or ``urllib.parse.urlsplit`` annotated
    ``urllib.parse.SplitResult``, returning 1. v1 folded a string attribute. The result class stays.
    strftime-format-only. ``time.strftime`` called with only the format, result annotated ``str``.
    The one-argument form is specified. v1 also passed a ``struct_time``. The one-argument call stays.
    mirrored-int. ``unicodedata.mirrored`` annotated ``int`` and the int returned. That is CPython's
    result. v1 branched on the value. The int result stays.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchmarks.compat2.probes.calibrate import v1_lowered_ids

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE_FILES = ("builtins", "surface", "modules", "language")
FENCE_BUCKETS = ("probe wrong", "stricter than v1", "compiler gap")
OUT_OF_SCOPE = "out of scope"
UNCLASSIFIED = "unclassified"

ZERO_ARG = frozenset({"bool", "dict", "float", "int", "list", "str", "hypot"})
ITERATORS = frozenset({"enumerate", "range", "zip"})
INT_OPERANDS = frozenset({"getattr", "hasattr", "shutil.copyfile"})
RESULT_CLASS = {"re.compile": "re.Pattern", "urllib.parse.urlsplit": "urllib.parse.SplitResult"}


@dataclass(frozen=True)
class Ctx:
    row: dict[str, object]
    v1: dict[str, object] | None
    fn: ast.FunctionDef
    lowered: bool

    @property
    def annotation(self) -> str | None:
        for stmt in self.fn.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "r":
                return ast.unparse(stmt.annotation) if stmt.annotation is not None else None
        return None


@dataclass(frozen=True)
class Rule:
    id: str
    bucket: str
    summary: str
    match: Callable[[Ctx], str | None]


@dataclass(frozen=True)
class Decision:
    id: str
    area: str
    kind: str
    category: str
    bucket: str
    rule: str
    detail: str

    @property
    def cell(self) -> str:
        return f"{self.area}/{self.category}/{self.kind}"


@dataclass(frozen=True)
class Report:
    iteration: str
    not_passing: int
    fence_expected: int
    build_error_expected: int
    build_error_cells: tuple[str, ...]
    decisions: tuple[Decision, ...]

    @property
    def fences(self) -> tuple[Decision, ...]:
        return tuple(item for item in self.decisions if item.bucket in FENCE_BUCKETS)

    @property
    def out_of_scope(self) -> tuple[Decision, ...]:
        return tuple(item for item in self.decisions if item.bucket == OUT_OF_SCOPE)

    @property
    def unclassified(self) -> tuple[Decision, ...]:
        return tuple(item for item in self.decisions if item.bucket == UNCLASSIFIED)


def _probe_function(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "probe":
            return node
    raise ValueError("no probe function")


def _primary_call(fn: ast.FunctionDef) -> ast.Call | None:
    for stmt in fn.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            return stmt.value
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Call):
            return stmt.value
    return None


def _callee(call: ast.Call) -> str:
    return ast.unparse(call.func)


def _param_type(fn: ast.FunctionDef, name: str) -> str | None:
    for arg in fn.args.args:
        if arg.arg == name and arg.annotation is not None:
            return ast.unparse(arg.annotation)
    return None


def _returns_one(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and stmt.value.value == 1


def _v1_return(source: str) -> str | None:
    fns = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
    if not fns:
        return None
    for stmt in fns[-1].body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            return ast.unparse(stmt.value)
    return None


def _call_name(fn: ast.FunctionDef) -> str | None:
    call = _primary_call(fn)
    if call is None:
        return None
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _private(annotation: str | None) -> bool:
    return annotation is not None and (annotation.startswith("_") or annotation == "builtin_function_or_method")


def match_getenv(ctx: Ctx) -> str | None:
    if ctx.row["kind"] != "value" or len(ctx.fn.body) != 2:
        return None
    expr, ret = ctx.fn.body
    if not isinstance(expr, ast.Expr) or not isinstance(expr.value, ast.Call) or not _returns_one(ret):
        return None
    call = expr.value
    if _callee(call) != "os.getenv" or len(call.args) != 1:
        return None
    return f"os.getenv({ast.unparse(call.args[0])}) then return 1; args {ctx.row['args']!r}"


def match_raises(ctx: Ctx) -> str | None:
    if ctx.row["kind"] != "raises" or not ctx.row["expect_exc"]:
        return None
    equiv = "present" if ctx.v1 is not None else "absent"
    return f"expect_exc={ctx.row['expect_exc']}; v1-equivalent probe {equiv}"


def match_private(ctx: Ctx) -> str | None:
    if not _private(ctx.annotation):
        return None
    return f"annotation {ctx.annotation}"


def match_mod(ctx: Ctx) -> str | None:
    if "% 1000003" not in str(ctx.row["source"]):
        return None
    return "seq-int fold uses `% 1000003`"


def match_int_operand(ctx: Ctx) -> str | None:
    if _private(ctx.annotation):
        return None
    call = _primary_call(ctx.fn)
    if call is None or not call.args or _callee(call) not in INT_OPERANDS:
        return None
    arg0 = call.args[0]
    if not isinstance(arg0, ast.Name) or _param_type(ctx.fn, arg0.id) != "int":
        return None
    return f"{_callee(call)} receives int parameter {arg0.id}"


def match_bisect(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) not in {"bisect.bisect_left", "bisect.bisect_right"}:
        return None
    args = ctx.row["args"]
    if not isinstance(args, list) or not args or not isinstance(args[0], str):
        return None
    return f"haystack argument is str {args[0]!r}; recorded args {args!r}"


def match_sum_bools(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "sum" or not call.args or not isinstance(call.args[0], ast.List):
        return None
    literals = [elt.value for elt in call.args[0].elts if isinstance(elt, ast.Constant)]
    if not any(value is True or value is False for value in literals):
        return None
    return f"sum of bool literals {ast.unparse(call.args[0])}"


def match_listdir(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "os.listdir" or call.args or ctx.annotation != "list[int]":
        return None
    return "os.listdir() annotated list[int]"


def match_parse_qs(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "urllib.parse.parse_qs" or ctx.annotation != "dict[str, int]":
        return None
    return f"parse_qs annotated dict[str, int]; args {ctx.row['args']!r}"


def match_struct(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) not in {"struct.pack", "struct.unpack"}:
        return None
    if not call.args or not isinstance(call.args[0], ast.Name):
        return None
    return f"{_callee(call)} format is parameter {call.args[0].id}; args {ctx.row['args']!r}"


def match_empty_tuple(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "tuple" or call.args or ctx.annotation != "tuple[int, ...]":
        return None
    return "tuple() annotated tuple[int, ...]"


def match_rmtree(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "shutil.rmtree" or len(call.args) != 2:
        return None
    return f"shutil.rmtree called with 2 arguments; args {ctx.row['args']!r}"


def match_posix_path(ctx: Ctx) -> str | None:
    if ctx.annotation != "pathlib.PosixPath":
        return None
    return "annotation pathlib.PosixPath"


def match_enum(ctx: Ctx) -> str | None:
    if ctx.row["category"] != "constant" or ctx.row["kind"] != "value" or ctx.v1 is None or len(ctx.fn.body) != 2:
        return None
    assign, ret = ctx.fn.body
    if not isinstance(assign, ast.AnnAssign) or not isinstance(assign.value, ast.Attribute):
        return None
    if not isinstance(assign.annotation, ast.Attribute) or not _returns_one(ret):
        return None
    v1_expr = _v1_return(str(ctx.v1["source"]))
    assigned = ast.unparse(assign.value)
    if v1_expr != assigned:
        return None
    return f"annotation {ast.unparse(assign.annotation)}; v1 returns {v1_expr}; v2 returns 1"


def match_zero_arg(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or call.args or call.keywords or _call_name(ctx.fn) not in ZERO_ARG:
        return None
    return f"{_callee(call)}() annotation {ctx.annotation}"


def match_iterator(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    name = _call_name(ctx.fn)
    if call is None or name not in ITERATORS or ctx.annotation != name or not _returns_one(ctx.fn.body[-1]):
        return None
    return f"{name} materialized as {name}; returns 1"


def match_container(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or call.args:
        return None
    name = _callee(call)
    if name not in {"collections.OrderedDict", "collections.deque"}:
        return None
    if ctx.annotation != name or not _returns_one(ctx.fn.body[-1]):
        return None
    return f"{name}() annotation {name}; returns 1"


def match_split(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or ctx.annotation != "tuple[str, str]":
        return None
    name = _callee(call)
    if name not in {"posixpath.split", "posixpath.splitext"}:
        return None
    return f"{name} annotated tuple[str, str]"


def match_result_class(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or not _returns_one(ctx.fn.body[-1]):
        return None
    name = _callee(call)
    expected = RESULT_CLASS.get(name)
    if expected is None or expected != ctx.annotation:
        return None
    return f"{name} annotated {ctx.annotation}; returns 1"


def match_strftime(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "time.strftime" or len(call.args) != 1 or ctx.annotation != "str":
        return None
    return "time.strftime(format) annotated str"


def match_mirrored(ctx: Ctx) -> str | None:
    call = _primary_call(ctx.fn)
    if call is None or _callee(call) != "unicodedata.mirrored" or ctx.annotation != "int":
        return None
    ret = ctx.fn.body[-1]
    if not isinstance(ret, ast.Return) or not isinstance(ret.value, ast.Name):
        return None
    return "unicodedata.mirrored annotated int and returned"


RULES: tuple[Rule, ...] = (
    Rule(
        "out-of-scope-getenv",
        OUT_OF_SCOPE,
        "Value probe whose body is os.getenv of one parameter then return 1. The calibration aggregate's "
        "single build-error, in cell surface/function/value. Not a fence bucket.",
        match_getenv,
    ),
    Rule(
        "raises-fallback",
        "probe wrong",
        "kind raises with expect_exc set. Argument search kept an exception. v1 had lowered the name.",
        match_raises,
    ),
    Rule(
        "private-annotation",
        "probe wrong",
        "Result annotation starts with '_' or is builtin_function_or_method. v1 folded the name to an int.",
        match_private,
    ),
    Rule(
        "seq-int-remainder",
        "probe wrong",
        "Source contains the seq-int fold '% 1000003'. v1 returned an element.",
        match_mod,
    ),
    Rule(
        "int-operand",
        "probe wrong",
        "getattr, hasattr, or shutil.copyfile receives an int parameter. v1 passed an object or a path.",
        match_int_operand,
    ),
    Rule(
        "bisect-str-haystack",
        "probe wrong",
        "bisect_left or bisect_right is called with a str haystack. v1 passed a list of ints.",
        match_bisect,
    ),
    Rule(
        "sum-bools",
        "probe wrong",
        "sum of a list of bool literals. v1 summed a list of ints.",
        match_sum_bools,
    ),
    Rule(
        "listdir-elem",
        "probe wrong",
        "os.listdir() with no path, annotated list[int]. listdir returns names. v1 listed '/tmp'.",
        match_listdir,
    ),
    Rule(
        "parse-qs-elem",
        "probe wrong",
        "parse_qs annotated dict[str, int]. The function returns dict[str, list[str]]. The argument had no pair.",
        match_parse_qs,
    ),
    Rule(
        "struct-format-parameter",
        "probe wrong",
        "struct.pack or struct.unpack takes its format from a parameter. v1 inlined a format literal.",
        match_struct,
    ),
    Rule(
        "empty-tuple-annotation",
        "probe wrong",
        "tuple() annotated tuple[int, ...], the empty-tuple default. v1 indexed a two-int tuple literal.",
        match_empty_tuple,
    ),
    Rule(
        "rmtree-unchecked",
        "probe wrong",
        "shutil.rmtree called with two arguments and the result discarded. v1 checked the tree was gone.",
        match_rmtree,
    ),
    Rule(
        "posixpath-concrete-class",
        "probe wrong",
        "Annotation pathlib.PosixPath, the platform subclass. The public constructor is pathlib.Path.",
        match_posix_path,
    ),
    Rule(
        "enum-constant",
        "stricter than v1",
        "Constant bound to its enum class and returned as 1. v1 returned the integer attribute. Kept.",
        match_enum,
    ),
    Rule(
        "zero-arg-constructor",
        "compiler gap",
        "bool, dict, float, int, list, str, or math.hypot called with no arguments. v1 passed arguments. Kept.",
        match_zero_arg,
    ),
    Rule(
        "iterator-object",
        "compiler gap",
        "enumerate, range, or zip materialized as that type. v1 only used it in a for. Kept.",
        match_iterator,
    ),
    Rule(
        "container-class",
        "compiler gap",
        "OrderedDict() or deque() annotated as that class. v1 checked len or pop. Kept.",
        match_container,
    ),
    Rule(
        "split-tuple",
        "compiler gap",
        "posixpath.split or splitext annotated tuple[str, str], CPython's return. v1 folded len. Kept.",
        match_split,
    ),
    Rule(
        "result-class",
        "compiler gap",
        "re.compile annotated re.Pattern, or urlsplit annotated SplitResult. v1 folded a string attribute. Kept.",
        match_result_class,
    ),
    Rule(
        "strftime-format-only",
        "compiler gap",
        "time.strftime called with only the format. The one-argument form stays. v1 also passed a struct_time.",
        match_strftime,
    ),
    Rule(
        "mirrored-int",
        "compiler gap",
        "unicodedata.mirrored annotated int and the int returned. v1 branched on the value. Kept.",
        match_mirrored,
    ),
)


def _load_jsonl(path: Path) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            row = json.loads(line)
            rows[str(row["id"])] = row
    return rows


def _load_probes() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for name in PROBE_FILES:
        rows.update(_load_jsonl(HERE / f"{name}.jsonl"))
    return rows


def _newest_iteration() -> Path:
    return sorted((ROOT / "benchmarks" / "compat" / "iterations").glob("*.json"))[-1]


def _build_error_cells(calibration: dict[str, object]) -> tuple[str, ...]:
    cells = calibration["cells"]
    assert isinstance(cells, dict)
    found = [key for key, counts in cells.items() if isinstance(counts, dict) and "build-error" in counts]
    return tuple(sorted(str(key) for key in found))


def _decide(ctx: Ctx) -> Decision:
    area = str(ctx.row["area"])
    kind = str(ctx.row["kind"])
    category = str(ctx.row["category"])
    probe_id = str(ctx.row["id"])
    if not ctx.lowered:
        return Decision(probe_id, area, kind, category, UNCLASSIFIED, "", "v1 status is not lowered")
    hits = [(rule, detail) for rule in RULES if (detail := rule.match(ctx)) is not None]
    if len(hits) != 1:
        names = ", ".join(rule.id for rule, _detail in hits) or "none"
        return Decision(probe_id, area, kind, category, UNCLASSIFIED, "", f"matched {names}")
    rule, detail = hits[0]
    return Decision(probe_id, area, kind, category, rule.bucket, rule.id, detail)


def build_report() -> Report:
    calibration = json.loads((HERE / "CALIBRATION.json").read_text(encoding="utf-8"))
    lowered_block = calibration["v1_lowered"]
    assert isinstance(lowered_block, dict)
    outcomes = lowered_block["v2_probe_outcomes"]
    assert isinstance(outcomes, dict)
    not_passing = calibration["v1_lowered_v2_probe_not_passing"]
    assert isinstance(not_passing, list)
    probes = _load_probes()
    v1equiv = _load_jsonl(HERE.parent / "continuity" / "v1equiv.jsonl")
    lowered = v1_lowered_ids()
    decisions: list[Decision] = []
    for probe_id in not_passing:
        pid = str(probe_id)
        row = probes.get(pid)
        if row is None:
            decisions.append(Decision(pid, "", "", "", UNCLASSIFIED, "", "probe record missing"))
            continue
        try:
            fn = _probe_function(str(row["source"]))
        except (SyntaxError, ValueError) as exc:
            decisions.append(
                Decision(pid, str(row["area"]), str(row["kind"]), str(row["category"]), UNCLASSIFIED, "", str(exc))
            )
            continue
        decisions.append(_decide(Ctx(row, v1equiv.get(pid + ".v1"), fn, pid in lowered)))
    return Report(
        iteration=_newest_iteration().name,
        not_passing=len(not_passing),
        fence_expected=int(outcomes["fence"]),
        build_error_expected=int(outcomes.get("build-error", 0)),
        build_error_cells=_build_error_cells(calibration),
        decisions=tuple(decisions),
    )


def problems(report: Report) -> list[str]:
    """Exit-check failures. Empty means every fence row has one bucket and the build-error is aside."""
    issues: list[str] = []
    if report.unclassified:
        shown = ", ".join(f"{item.id} ({item.detail})" for item in report.unclassified)
        issues.append(f"{len(report.unclassified)} unclassified: {shown}")
    if len(report.fences) != report.fence_expected:
        issues.append(f"fence rows {len(report.fences)} != calibration {report.fence_expected}")
    if len(report.out_of_scope) != report.build_error_expected:
        issues.append(f"out of scope {len(report.out_of_scope)} != build-error {report.build_error_expected}")
    seen: set[str] = set()
    for item in report.decisions:
        if item.id in seen:
            issues.append(f"duplicate {item.id}")
        seen.add(item.id)
    for item in report.out_of_scope:
        if item.cell not in report.build_error_cells:
            issues.append(f"{item.id} is {item.cell}, build-error cells are {list(report.build_error_cells)}")
    accounted = len(report.fences) + len(report.out_of_scope) + len(report.unclassified)
    if accounted != len(report.decisions) or len(report.decisions) != report.not_passing:
        issues.append(f"decisions {len(report.decisions)} not-passing {report.not_passing} accounted {accounted}")
    return issues


def _cell_text(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(report: Report) -> str:
    counts = Counter(item.bucket for item in report.fences)
    by_rule = Counter(item.rule for item in report.decisions if item.bucket != UNCLASSIFIED)
    lines = [
        "# v1-lowered fence triage",
        "",
        "Generated by `python -m benchmarks.compat2.probes.triage` from the frozen calibration report,",
        "the probe JSONL, `continuity/v1equiv.jsonl`, and the newest frozen v1 iteration.",
        "The script does not run the compiler. `probe wrong` is a label in this table, not a probe edit.",
        "",
        "## Inputs",
        "",
        f"- Newest frozen v1 iteration: `{report.iteration}`. v1 ids use the same spelling as",
        "  `calibrate.v1_lowered_ids` (`builtins.<id>`, `stdlib.<id>`).",
        f"- `CALIBRATION.json` `v1_lowered.v2_probe_outcomes`: fence {report.fence_expected},",
        f"  build-error {report.build_error_expected}.",
        f"- `v1_lowered_v2_probe_not_passing` has {report.not_passing} ids. That list is the fence set",
        "  plus every other non-pass. Only fence rows are classified.",
        "- Cells with `build-error`: " + ", ".join(f"`{cell}`" for cell in report.build_error_cells) + ".",
        "",
        "## Counts",
        "",
        "| bucket | rows |",
        "| --- | --- |",
    ]
    for bucket in FENCE_BUCKETS:
        lines.append(f"| {bucket} | {counts[bucket]} |")
    lines += [
        f"| fence total | {len(report.fences)} |",
        f"| unclassified | {len(report.unclassified)} |",
        f"| out of scope (build-error) | {len(report.out_of_scope)} |",
        "",
        "Fence buckets sum to the calibration fence count. Unclassified rows make this script exit non-zero.",
        "",
        "## Rules",
        "",
        "A not-passing row must match exactly one predicate. The predicates are disjoint. None or more",
        "than one is unclassified. The out-of-scope rule is the build-error and is not given a fence bucket.",
        "",
        "| rule | bucket | rows | why |",
        "| --- | --- | --- | --- |",
    ]
    for rule in RULES:
        lines.append(f"| `{rule.id}` | {rule.bucket} | {by_rule[rule.id]} | {_cell_text(rule.summary)} |")
    lines += ["", "## Rows", ""]
    for bucket in FENCE_BUCKETS:
        chosen = [item for item in report.fences if item.bucket == bucket]
        lines += [
            f"### {bucket} ({len(chosen)})",
            "",
            "| id | kind | category | rule | detail |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in sorted(chosen, key=lambda row: row.id):
            lines.append(f"| `{item.id}` | {item.kind} | {item.category} | `{item.rule}` | {_cell_text(item.detail)} |")
        lines.append("")
    lines += ["## Out of scope", ""]
    if not report.out_of_scope:
        lines.append("None.")
    for item in report.out_of_scope:
        lines += [
            f"`{item.id}` is `{item.cell}` and matches `{item.rule}`. {_cell_text(item.detail)}",
            "",
            "This is the calibration build-error. `calibrate.run_one` returns `build-error` when a value",
            "probe exits non-zero for a reason other than fence exit 3. The row is not forced into a bucket.",
            "",
        ]
    if report.unclassified:
        lines += ["## Unclassified", ""]
        for item in report.unclassified:
            lines.append(f"- `{item.id}`: {_cell_text(item.detail)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write probes/TRIAGE.md for the v1-lowered fence rows.")
    parser.add_argument("--out", default=str(HERE / "TRIAGE.md"), help="markdown path (default probes/TRIAGE.md)")
    parser.add_argument("--check", action="store_true", help="exit 1 when --out does not already match")
    opts = parser.parse_args(argv)
    report = build_report()
    text = render(report)
    dest = Path(opts.out)
    issues = problems(report)
    if opts.check:
        current = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        if current != text:
            issues.append(f"{dest} does not match the generator")
    else:
        dest.write_text(text, encoding="utf-8")
    counts = Counter(item.bucket for item in report.fences)
    summary = ", ".join(f"{bucket} {counts[bucket]}" for bucket in FENCE_BUCKETS)
    print(f"{len(report.fences)} fence rows ({summary}); unclassified {len(report.unclassified)}")
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
