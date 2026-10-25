#!/usr/bin/env python3
"""Check every internal link in the docs sources.

docmd's link validator reads the raw markdown and does not run docmd plugins,
so `{{PYSC_COMPAT_URL}}` would be reported as a broken relative link. This
script copies docs/ to a temp directory, expands the placeholders the same way
the site build does, and runs docmd's build (which validates every link) over
the copy.

Placeholders and their defaults live in docs/.docmd/plugins/placeholders.js;
the environment variable overrides the default in both. Keep the two in sync.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCMD = ROOT / "docs" / ".docmd" / "node_modules" / ".bin" / "docmd"

PLACEHOLDERS = {
    "PYSC_COMPAT_URL": ("PYSC_COMPAT_URL", "https://pyscriptc.pages.dev/compat"),
    "PYSC_DOCS_URL": ("PYSC_DOCS_URL", "https://pyscriptc.pages.dev"),
}


def expand(text: str) -> str:
    for name, (env, default) in PLACEHOLDERS.items():
        text = text.replace("{{" + name + "}}", os.environ.get(env) or default)
    return text


def main() -> int:
    docs = ROOT / "docs"
    if not docs.is_dir():
        print(f"docs directory not found: {docs}", file=sys.stderr)
        return 2
    if not DOCMD.exists():
        print("docmd not installed: run `pnpm --dir docs/.docmd install` (or `mise run setup`)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(docs, work / "docs", ignore=shutil.ignore_patterns("node_modules"))
        (work / "docs" / ".docmd" / "node_modules").symlink_to(
            ROOT / "docs" / ".docmd" / "node_modules", target_is_directory=True
        )
        for path in sorted((work / "docs").rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            path.write_text(expand(text), encoding="utf-8")
        # The docmd config lives in docs/.docmd/ and resolves src relative to
        # its own directory, so the validator runs from there.
        result = subprocess.run(
            [str(DOCMD), "validate"],
            cwd=work / "docs" / ".docmd",
            capture_output=True,
            text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
