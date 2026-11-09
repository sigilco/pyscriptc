"""Inventory raw dict-IR reads outside the emit/lower boundary (drop package P6).

Counts the subscript and `.get` sites the handoff named (`kind`, `functions`,
`classes`, `body`, `span`) plus hand-built `{"kind":` records, so later P6
conversions follow a measured ranking instead of a guessed one.

    uv run python scripts/dict_ir_consumers.py
    uv run python scripts/dict_ir_consumers.py --json
    uv run python scripts/dict_ir_consumers.py --markdown FILE

The listed modules are the P6 set from `.agents/plans/2026-11-05-cloud-agent-handoff-4.md`.
`cli/_report.py` is the CLI surface; `debug.py` is the `--debug` / `summary.json`
reader of the same lowered module. Rank is the sum of the five key reads.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "pyscriptc"

# The P6 inventory set. Order here is the plan's listing, not the ranking.
MODULES: tuple[str, ...] = (
    "cli/_report.py",
    "debug.py",
    "coverage.py",
    "package.py",
    "project.py",
    "assess.py",
    "reduce.py",
    "binding.py",
    "emitlib.py",
    "bundle.py",
    "boxed.py",
    "dwarf.py",
    "reflection.py",
    "mobile.py",
    "llvverify.py",
)

IR_KEYS: tuple[str, ...] = ("kind", "functions", "classes", "body", "span")
DICT_BUILD = re.compile(r"""\{\s*["']kind["']\s*:""")
TYPED_USE = re.compile(r"\b_ir\.\w+\(|from pyscriptc\._irnode|from \.+_irnode|import _irnode|from pyscriptc\.emit\._ir")

# Entry points that produce a lowered module dict for others to read.
PRODUCERS = frozenset({"package.py", "project.py"})


class _Reads(ast.NodeVisitor):
    """Count `obj["key"]` and `obj.get("key", ...)` for the IR keys."""

    def __init__(self) -> None:
        self.by_key: dict[str, int] = {key: 0 for key in IR_KEYS}
        self.sites: list[tuple[int, str, str]] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:
        key = _const_str(node.slice)
        if key in self.by_key:
            self.by_key[key] += 1
            self.sites.append((node.lineno, "sub", key))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
            key = _const_str(node.args[0])
            if key in self.by_key:
                self.by_key[key] += 1
                self.sites.append((node.lineno, "get", key))
        self.generic_visit(node)


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def measure_path(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    reads = _Reads()
    reads.visit(tree)
    total = sum(reads.by_key.values())
    try:
        rel = path.relative_to(SRC)
        name = str(rel)
        repo = str(path.relative_to(ROOT))
    except ValueError:
        name = path.name
        repo = str(path)
    return {
        "module": name,
        "path": repo,
        "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
        "reads": total,
        "by_key": dict(reads.by_key),
        "dict_build": len(DICT_BUILD.findall(text)),
        "typed_use": len(TYPED_USE.findall(text)),
        "role": "produce" if name in PRODUCERS else "consume",
        "sites": [{"line": line, "form": form, "key": key} for line, form, key in reads.sites],
    }


def raw_read_count(path: Path) -> int:
    """The named-key read count a seam test asserts is zero after conversion."""

    return int(measure_path(path)["reads"])


def inventory() -> list[dict[str, Any]]:
    rows = [measure_path(SRC / name) for name in MODULES]
    rows.sort(key=lambda row: (-int(row["reads"]), str(row["module"])))
    return rows


def ranking_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# P6 dict-IR consumer inventory",
        "",
        "Measured raw dict-IR reads in the drop-package P6 modules. Rank is the",
        'sum of AST `obj["key"]` and `obj.get("key")` sites for the keys',
        "`kind`, `functions`, `classes`, `body`, `span` (the handoff's count).",
        '`dict_build` is hand-built `{"kind":` records. `typed_use` is the',
        "`emit/_ir` / `_irnode` import-or-call count. Role is produce when the",
        "module lowers or merges a module dict; consume when it only reads one.",
        "",
        "Regenerate: `uv run python scripts/dict_ir_consumers.py --markdown FILE`.",
        "",
        "| rank | module | reads | kind | functions | classes | body | span | dict_build | typed_use | role |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, row in enumerate(rows, start=1):
        keys = row["by_key"]
        lines.append(
            f"| {i} | `{row['module']}` | {row['reads']} | {keys['kind']} | "
            f"{keys['functions']} | {keys['classes']} | {keys['body']} | "
            f"{keys['span']} | {row['dict_build']} | {row['typed_use']} | {row['role']} |"
        )
    lines += [
        "",
        "## Conversion order",
        "",
        "Follow this ranking. Skip a module whose `reads` is 0. Grouped PRs",
        "from the handoff stay grouped, in the order their members first appear:",
        "",
    ]
    groups = (
        ("P6-a report surface", ("cli/_report.py", "debug.py")),
        ("P6-b coverage.py", ("coverage.py",)),
        ("P6-c package.py", ("package.py",)),
        ("P6-d project.py", ("project.py",)),
        ("P6-e assess.py and reduce.py", ("assess.py", "reduce.py")),
        ("P6-f binding, emitlib, bundle, boxed", ("binding.py", "emitlib.py", "bundle.py", "boxed.py")),
    )
    ranked = {row["module"]: i for i, row in enumerate(rows, start=1)}
    scored = []
    for label, members in groups:
        member_rows = [next(r for r in rows if r["module"] == m) for m in members]
        total = sum(int(r["reads"]) for r in member_rows)
        first = min(ranked[m] for m in members)
        scored.append((first, -total, label, members, total, member_rows))
    scored.sort()
    for i, (_first, _neg, label, members, total, member_rows) in enumerate(scored, start=1):
        skip = all(int(r["reads"]) == 0 for r in member_rows)
        status = "skip (zero reads)" if skip else f"{total} reads"
        names = ", ".join(f"`{m}`" for m in members)
        lines.append(f"{i}. **{label}** ({status}): {names}")
    lines += [
        "",
        "## Notes",
        "",
        "- `cli/_report.py` is the coverage/imports/reduce CLI; it does not",
        "  walk the module dict. The `--debug` summaries live in `debug.py`.",
        "- `package.py` both produces the merged module and reads `functions` /",
        "  `classes` / `span` while merging; keep the on-disk IR shape unchanged.",
        '- `assess.py` `get("functions")` / `get("classes")` hits are the',
        "  fallback plane map, not the IR function list. They still match the",
        "  named keys, so they count; the conversion should not treat them as",
        "  IR records.",
        "- `boxed.py` likewise reads plane-map `functions`/`classes` and IR",
        "  function/class `name` records. Boxed markers stay.",
        "- `emitlib.py`, `bundle.py`, `reflection.py`, `mobile.py`,",
        "  `llvverify.py` have zero of the named reads on this snapshot.",
        '- `reduce.py` is zero on the five keys; it still reads `module.get("fences")`.',
        "  Convert it with `assess.py` as the grouped PR.",
        "- `dwarf.py` has 5 `kind` reads and is not in a grouped conversion PR;",
        "  convert it after the grouped list.",
        "- Frozen scorer `benchmarks/compat2/coverage_map.py` is not in this set.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", type=Path, default=None)
    args = ap.parse_args()
    rows = inventory()
    if args.json:
        json.dump({"modules": rows}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        text = ranking_markdown(rows)
        sys.stdout.write(text)
        if args.markdown is not None:
            args.markdown.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
