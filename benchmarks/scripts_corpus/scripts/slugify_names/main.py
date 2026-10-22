"""Rename files in a directory by a slug rule."""

import os
import re
import sys

SLUG_RE: re.Pattern[str] = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    lowered: str = name.lower()
    parts: list[str] = SLUG_RE.split(lowered)
    joined: str = "-".join(parts)
    return joined.strip("-")


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: slugify_names DIR\n")
        return 2
    root: str = sys.argv[1]
    renamed: int = 0
    for name in sorted(os.listdir(root)):
        path: str = root + "/" + name
        if not os.path.isfile(path):
            continue
        stem: str = name.rsplit(".", 1)[0]
        suffix: str = name.rsplit(".", 1)[1] if "." in name else ""
        target: str = slugify(stem) + ("." + suffix if suffix else "")
        if target != name:
            print(f"{name} -> {target}")
            renamed = renamed + 1
    sys.stderr.write(f"{renamed} files would be renamed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
