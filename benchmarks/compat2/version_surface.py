"""C23 version-track survey: per-version syntax surface of the scanned corpus.

Read-only, stdlib only. For each target version 3.10 through 3.14 the tool
parses every scanned file with ``ast.parse(feature_version=...)`` and walks
the newest-grammar AST for version-gated node kinds (``type`` statements,
``except*``, t-strings, match statements, PEP 695 generic syntax). It reports,
per file, the minimum version the file parses at and the exact features that
gate it, plus per-version tallies.

Scan set matches ``benchmarks/compat2/entrypoints.py``: ``examples/`` plus
``benchmarks/compat2/corpus/scripts/*/main.py`` (``corpus/src/`` vendored
packages and the holdout are never scanned).

Usage:

    uv run python -m benchmarks.compat2.version_surface [--out FILE]

stdout prints a stable JSON summary (sorted keys, trailing newline,
byte-identical across runs). ``--out`` writes the same document to a file;
nothing else is written.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

from benchmarks.compat2.entrypoints import is_entry_record, is_main_guard, is_str_expr, record_for

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = Path("examples")
SCRIPTS_DIR = Path("benchmarks/compat2/corpus/scripts")

TARGET_VERSIONS = [(3, minor) for minor in range(10, 15)]
VERSION_LABELS = [f"3.{minor}" for _, minor in TARGET_VERSIONS]
NEWEST = TARGET_VERSIONS[-1]

# Version-gated AST node kinds relevant to the subset. Grammar-level gates the
# parser enforces via feature_version appear here too so a node walk can name
# the exact feature even when the syntax is a soft keyword (match) or when the
# grammar accepts the spelling but the semantics changed.
#
# kind -> (feature slug, minimum version label)
NODE_GATES: dict[str, tuple[str, str]] = {
    # 3.10: structural pattern matching (soft keyword: parses at every
    # supported feature_version, so only the node walk sees it).
    "Match": ("match_statement", "3.10"),
    # 3.11: except* / exception groups.
    "TryStar": ("except_star", "3.11"),
    # 3.12: PEP 695 type statements and generic syntax.
    "TypeAlias": ("type_alias", "3.12"),
    "TypeVar": ("pep695_type_var", "3.12"),
    "ParamSpec": ("pep695_param_spec", "3.12"),
    "TypeVarTuple": ("pep695_type_var_tuple", "3.12"),
    # 3.14: PEP 750 t-strings.
    "TemplateStr": ("t_string", "3.14"),
    "Interpolation": ("t_string", "3.14"),
}

# Grammar-reported SyntaxError messages -> feature slug. CPython's
# feature_version gate raises descriptive errors; mapping them keeps the
# "exact feature" column stable instead of leaking prose.
MESSAGE_FEATURES = [
    (re.compile(r"Type statement", re.I), "type_alias"),
    (re.compile(r"Exception groups", re.I), "except_star"),
    (re.compile(r"t-strings", re.I), "t_string"),
    (re.compile(r"f-string", re.I), "fstring_grammar"),
    (re.compile(r"except expressions without parentheses", re.I), "except_unparenthesized"),
]


def version_ge(label: str, floor: tuple[int, int]) -> bool:
    return tuple(int(p) for p in label.split(".")) >= floor


def feature_for_syntax_error(exc: SyntaxError) -> str:
    msg = exc.msg or "unknown"
    for pattern, slug in MESSAGE_FEATURES:
        if pattern.search(msg):
            return slug
    return f"syntax:{msg.splitlines()[0].strip()}"


def scan_files() -> list[Path]:
    """The entrypoint scan set, in stable order."""
    files = sorted((REPO_ROOT / EXAMPLES_DIR).glob("*.py"))
    files += sorted((REPO_ROOT / SCRIPTS_DIR).glob("*/main.py"))
    return files


def classify(tree: ast.Module) -> str:
    """script|module, reusing the entrypoints inventory rules."""
    records: list[dict[str, Any]] = []
    for i, stmt in enumerate(tree.body):
        if i == 0 and is_str_expr(stmt):
            continue
        records.append(record_for(stmt, "top"))
        if isinstance(stmt, ast.If) and is_main_guard(stmt):
            records.extend(record_for(inner, "guard") for inner in stmt.body)
    return "script" if any(is_entry_record(rec) for rec in records) else "module"


def gated_features(tree: ast.AST) -> list[dict[str, Any]]:
    """Version-gated node kinds found in the newest-grammar parse, sorted."""
    found: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        gate = NODE_GATES.get(type(node).__name__)
        if gate is None:
            continue
        slug, min_version = gate
        line = getattr(node, "lineno", 0)
        prev = found.get(slug)
        if prev is None or line < prev["line"]:
            found[slug] = {"feature": slug, "min_version": min_version, "line": line}
    return sorted(found.values(), key=lambda f: (f["min_version"], f["feature"], f["line"]))


def scan_file(path: Path, rel: Path | None = None) -> dict[str, Any]:
    rel = (rel or path.relative_to(REPO_ROOT)).as_posix()
    source = path.read_text(encoding="utf-8")
    parse: dict[str, Any] = {}
    first_failure: dict[str, Any] | None = None
    tree: ast.Module | None = None
    for label, fv in zip(VERSION_LABELS, TARGET_VERSIONS, strict=True):
        try:
            cand = ast.parse(source, filename=rel, feature_version=fv)
        except SyntaxError as exc:
            feature = feature_for_syntax_error(exc)
            parse[label] = {"ok": False, "feature": feature, "line": exc.lineno or 0}
            if first_failure is None:
                first_failure = {"version": label, "feature": feature, "line": exc.lineno or 0}
        else:
            parse[label] = {"ok": True}
            if tree is None or fv == NEWEST:
                tree = cand
    min_version = next((label for label in VERSION_LABELS if parse[label]["ok"]), None)
    rec: dict[str, Any] = {
        "path": rel,
        "kind": classify(tree) if tree is not None else "unparsed",
        "min_version": min_version,
        "parse": parse,
        "features": gated_features(tree) if tree is not None else [],
    }
    if first_failure is not None:
        rec["gated_by"] = first_failure["feature"]
    return rec


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    examples = [r for r in records if r["path"].startswith("examples/")]
    corpus = [r for r in records if r["path"].startswith("benchmarks/")]
    per_version: dict[str, Any] = {}
    for label in VERSION_LABELS:
        failing = [r["path"] for r in records if not r["parse"][label]["ok"]]
        gated = sorted(
            {
                feat["feature"]
                for r in records
                for feat in r["features"]
                if not version_ge(label, tuple(int(p) for p in feat["min_version"].split(".")))
            }
            | {r["parse"][label]["feature"] for r in records if not r["parse"][label]["ok"]}
        )
        per_version[label] = {
            "parse_ok": len(records) - len(failing),
            "parse_fail": len(failing),
            "files_failing": failing,
            "features_gating": gated,
        }
    return {
        "scan": {
            "examples": {
                "files": len(examples),
                "scripts": sum(1 for r in examples if r["kind"] == "script"),
                "modules": sum(1 for r in examples if r["kind"] == "module"),
            },
            "corpus_scripts": {"files": len(corpus)},
            "total_files": len(records),
        },
        "per_version": per_version,
    }


def build_report() -> dict[str, Any]:
    records = [scan_file(path) for path in scan_files()]
    report = {
        "schema": 1,
        "versions": VERSION_LABELS,
        "files": records,
    }
    report.update(summarize(records))
    return report


def emit(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report to FILE as well")
    args = parser.parse_args(argv)
    text = emit(build_report())
    sys.stdout.write(text)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
