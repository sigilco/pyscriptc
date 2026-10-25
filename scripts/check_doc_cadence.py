#!/usr/bin/env python3
"""Check that no per-nightly metric is hardcoded in markdown documentation.

Per the documentation cadence policy (.agents/docs/doc-cadence.md), volatile
metrics (census counts, fence totals, scorer numbers, boxed ledger values,
support tables) must live in data files under compat/data/*.json, never in .md.

Run:
    uv run python scripts/check_doc_cadence.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories containing markdown documentation
DOCS_DIRS = [
    ROOT / "docs",
    ROOT / ".agents" / "docs" / "support",
]

# Patterns that indicate volatile nightly metrics hardcoded into markdown
VOLATILE_PATTERNS = [
    (
        re.compile(r"\b\d+\s+fences\s+(across|in|measured)\b", re.IGNORECASE),
        "hardcoded fence count (e.g. '386 fences across')",
    ),
    (re.compile(r"Total fences:\s*\d+", re.IGNORECASE), "hardcoded total fences"),
    (
        re.compile(r"\b\d+\s+of\s+\d+\s+scripts\b", re.IGNORECASE),
        "hardcoded script pass count (e.g. '0 of 39 scripts')",
    ),
    (re.compile(r"Pass rate:\s*\d+", re.IGNORECASE), "hardcoded pass rate"),
    (re.compile(r"pinned pure-Python libraries:\s*\d+\s*[🟢🟡🔴]", re.IGNORECASE), "hardcoded top-100 summary"),
    (re.compile(r"builtins:\s*\d+\s*[🟢🟡🔴]", re.IGNORECASE), "hardcoded builtins status count"),
    (re.compile(r"stdlib:\s*\d+\s*[🟢🟡🔴]", re.IGNORECASE), "hardcoded stdlib status count"),
    (re.compile(r"Boxed ledger:\s*code\s*\d+\.\d+%", re.IGNORECASE), "hardcoded boxed ledger measurement"),
]


def check_markdown_file(path: Path) -> list[str]:
    """Scan a markdown file for volatile metric patterns."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for line_num, line in enumerate(lines, 1):
        for pattern, description in VOLATILE_PATTERNS:
            match = pattern.search(line)
            if match:
                rel_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                violations.append(f"{rel_path}:{line_num}: found {description}: {line.strip()!r}")
    return violations


def scan_docs(roots: list[Path]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".md":
            violations.extend(check_markdown_file(root))
        else:
            for md_path in sorted(root.rglob("*.md")):
                violations.extend(check_markdown_file(md_path))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=DOCS_DIRS,
        help="paths to check (defaults to docs/ and .agents/docs/support/)",
    )
    args = parser.parse_args()

    violations = scan_docs(args.paths)
    if violations:
        print("Doc cadence check failed: volatile metrics found in markdown docs:\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("\nMove metrics to compat/data/*.json and link to the data file or compat site.", file=sys.stderr)
        return 1

    print("Doc cadence check passed: no hardcoded volatile metrics found in markdown docs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
