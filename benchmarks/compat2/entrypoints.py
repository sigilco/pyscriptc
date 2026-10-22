"""E1 entry-point detector: script-vs-module scan and predicted-flip list.

Implements ``.agents/docs/spec/e1-entrypoint-detector-spec.md``. Scans ``examples/`` and
``benchmarks/compat2/corpus/scripts/*/main.py`` (one ``main.py`` per script
directory; ``corpus/src/`` is vendored packages and is never scanned), walks
each module body once with stdlib ``ast``, inventories every statement one
level deep inside ``__main__`` guards, and classifies each file.

A file is a script (E1-relevant) when its inventory holds at least one
statement that is not inert and not a non-guard ``If``. Inert kinds:
``Import``, ``ImportFrom``, ``FunctionDef``, ``AsyncFunctionDef``,
``ClassDef``, ``Docstring``, ``Pass``.

Validation against the current compiler is on by default: each file is
lowered through ``pyscriptc.lower.lower_file`` (the same path ``pyscriptc
compile`` uses for a file) and the record notes whether today's fences land
on an entry statement. ``predicted_flip`` is true only when the earliest
fence sits on the first non-inert module statement - the shape the spec
describes as "fences at the first non-definition statement". Pass
``--no-fences`` to skip compilation and emit a pure AST scan.

Usage:

    uv run python -m benchmarks.compat2.entrypoints [--out FILE] [--no-fences]

stdout prints the summary counts table. ``--out`` writes the full record
list as stable JSON (sorted by path, sorted keys, trailing newline,
byte-identical across runs). The predicted-flip set is the records with
``predicted_flip: true``.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = Path("examples")
SCRIPTS_DIR = Path("benchmarks/compat2/corpus/scripts")

# Kinds that never make a file entry-relevant on their own. Pass is inert too:
# there is nothing to lower, but it stays visible in statement_kinds.
INERT_KINDS = {
    "Import",
    "ImportFrom",
    "FunctionDef",
    "AsyncFunctionDef",
    "ClassDef",
    "Docstring",
    "Pass",
}


def dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return type(node).__name__


def is_main_guard(stmt: ast.stmt) -> bool:
    """The exact structural guard from lower/_stmt.py _is_main_guard."""
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    test = stmt.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def is_str_expr(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def target_names(stmt: ast.stmt) -> list[str]:
    targets: list[ast.expr | None] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
        targets = [stmt.target]
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in stmt.items if item.optional_vars is not None]
    names: list[str] = []
    for node in targets:
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            names.extend(elt.id for elt in node.elts if isinstance(elt, ast.Name))
        elif node is not None:
            names.append(dotted(node))
    return names


def def_fields(stmt: ast.stmt) -> dict[str, Any]:
    out: dict[str, Any] = {"decorators": [], "bases": []}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out["decorators"] = [dotted(dec) for dec in stmt.decorator_list]
    if isinstance(stmt, ast.ClassDef):
        out["bases"] = [dotted(base) for base in stmt.bases]
    return out


def record_for(stmt: ast.stmt, where: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"kind": type(stmt).__name__, "line": stmt.lineno, "where": where}
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
        rec["targets"] = target_names(stmt)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        rec["call"] = dotted(stmt.value)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        rec.update(def_fields(stmt))
    if isinstance(stmt, ast.If):
        rec["main_guard"] = is_main_guard(stmt)
    return rec


def is_entry_record(rec: dict[str, Any]) -> bool:
    """A record counts toward entry_stmt_count: non-inert, and a guard If is
    inert itself (its body records are what count)."""
    return rec["kind"] not in INERT_KINDS and not (rec["kind"] == "If" and rec.get("main_guard"))


def entry_spans(records: list[dict[str, Any]], tree: ast.Module) -> list[tuple[int, int]]:
    """Line spans of non-inert statements, so a fence can be mapped back to
    the entry statement it sits on. Multi-line statements count by span."""
    spans: list[tuple[int, int]] = []

    def add(stmt: ast.stmt) -> None:
        if type(stmt).__name__ in INERT_KINDS or (isinstance(stmt, ast.If) and is_main_guard(stmt)):
            return
        spans.append((stmt.lineno, stmt.end_lineno or stmt.lineno))

    for stmt in tree.body:
        if isinstance(stmt, ast.If) and is_main_guard(stmt):
            for inner in stmt.body:
                add(inner)
        else:
            add(stmt)
    return spans


def scan_file(path: Path, *, rel: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
    records: list[dict[str, Any]] = []
    for i, stmt in enumerate(tree.body):
        if i == 0 and is_str_expr(stmt):
            records.append({"kind": "Docstring", "line": stmt.lineno, "where": "top"})
            continue
        records.append(record_for(stmt, "top"))
        if isinstance(stmt, ast.If) and is_main_guard(stmt):
            # Inventory one level deep, per spec. Inner ifs are not flattened.
            for inner in stmt.body:
                records.append(record_for(inner, "guard"))
    entry = [rec for rec in records if is_entry_record(rec)]
    return {
        "path": rel.as_posix(),
        "statement_kinds": [rec["kind"] for rec in records],
        "statements": records,
        "entry_stmt_count": len(entry),
        "print_calls": sum(1 for rec in records if rec.get("call") == "print"),
        "has_main_guard": any(rec.get("main_guard") for rec in records),
        "is_script": bool(entry),
        "_entry_spans": entry_spans(records, tree),
    }


def fences_today(path: Path) -> list[dict[str, Any]]:
    """Lower the file through the normal CLI path and return fence dicts."""
    from pyscriptc.lower import lower_file

    module, _ = lower_file(str(path))
    return module["fences"]


def validate(record: dict[str, Any], path: Path) -> None:
    """Annotate a scan record with today's compiler fences.

    ``fences_at_entry`` lists the entry-statement lines a fence sits on;
    ``predicted_flip`` is true only when the file is a script AND the
    earliest fence in the file sits on the first non-inert module
    statement - "the file currently fences at the first non-definition
    statement". Files that already compile (zero fences) have nothing left
    to flip; files whose first fence lives inside a definition need more
    than entry lowering.
    """
    spans = record.pop("_entry_spans", [])
    try:
        fences = fences_today(path)
    except Exception as exc:  # lowering crashed; treat as fenced, unlocatable
        record.update(
            {
                "fences_today": True,
                "fences_at_entry": [],
                "first_fence": {"code": "LOWER_ERROR", "line": 0, "function": None, "message": str(exc)},
                "predicted_flip": False,
            }
        )
        return
    fence_rows = [
        {"code": f["code"], "line": f["span"]["line"], "function": f["function"], "message": f["message"]}
        for f in fences
    ]
    at_entry = sorted({f["span"]["line"] for f in fences if any(lo <= f["span"]["line"] <= hi for lo, hi in spans)})
    first_fence = min(fence_rows, key=lambda f: f["line"], default=None)
    predicted = (
        bool(record["is_script"])
        and first_fence is not None
        and any(lo <= first_fence["line"] <= hi for lo, hi in spans)
    )
    record.update(
        {
            "fences_today": bool(fences),
            "fences_at_entry": at_entry,
            "first_fence": first_fence,
            "predicted_flip": predicted,
        }
    )


def iter_scan_paths() -> list[tuple[Path, Path]]:
    """(absolute path, repo-relative path) for every file in scan scope."""
    out: list[tuple[Path, Path]] = []
    for path in sorted((REPO_ROOT / EXAMPLES_DIR).glob("*.py")):
        out.append((path, EXAMPLES_DIR / path.name))
    for path in sorted((REPO_ROOT / SCRIPTS_DIR).glob("*/main.py")):
        out.append((path, SCRIPTS_DIR / path.parent.name / "main.py"))
    return out


def scan(*, with_fences: bool) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, rel in iter_scan_paths():
        try:
            record = scan_file(path, rel=rel)
        except SyntaxError as exc:
            record = {"path": rel.as_posix(), "error": str(exc), "is_script": False}
        if "error" not in record:
            if with_fences:
                validate(record, path)
            else:
                record.pop("_entry_spans", None)
        records.append(record)
    return sorted(records, key=lambda r: r["path"])


def serialize(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def summary(records: list[dict[str, Any]]) -> str:
    lines = []
    for label, group in (
        ("examples/", [r for r in records if r["path"].startswith("examples/")]),
        ("corpus/scripts", [r for r in records if r["path"].startswith(SCRIPTS_DIR.as_posix())]),
    ):
        scripts = sum(1 for r in group if r.get("is_script"))
        errors = sum(1 for r in group if "error" in r)
        lines.append(
            f"{label:16} files={len(group):3} scripts={scripts:3} "
            f"modules={len(group) - scripts - errors:3} errors={errors}"
        )
    flips = [r for r in records if r.get("predicted_flip")]
    if flips or any("predicted_flip" in r for r in records):
        lines.append(f"predicted flips: {len(flips)}")
        for r in flips:
            lines.append(f"  {r['path']}: first fence at entry statement (line {r['first_fence']['line']})")
        flipped = [r["path"] for r in records if r.get("is_script") and not r.get("fences_today")]
        if flipped:
            lines.append(f"already compiling (flipped before E1 finished): {len(flipped)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.compat2.entrypoints",
        description="E1 entry-point detector: scan examples/ and the scripts corpus, emit the predicted-flip list.",
    )
    parser.add_argument("--out", help="write the full record list as stable JSON to FILE")
    parser.add_argument(
        "--no-fences",
        action="store_true",
        help="pure AST scan; skip lowering each file through the current compiler",
    )
    args = parser.parse_args(argv)
    records = scan(with_fences=not args.no_fences)
    if args.out:
        Path(args.out).write_text(serialize(records), encoding="utf-8")
    print(summary(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
