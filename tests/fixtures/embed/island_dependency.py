"""An island program that imports a bundled pure-Python dependency (issue #660).

`pathspec` is pure Python and is installed in the development environment, so
a `--embed-python=bundle` compile stages it into the bundle's site-packages.
The bundle run must print the same value as CPython.
"""

import pathspec


def main() -> None:
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["*.pyc"])
    matched = [name for name in ("a.py", "b.c", "c.pyc") if spec.match_file(name)]
    print(len(matched))


if __name__ == "__main__":
    main()
