"""Recursively list files under a root, one path per line, sorted."""

import os
import sys


def walk(root: str, out: list[str]) -> None:
    names: list[str] = sorted(os.listdir(root))
    for name in names:
        path: str = root + "/" + name
        st: os.stat_result = os.stat(path)
        if st.st_size < 0:
            continue
        out.append(path)
        if os.path.isdir(path):
            walk(path, out)


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: find_files ROOT\n")
        return 2
    found: list[str] = []
    walk(sys.argv[1], found)
    for path in found:
        print(path)
    print(f"{len(found)} paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
