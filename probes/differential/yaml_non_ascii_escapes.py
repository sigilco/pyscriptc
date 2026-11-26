# SPDX-License-Identifier: Apache-2.0
"""The YAML double-quoted escapes over code points (issue #477).

`yaml.safe_load` produces `str` values, so it works on code points.
The double-quoted scalar's `\\x`, `\\u` and `\\U` escapes name a code
point and decode to it, so a code point above Latin-1 decodes the way
PyYAML decodes it; the raw text of a plain or quoted scalar keeps its
non-ASCII code points as they are. Both sides run this same program
and the lane byte-compares stdout, stderr and the exit code.
"""

import yaml


def main() -> None:
    d: dict[str, str] = yaml.safe_load(
        'a: "\\u00e9"\n'
        'b: "\\U0001f600"\n'
        'c: "\\u4e2d"\n'
        'd: "\\x41"\n'
        'e: "\\x80"\n'
        'f: "\\xff"\n'
        'g: raw é\n'
        'h: "quoted é"\n'
    )
    print(d["a"])
    print(d["b"])
    print(d["c"])
    print(d["d"])
    print(d["e"])
    print(d["f"])
    print(d["g"])
    print(d["h"])


main()
