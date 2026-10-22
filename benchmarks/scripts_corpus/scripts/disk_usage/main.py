"""Report total, used, and free bytes of filesystem paths given on argv."""

import os
import sys

UNITS: list[str] = ["B", "KiB", "MiB", "GiB", "TiB"]


def human(size: float) -> str:
    idx: int = 0
    while size >= 1024.0 and idx < len(UNITS) - 1:
        size = size / 1024.0
        idx = idx + 1
    return f"{size:.1f} {UNITS[idx]}"


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: disk_usage FILE [FILE ...]\n")
        return 2
    total: int = 0
    files: int = 0
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            st: os.stat_result = os.stat(arg)
            total = total + st.st_size
            files = files + 1
            print(f"{human(float(st.st_size)):>10}  {arg}")
    print(f"{files} files, {human(float(total))} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
