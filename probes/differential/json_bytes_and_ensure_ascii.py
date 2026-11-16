# SPDX-License-Identifier: Apache-2.0
"""`json.loads` over bytes and `ensure_ascii=False` (issue #495).

`json.loads` takes a `bytes` buffer and decodes it as UTF-8 before the
parse, the way CPython does, so the same document reads back the same
values from the text form and from the byte form. `ensure_ascii=False`
writes the non-ASCII text raw as UTF-8 where the default escapes it as
`\\uXXXX`, and both forms read back the same text. A document with a
code point above the BMP escapes as a surrogate pair in the default
form and stays raw in the `ensure_ascii=False` form. Both sides run
this same program and the lane byte-compares stdout.
"""

import json

d: dict[str, int] = json.loads(b'{"a": 7}')
print(d["a"])

xs: list[int] = json.loads(b"[1, 2, 3]")
print(xs[0] + xs[1] + xs[2])

s: str = json.loads(b'"caf\\u00e9"')
print(s)

emoji: str = json.loads(b'"\\ud83d\\ude00"')
print(emoji)

escaped: str = json.dumps("café")
print(escaped)

raw: str = json.dumps("café", ensure_ascii=False)
print(raw)

back_escaped: str = json.loads(escaped)
back_raw: str = json.loads(raw)
print(back_escaped == back_raw)

pair_escaped: str = json.dumps("😀")
print(pair_escaped)

pair_raw: str = json.dumps("😀", ensure_ascii=False)
print(pair_raw)
