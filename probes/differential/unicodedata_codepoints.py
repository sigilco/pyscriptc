# SPDX-License-Identifier: Apache-2.0
"""The `unicodedata` surface over code points (issue #476).

Every answer here is a code-point property, so the probe walks the
non-ASCII range: the Latin-1 letters, the combining marks, the Hangul
syllables, the CJK ideographs and the astral planes. The runtime answers
from the generated Unicode tables, so the lane byte-compares the two
sides over a range that a Latin-1-only table cannot answer.

The forms the probe exercises are the ones the S4 slice carries: the
general category, the name, the lookup, the combining class, the numeric
values, the decomposition and the four normalization forms.
"""

import unicodedata

# The Latin-1 letters and the combining marks.
print(unicodedata.category("é"))
print(unicodedata.category("ñ"))
print(unicodedata.category("́"))
print(unicodedata.name("é"))
print(unicodedata.name("ñ"))
print(unicodedata.name("́"))
print(unicodedata.combining("́"))
print(unicodedata.combining("a"))

# The algorithmic names: the Hangul syllables and the CJK ideographs.
print(unicodedata.name("가"))
print(unicodedata.name("한"))
print(unicodedata.name("一"))
print(unicodedata.name("鿿"))
print(unicodedata.lookup("HANGUL SYLLABLE GA"))
print(unicodedata.lookup("CJK UNIFIED IDEOGRAPH-4E00"))

# The formal aliases.
print(unicodedata.lookup("NULL"))
print(unicodedata.lookup("LINE FEED"))
print(unicodedata.lookup("NO-BREAK SPACE"))

# The numeric values, including the ones no binary fraction holds.
print(unicodedata.numeric("¼"))
print(unicodedata.numeric("⅛"))
print(unicodedata.numeric("↉"))
print(unicodedata.decimal("٣"))
print(unicodedata.digit("٣"))

# The decompositions, canonical and compatibility.
print(unicodedata.decomposition("é"))
print(unicodedata.decomposition("½"))
print(unicodedata.decomposition("가"))

# The four normalization forms.
print(repr(unicodedata.normalize("NFC", "é")))
print(repr(unicodedata.normalize("NFD", "é")))
print(repr(unicodedata.normalize("NFKC", "½")))
print(repr(unicodedata.normalize("NFKD", "½")))
print(unicodedata.is_normalized("NFC", "é"))
print(unicodedata.is_normalized("NFD", "é"))

# The remaining properties.
print(unicodedata.bidirectional("א"))
print(unicodedata.east_asian_width("一"))
print(unicodedata.mirrored("("))
print(unicodedata.unidata_version)

# The error paths.
try:
    print(unicodedata.name("\x00"))
except ValueError as exc:
    print("ValueError:", exc)
try:
    print(unicodedata.lookup("NOT A NAME"))
except KeyError as exc:
    print("KeyError:", exc)
try:
    print(unicodedata.decimal("a"))
except ValueError as exc:
    print("ValueError:", exc)
try:
    print(unicodedata.numeric("a"))
except ValueError as exc:
    print("ValueError:", exc)
