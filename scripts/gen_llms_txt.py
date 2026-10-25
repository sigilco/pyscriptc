#!/usr/bin/env python3
"""Generate llms.txt and llms-full.txt for agent consumption.

Links every documentation page under docs/ AND every public data file under
compat/data/ with its description and schema/format.

Run:
    uv run python scripts/gen_llms_txt.py [--out-dir PATH] [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "compat" / "data"
DEFAULT_OUT_DIR = ROOT / "dist" / "docs"

DOCS_BASE_URL = "https://pyscriptc.pages.dev/docs"
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


def _extract_frontmatter(content: str) -> tuple[str, str]:
    """Extract title and description from markdown frontmatter or fallback."""
    title = ""
    desc = ""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            title_m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
            if title_m:
                title = title_m.group(1).strip().strip("'\"")
            desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
            if desc_m:
                desc = desc_m.group(1).strip().strip("'\"")

    if not title:
        h1_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1_m:
            title = h1_m.group(1).strip()
        else:
            title = "Untitled"
    return title, desc


def get_doc_pages() -> list[dict[str, str]]:
    """Scan docs/ for all markdown files and return page records."""
    pages = []
    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        rel = md_path.relative_to(DOCS_DIR)
        # Skip internal docmd files
        if str(rel).startswith(".docmd"):
            continue

        text = md_path.read_text(encoding="utf-8")
        title, desc = _extract_frontmatter(text)

        # Compute URL pathname
        rel_str = str(rel)
        if rel_str == "index.md":
            path_url = f"{DOCS_BASE_URL}/"
        elif rel_str.endswith("/index.md"):
            sub = rel_str[:-9]
            path_url = f"{DOCS_BASE_URL}/{sub}/"
        elif rel_str.endswith(".md"):
            sub = rel_str[:-3]
            path_url = f"{DOCS_BASE_URL}/{sub}/"
        else:
            path_url = f"{DOCS_BASE_URL}/{rel_str}"

        pages.append(
            {
                "path": str(rel),
                "abs_path": str(md_path),
                "title": title,
                "description": desc,
                "url": path_url,
                "content": text,
            }
        )
    pages.sort(key=lambda p: p["url"])
    return pages


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


def build_llms_txt(pages: list[dict[str, str]], data_files: list[dict[str, str]]) -> str:
    """Render llms.txt."""
    lines = [
        "# pyscriptc",
        "",
        (
            "> Developer and agent documentation for pyscriptc: compile annotated Python "
            "to native binaries. Island is the floor and native is the ceiling."
        ),
        "",
        "## Documentation Files",
        "",
    ]
    for page in pages:
        lines.append(f"- [{page['title']}]({page['url']})")
        if page["description"]:
            lines.append(f"  {page['description']}")

    lines.append("")
    lines.append("## Public Data Files")
    lines.append("")
    for df in data_files:
        lines.append(f"- [{df['name']}]({df['url']})")
        lines.append(f"  {df['description']} (schema/format: {df['schema']}).")

    return "\n".join(lines).rstrip() + "\n"


def build_llms_full_txt(pages: list[dict[str, str]], data_files: list[dict[str, str]]) -> str:
    """Render llms-full.txt."""
    lines = [
        "# pyscriptc - Full Documentation and Data Context",
        "",
        "> Complete documentation text and data schema references for pyscriptc.",
        "",
        "---",
        "",
    ]
    for page in pages:
        lines.append(f"## [{page['title']}]({page['url']})")
        lines.append("")
        lines.append(page["content"].strip())
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Public Data Files Reference")
    lines.append("")
    for df in data_files:
        lines.append(f"### [{df['name']}]({df['url']})")
        lines.append(f"Description: {df['description']}.")
        lines.append(f"Schema / Format: {df['schema']}.")
        lines.append(f"Direct link: {df['url']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate(out_dir: Path) -> dict[str, str]:
    pages = get_doc_pages()
    data_files = get_data_files()
    llms_txt = build_llms_txt(pages, data_files)
    llms_full = build_llms_full_txt(pages, data_files)
    return {
        str(out_dir / "llms.txt"): llms_txt,
        str(out_dir / "llms-full.txt"): llms_full,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="directory to write llms.txt")
    parser.add_argument("--check", action="store_true", help="verify output without writing")
    args = parser.parse_args()

    files = generate(args.out_dir)

    if args.check:
        # dist/ is a build output and absent in a fresh checkout, so check
        # that generation succeeds and yields non-empty files; coverage of
        # every page and data file is asserted in tests/test_llms_txt.py.
        return 0 if all(content.strip() for content in files.values()) else 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path_str, content in files.items():
        Path(path_str).write_text(content, encoding="utf-8")
    print(f"Generated llms.txt and llms-full.txt in {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
