# SPDX-License-Identifier: Apache-2.0
"""`except json.JSONDecodeError` (issue #1246).

The json runtime raises the class the module registered, so the handler
names it and the wider `except ValueError` catches the same raise
through the parent chain, like CPython's subclassing. A `str` payload
decodes cleanly, a truncated one raises, and the value the failure
leaves in the variable stays unbound on both sides (the handler returns
before the read). Both sides run this same program and the lane
byte-compares stdout.
"""

import json


def main() -> None:
    ok: dict[str, int] = json.loads('{"a": 1}')
    print(ok["a"])
    try:
        bad: dict[str, int] = json.loads("not json")
        print(bad)
    except json.JSONDecodeError:
        print("caught JSONDecodeError")
    try:
        bad2: list[int] = json.loads("[1, ")
        print(bad2)
    except ValueError:
        print("caught ValueError")


main()
