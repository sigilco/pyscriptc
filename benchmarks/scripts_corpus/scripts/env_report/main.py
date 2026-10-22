"""Print the environment variables that start with a given prefix."""

import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: env_report PREFIX\n")
        return 2
    prefix: str = sys.argv[1]
    names: list[str] = sorted(os.environ.keys())
    shown: int = 0
    for name in names:
        if name.startswith(prefix):
            shown = shown + 1
            print(f"{name}={os.environ[name]}")
    sys.stderr.write(f"{shown} variables with prefix {prefix}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
