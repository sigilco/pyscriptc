"""The environment report."""

import os
import sys

from envutil import report

USAGE = "usage: env_layer PREFIX"


def main() -> int:
    argv: list[str] = sys.argv[1:]
    if len(argv) < 1:
        sys.stderr.write(USAGE + "\n")
        return 2
    prefix: str = argv[0]
    names: list[str] = []
    for name in sorted(os.environ.keys()):
        if name.startswith(prefix):
            names.append(name)
    for line in report(names, prefix):
        print(line)
    return 0
