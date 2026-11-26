# SPDX-License-Identifier: Apache-2.0
"""The orjson pack over code points (issue #477).

`orjson.dumps` never escapes the non-ASCII text the way CPython's
`json.dumps` default does: it emits the UTF-8 the code points are, the
opposite spelling. `orjson.loads` reads both the escaped and the raw
form back to the same text. Both sides run this same program and the
lane byte-compares stdout, stderr and the exit code.
"""

import orjson


def main() -> None:
    data: bytes = orjson.dumps({"k": "café", "j": "😀"})
    print(data.decode())

    d: dict[str, str] = orjson.loads('{"k": "caf\\u00e9", "j": "\\ud83d\\ude00"}')
    print(d["k"])
    print(d["j"])

    round_trip: bytes = orjson.dumps(d)
    print(round_trip.decode())


main()
