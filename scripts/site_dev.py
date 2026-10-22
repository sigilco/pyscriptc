#!/usr/bin/env python3
"""Run one part of the site in a dev server.

    mise run site:dev landing   # Astro dev server, http://localhost:4321
    mise run site:dev docs      # docmd dev server, http://localhost:3000
    mise run site:dev compat    # prints how to serve the plain page

The three parts are separate apps with separate dev servers, the same way
they are separate build outputs. The assembled tree has no dev server: it is
the output of `mise run site:build`, and the way to look at it is
`mise run site:check` plus a static server over `deploy/`.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMANDS = {
    "landing": ["pnpm", "--dir", "site/landing", "dev"],
    "docs": ["pnpm", "--dir", "docs/.docmd", "exec", "docmd", "dev", "--config", "docmd.config.js"],
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    part = args[0] if args else ""

    if part == "compat":
        print("The compat page is plain files: no dev server, no bundler.")
        print("Build it and serve the output:")
        print("  mise run compat:build")
        print("  pnpm dlx serve dist/compat")
        print("The page fetches data/compat.json, so serve the directory, not the file.")
        return 0

    if part not in COMMANDS:
        print("usage: mise run site:dev landing|docs|compat", file=sys.stderr)
        return 2

    print(f"starting the {part} dev server", flush=True)
    return subprocess.run(COMMANDS[part], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
