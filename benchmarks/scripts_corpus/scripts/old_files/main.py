"""Keep only the files last modified before a cutoff date."""

import os
import sys
from datetime import UTC, datetime


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: old_files ROOT ISO_DATE\n")
        return 2
    cutoff: datetime = datetime.fromisoformat(sys.argv[2])
    root: str = sys.argv[1]
    old: int = 0
    for name in sorted(os.listdir(root)):
        path: str = root + "/" + name
        if not os.path.isfile(path):
            continue
        st: os.stat_result = os.stat(path)
        # UTC, so the recording does not depend on the runner's timezone.
        modified: datetime = datetime.fromtimestamp(st.st_mtime, tz=UTC).replace(tzinfo=None)
        if modified < cutoff:
            old = old + 1
            print(f"{modified.isoformat()} {path}")
    sys.stderr.write(f"{old} files older than {cutoff.isoformat()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
