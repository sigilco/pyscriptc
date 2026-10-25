#!/usr/bin/env python3
"""Append the public data files section to docmd's llms.txt and llms-full.txt.

docmd's bundled `llms` plugin writes both files for every documentation page
during `docmd build` (see docs/.docmd/docmd.config.js). It cannot list files
that are not pages, so this post-build step appends the one section it lacks:
every public data file under compat/data/ with its description and schema.
The step is idempotent: a previous data section is replaced, not duplicated.

Run after the docmd build:
    uv run python scripts/gen_llms_txt.py [--out-dir PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "compat" / "data"
DEFAULT_OUT_DIR = ROOT / "dist" / "docs"

COMPAT_BASE_URL = "https://pyscriptc.pages.dev/compat"

DATA_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "compat.json": (
        "Live compatibility metrics across builtins, standard library, and PyPI top-100 packages",
        "compat_schema.py (version 1)",
    ),
    "ladder.json": (
        "Adoption ladder scores across R1 (single-file), R2 (multi-module), and R4 (dependency lanes) rungs",
        "ladder JSON (version 1)",
    ),
    "what_will_fail.json": (
        "Known compiler gaps and fences measured on the R0 corpus grouped by root cause",
        "what_will_fail JSON (version 1)",
    ),
    "support.json": (
        "Evaluated support status for standard library and builtin functions from compile probes",
        "support JSON (version 1)",
    ),
}


def get_data_files() -> list[dict[str, str]]:
    """Scan compat/data/ for all public JSON files and return data records."""
    records = []
    for json_path in sorted(DATA_DIR.glob("*.json")):
        name = json_path.name
        desc, schema = DATA_DESCRIPTIONS.get(
            name,
            ("Public compatibility data file", "JSON document (version 1)"),
        )
        url = f"{COMPAT_BASE_URL}/data/{name}"
        records.append(
            {
                "name": name,
                "path": str(json_path),
                "description": desc,
                "schema": schema,
                "url": url,
            }
        )
    return records


SHORT_MARKER = "## Public Data Files"
FULL_MARKER = "## Public Data Files Reference"


def short_section(data_files: list[dict[str, str]]) -> str:
    """The llms.txt section: one link and one line per data file."""
    lines = [SHORT_MARKER, ""]
    for df in data_files:
        lines.append(f"- [{df['name']}]({df['url']})")
        lines.append(f"  {df['description']} (schema/format: {df['schema']}).")
    return "\n".join(lines) + "\n"


def full_section(data_files: list[dict[str, str]]) -> str:
    """The llms-full.txt section: description, schema and direct link per file."""
    lines = [FULL_MARKER, ""]
    for df in data_files:
        lines.append(f"### [{df['name']}]({df['url']})")
        lines.append(f"Description: {df['description']}.")
        lines.append(f"Schema / Format: {df['schema']}.")
        lines.append(f"Direct link: {df['url']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_section(text: str, marker: str, section: str) -> str:
    """Return text with any earlier section starting at marker replaced by section."""
    head = text.split("\n" + marker, 1)[0] if ("\n" + marker) in text else text
    return head.rstrip() + "\n\n" + section


def apply(out_dir: Path) -> list[Path]:
    data_files = get_data_files()
    written = []
    for name, marker, section in (
        ("llms.txt", SHORT_MARKER, short_section(data_files)),
        ("llms-full.txt", FULL_MARKER, full_section(data_files)),
    ):
        path = out_dir / name
        if not path.is_file():
            raise SystemExit(f"{path} not found: run the docmd build (docs:build) first")
        path.write_text(append_section(path.read_text(encoding="utf-8"), marker, section), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="docmd output directory")
    args = parser.parse_args()
    for path in apply(args.out_dir):
        print(f"Appended public data files to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
