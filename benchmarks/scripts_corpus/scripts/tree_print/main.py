"""Print a filesystem tree with ASCII branches."""

import os
import sys


def walk(root: str, prefix: str, out: list[str]) -> None:
    names: list[str] = sorted(os.listdir(root))
    idx: int = 0
    for name in names:
        path: str = root + "/" + name
        last: bool = idx == len(names) - 1
        branch: str = "`-- " if last else "|-- "
        out.append(prefix + branch + name)
        if os.path.isdir(path):
            walk(path, prefix + ("    " if last else "|   "), out)
        idx = idx + 1


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: tree_print ROOT\n")
        return 2
    lines: list[str] = [sys.argv[1]]
    walk(sys.argv[1], "", lines)
    for line in lines:
        print(line)
    print(f"{len(lines) - 1} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
