#!/usr/bin/env python3
"""Build the compat page into `dist/compat/`.

The page is plain HTML, CSS and JavaScript: one file each, no framework, no
bundler. The build copies them next to the data file, so the deployed tree is

    dist/compat/index.html
    dist/compat/compat.css
    dist/compat/compat.js
    dist/compat/data/compat.json

The page fetches `data/compat.json` at runtime and renders the sections the
schema defines. Nothing here reads the compiler, so the build cannot fail on
a measurement and cannot invent numbers.

Usage:

    uv run python compat/build.py [--out dist/compat] [--data compat/data/compat.json]

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / "compat"
PAGE = COMPAT / "site"
DEFAULT_OUT = ROOT / "dist" / "compat"
DEFAULT_DATA = COMPAT / "data" / "compat.json"
DEFAULT_SUMMARY = COMPAT / "data" / "summary.json"

# The files the page is made of. Kept explicit so a stray file in the source
# directory does not silently end up in the deploy tree.
PAGE_FILES = ("index.html", "compat.css", "compat.js")

# The logo and the favicon, copied from the repository assets so the page
# does not depend on the docs build for its images.
ASSETS = {name: ROOT / "docs" / "assets" / name for name in ("logo-256.png", "icon-64.png")}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="the compat JSON file")
    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_SUMMARY,
        help="the summary JSON file the landing page can consume",
    )
    parser.add_argument(
        "--base",
        default="/compat/",
        help="the path the page is served under, for the page to build its own URLs",
    )
    return parser.parse_args(argv)


def check_data(path: Path) -> dict:
    """Read the data file and fail loudly when it is missing or malformed."""
    if not path.exists():
        print(f"compat data file not found: {path}", file=sys.stderr)
        print("build it with: uv run python scripts/build_compat_json.py", file=sys.stderr)
        raise SystemExit(2)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"compat data file is not valid JSON: {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(document, dict):
        print(f"compat data file must hold a JSON object: {path}", file=sys.stderr)
        raise SystemExit(2)
    return document


def build(out: Path, data: Path, base: str, summary: Path | None = None) -> Path:
    """Copy the page and the data into `out` and return the output path.

    `summary` is the small headline-count file the landing page consumes; it
    ships next to the data when it exists, and the build does not fail when
    it does not (the summary is optional for the compat page itself).
    """
    document = check_data(data)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in PAGE_FILES:
        source = PAGE / name
        if not source.exists():
            print(f"missing page file: {source}", file=sys.stderr)
            raise SystemExit(2)
        shutil.copy2(source, out / name)

    data_out = out / "data"
    data_out.mkdir()
    shutil.copy2(data, data_out / "compat.json")
    summary = summary or DEFAULT_SUMMARY
    if summary.exists():
        shutil.copy2(summary, data_out / "summary.json")

    for name, source in ASSETS.items():
        if not source.exists():
            print(f"missing asset: {source}", file=sys.stderr)
            raise SystemExit(2)
        shutil.copy2(source, out / name)

    # The page builds its own URLs from the base path, so the same build works
    # under /compat/ on the site and under / in a local preview.
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    html = html.replace("{{BASE}}", base)
    index.write_text(html, encoding="utf-8")

    print(f"compat page built in {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    print(f"  sections: {', '.join(k for k in document if isinstance(document[k], list))}")
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build(args.out, args.data, args.base, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
