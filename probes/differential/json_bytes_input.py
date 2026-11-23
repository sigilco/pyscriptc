# SPDX-License-Identifier: Apache-2.0
"""`json.loads` over a `bytes` input (issue #495).

CPython's `json.loads` accepts `bytes`, `bytearray` and `str`, and it
decodes the bytes as UTF-8 before the parse, so a malformed sequence
raises the UnicodeDecodeError family where a scan error would be. The
same document reads the same values from the text form and from the byte
form. Both sides run this same program and the lane byte-compares stdout.
"""

import json

d: dict[str, int] = json.loads(b'{"a": 7}')
print(d["a"])

xs: list[int] = json.loads(b"[1, 2, 3]")
print(xs[0] + xs[1] + xs[2])

s: str = json.loads(b'"caf\\u00e9"')
print(s)

raw: str = json.loads(b'"caf\xc3\xa9"')
print(raw)

b: bool = json.loads(b"true")
print(b)

f: float = json.loads(b"1.5")
print(f)
