"""Render README.md from README.tmpl.md.

``{{DOCS_URL}}`` is replaced by ``$PYSC_DOCS_URL`` (default https://pyscriptc.pages.dev).

  uv run python scripts/render_readme.py          # write README.md
  uv run python scripts/render_readme.py --check  # exit 1 if README.md is stale
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_DOCS_URL = "https://pyscriptc.pages.dev"
ROOT = Path(__file__).resolve().parent.parent


def render(docs_url: str | None = None) -> str:
    url = (docs_url or os.environ.get("PYSC_DOCS_URL") or DEFAULT_DOCS_URL).rstrip("/")
    return (ROOT / "README.tmpl.md").read_text().replace("{{DOCS_URL}}", url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out = ROOT / "README.md"
    text = render()
    if args.check:
        if out.read_text() != text:
            print("README.md is stale: run scripts/render_readme.py", file=sys.stderr)
            return 1
        return 0
    out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
