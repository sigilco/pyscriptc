"""Print the CHANGELOG.md section for a release tag, then the static release footer.

usage: release_notes.py TAG CHANGELOG FOOTER [REPO]

Fails if CHANGELOG has no `## [X.Y.Z]` section for TAG, so a release cannot
publish notes that disagree with (or lack) the changelog.

Beta and rc tags (v0.6.0-beta.1, PEP 440 0.6.0b1) additionally get a "How to
test" block: what a beta is, the install lines per channel and where to report.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PRE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)-(beta|rc)\.(\d+)$")

HOW_TO_TEST = """\
## How to test

This is a beta: an early, opt-in build of the upcoming {base} release. It is not the
stable release. pip, uv and PyPI treat it as a pre-release, so default installs never
see it.

Install it in a scratch virtualenv or tool slot (it replaces any stable pyscriptc in
that environment):

```sh
# uv tool (isolated)
uv tool install --prerelease allow pyscriptc=={version}

# pip (in a venv)
pip install --pre 'pyscriptc=={version}'
```

Prebuilt binaries are attached below; verify them against `sha256sums.txt`. Roll back
to stable with `uv tool install pyscriptc` (or `pip install pyscriptc`): the stable
version always wins its channel back. Full details, scope and rollback notes:
https://pyscriptc.pages.dev/docs/getting-started/beta/.

Please report anything you hit with the beta-feedback template:
https://github.com/{repo}/issues/new?template=beta-feedback.md
"""


def section(changelog: str, version: str) -> str | None:
    lines = changelog.splitlines()
    head = re.compile(rf"^## \[{re.escape(version)}\](\s|$)")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    tag, changelog, footer = argv[:3]
    repo = argv[3] if len(argv) == 4 else "sigilco/pyscriptc"
    m = PRE_TAG.match(tag)
    version = m.group(1) + {"beta": "b", "rc": "rc"}[m.group(2)] + m.group(3) if m else tag.removeprefix("v")
    body = section(Path(changelog).read_text(encoding="utf-8"), version)
    if not body:
        print(f"CHANGELOG.md has no non-empty section for [{version}]", file=sys.stderr)
        return 1
    print(f"## {tag}\n\n{body}\n\nFull changelog: CHANGELOG.md (section [{version}]).\n")
    if m:
        print(HOW_TO_TEST.format(base=m.group(1), version=version, repo=repo))
    print(Path(footer).read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
