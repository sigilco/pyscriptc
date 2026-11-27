# SPDX-License-Identifier: Apache-2.0
# Issue #415: `os.fspath(x)` over a str is the identity (CPython returns
# the argument unchanged), so a package that normalizes its path argument
# (`IniConfig.__init__` calls `os.fspath(path)`) lowers without a fence.
import os


def main() -> None:
    print(os.fspath("a/b.txt"))
    print(os.fspath("plain"))


if __name__ == "__main__":
    main()
