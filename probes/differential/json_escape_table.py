# SPDX-License-Identifier: Apache-2.0
"""The JSON escape table over the whole code-point range (issue #477).

`json.dumps` escapes the C0 control range and DEL as `\\u00XX`, writes
the two-character escapes for the quote, the backslash and the four
short forms, and leaves the rest of ASCII alone. A code point above
Latin-1 escapes as `\\uXXXX` in the default form and as the surrogate
pair above the BMP; `ensure_ascii=False` writes the same text raw as
UTF-8 but keeps the control range escaped. Both sides run this same
program and the lane byte-compares stdout, stderr and the exit code.
"""

import json

# The control range and DEL: escaped in both modes.
for code in [0x00, 0x01, 0x08, 0x0B, 0x0C, 0x1F, 0x7F]:
    ch: str = chr(code)
    print(json.dumps(ch))
    print(json.dumps(ch, ensure_ascii=False))

# The boundary code points of the UTF-8 widths.
for code in [0x20, 0x7E, 0x80, 0xE9, 0x7FF, 0x800, 0xFFFF, 0x10000, 0x10FFFF]:
    ch = chr(code)
    print(json.dumps(ch))
    print(json.dumps(ch, ensure_ascii=False))

# The two-character escapes and the short forms.
for text in ['a"b', "a\\b", "a/b", "a\nb", "a\rb", "a\tb", "a\x08b", "a\x0cb"]:
    print(json.dumps(text))
    print(json.dumps(text, ensure_ascii=False))

# The default and the raw form agree on the text they carry.
escaped: str = json.dumps("café")
raw: str = json.dumps("café", ensure_ascii=False)
print(escaped)
print(raw)
back_escaped: str = json.loads(escaped)
back_raw: str = json.loads(raw)
print(back_escaped == back_raw)

# A code point above the BMP: the surrogate pair in the default form.
pair: str = json.dumps("😀")
print(pair)
print(json.dumps("😀", ensure_ascii=False))
back_pair: str = json.loads(pair)
print(back_pair)
