"""Throwaway AST scan backing docs/e1-entrypoint-detector-spec.md.

One-off survey tool, not a product file and not wired into any benchmark. It
exists so the counts table in the spec is reproducible and the E1 implementer
can start from working rules. Delete freely; the detector E1 builds later is a
separate, permanent tool.

Usage:

    uv run python docs/e1-entrypoint-scan.py DIR_OR_FILE [...]
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

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
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
        targets = [stmt.target]
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in stmt.items if item.optional_vars is not None]
    names: list[str] = []
    for node in targets:
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Tuple):
            names.extend(elt.id for elt in node.elts if isinstance(elt, ast.Name))
        else:
            names.append(dotted(node))
    return names


def def_fields(stmt: ast.stmt) -> dict:
    out: dict = {"decorators": [], "bases": []}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out["decorators"] = [dotted(dec) for dec in stmt.decorator_list]
    if isinstance(stmt, ast.ClassDef):
        out["bases"] = [dotted(base) for base in stmt.bases]
    return out


def record_for(stmt: ast.stmt, where: str) -> dict:
    rec: dict = {"kind": type(stmt).__name__, "line": stmt.lineno, "where": where}
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
        rec["targets"] = target_names(stmt)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        rec["call"] = dotted(stmt.value)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        rec.update(def_fields(stmt))
    if isinstance(stmt, ast.If):
        rec["main_guard"] = is_main_guard(stmt)
    return rec


def scan_file(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    kinds: list[dict] = []
    for i, stmt in enumerate(tree.body):
        if i == 0 and is_str_expr(stmt):
            kinds.append({"kind": "Docstring", "line": stmt.lineno, "where": "top"})
            continue
        kinds.append(record_for(stmt, "top"))
        if isinstance(stmt, ast.If) and is_main_guard(stmt):
            # Inventory one level deep, per spec. Inner ifs are not flattened.
            for inner in stmt.body:
                kinds.append(record_for(inner, "guard"))
    relevant = [
        rec for rec in kinds if rec["kind"] not in INERT_KINDS and not (rec["kind"] == "If" and rec.get("main_guard"))
    ]
    print_calls = sum(1 for rec in kinds if rec.get("call") == "print")
    return {
        "path": str(path),
        "statement_kinds": [rec["kind"] for rec in kinds],
        "statements": kinds,
        "entry_stmt_count": len(relevant),
        "print_calls": print_calls,
        "has_main_guard": any(rec.get("main_guard") for rec in kinds),
        "is_script": bool(relevant),
    }


def main(argv: list[str]) -> int:
    records = []
    for arg in argv:
        root = Path(arg)
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            try:
                records.append(scan_file(path))
            except SyntaxError as exc:
                records.append({"path": str(path), "error": str(exc), "is_script": False})
    scripts = [r for r in records if r.get("is_script")]
    kind_tally: dict[str, int] = {}
    for rec in records:
        for kind in rec.get("statement_kinds", []):
            kind_tally[kind] = kind_tally.get(kind, 0) + 1
    summary = {
        "files": len(records),
        "scripts": len(scripts),
        "modules": len(records) - len(scripts),
        "kind_tally": dict(sorted(kind_tally.items(), key=lambda kv: -kv[1])),
    }
    print(json.dumps({"summary": summary, "records": records}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
