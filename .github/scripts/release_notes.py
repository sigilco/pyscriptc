"""Print the CHANGELOG.md section for a release tag, then the static release footer.

usage: release_notes.py TAG CHANGELOG FOOTER

Fails if CHANGELOG has no `## [X.Y.Z]` section for TAG, so a release cannot
publish notes that disagree with (or lack) the changelog.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str | None:
    lines = changelog.splitlines()
    head = re.compile(rf"^## \[{re.escape(version)}\](\s|$)")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def main() -> int:
    tag, changelog, footer = sys.argv[1:4]
    version = tag.removeprefix("v")
    body = section(Path(changelog).read_text(encoding="utf-8"), version)
    if not body:
        print(f"CHANGELOG.md has no non-empty section for [{version}]", file=sys.stderr)
        return 1
    print(f"## {tag}\n\n{body}\n\nFull changelog: CHANGELOG.md (section [{version}]).\n")
    print(Path(footer).read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
