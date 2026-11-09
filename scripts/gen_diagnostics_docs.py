"""Generate the code tables in docs/reference/diagnostics.md from the registry.

Every `PSxxxx` code, its title, what it means and how to change your code come
from `pyscriptc.diagnostics`, the same source `pyscriptc explain` reads. The
prose around the tables is hand written and stays outside the generated
markers.

Run: python scripts/gen_diagnostics_docs.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyscriptc.diagnostics import DIAGNOSTICS  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "reference" / "diagnostics.md"

BEGIN = "<!-- gen:codes -->"
END = "<!-- /gen:codes -->"

# Which group a code belongs to, keyed by the second digit of the code.
GROUPS: list[tuple[str, str]] = [
    ("PS0", "Compiler internals"),
    ("PS1", "Source and types"),
    ("PS2", "Constructs and calls"),
    ("PS3", "Environment and configuration"),
    ("PS4", "Externs, targets and the object model"),
]


def _group_of(code: str) -> str:
    for prefix, title in GROUPS:
        if code.startswith(prefix):
            return title
    return "Other"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_codes() -> str:
    lines: list[str] = []
    for _, title in GROUPS:
        codes = sorted(code for code in DIAGNOSTICS if _group_of(code) == title)
        if not codes:
            continue
        lines.append(f"### {title}\n")
        lines.append("| Code | What it means | How to change your code |")
        lines.append("| ---- | ------------- | ---------------------- |")
        for code in codes:
            entry = DIAGNOSTICS[code]
            lines.append(f"| `{code}` | {_one_line(entry.why)} | {_one_line(entry.fix)} |")
        lines.append("")
    return "\n".join(lines).rstrip()


def render() -> str:
    text = DEFAULT_OUT.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{DEFAULT_OUT} is missing the {BEGIN} / {END} markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{render_codes()}\n{END}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit nonzero when the page is out of date")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path (default docs/reference/diagnostics.md)")
    args = ap.parse_args()

    content = render()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != content:
            print(f"{args.out} is out of date. Run: uv run python scripts/gen_diagnostics_docs.py", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
