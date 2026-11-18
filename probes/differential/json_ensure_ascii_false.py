# SPDX-License-Identifier: Apache-2.0
"""`json.dumps(..., ensure_ascii=False)` (issue #495).

The non-ASCII text stays raw as UTF-8 where the default escapes it as
the `\\uXXXX` sequence CPython writes (a surrogate pair above the BMP).
The reader accepts both forms, so the same text round-trips through the
escaped and the raw spelling. Both sides run this same program and the
lane byte-compares stdout (UTF-8 on both sides).
"""

import json

print(json.dumps("café"))
print(json.dumps("café", ensure_ascii=False))
print(json.dumps("😀"))
print(json.dumps("😀", ensure_ascii=False))
print(json.dumps({"k": "é"}, ensure_ascii=False))
print(json.dumps(["naïve"], ensure_ascii=False))

round_escaped: str = json.loads(json.dumps("café"))
round_raw: str = json.loads(json.dumps("café", ensure_ascii=False))
print(round_escaped == round_raw)
print(round_raw)

plain: str = json.dumps("plain ascii only")
print(plain)
