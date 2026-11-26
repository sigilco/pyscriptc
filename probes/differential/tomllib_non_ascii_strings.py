# SPDX-License-Identifier: Apache-2.0
"""The TOML basic-string escapes over code points (issue #477).

`tomllib.loads` produces `str` values, so it works on code points.
The basic-string escapes are the JSON set plus the TOML-only forms:
the `\\uXXXX` and `\\UXXXXXXXX` escapes name a code point and decode to
it, and the raw text of a basic string keeps its non-ASCII code points
as they are. The multiline basic string carries the same escapes. Both
sides run this same program and the lane byte-compares stdout, stderr
and the exit code.
"""

import tomllib


def main() -> None:
    d: dict[str, str] = tomllib.loads(
        'a = "\\u00e9"\nb = "\\U0001f600"\nc = "\\u4e2d"\nd = "caf\\u00e9"\ne = "raw é"\nf = "\\u007f"\n'
    )
    print(d["a"])
    print(d["b"])
    print(d["c"])
    print(d["d"])
    print(d["e"])
    print(d["f"])

    multi: dict[str, str] = tomllib.loads('m = """\nmulti é\nsecond\n"""\n')
    print(multi["m"])


main()
