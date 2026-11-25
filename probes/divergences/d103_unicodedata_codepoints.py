# SPDX-License-Identifier: Apache-2.0
# Divergence D103: the `unicodedata` argument shape (issue #476).
# Before the S4 slice the runtime carried a 256-entry table, so a code point
# above U+00FF had no answer: the category came back as the unassigned
# default, the name raised, and a normalization that would leave Latin-1
# raised a ValueError. CPython answers all of them from its own Unicode
# tables. The probe walks the boundaries the Latin-1 table could not reach:
# a combining mark, a Hangul syllable, a CJK ideograph and an astral
# emoji, plus the normalization that composes them back.
import unicodedata

print(unicodedata.category("́"))
print(unicodedata.name("́"))
print(unicodedata.category("가"))
print(unicodedata.name("가"))
print(unicodedata.category("一"))
print(unicodedata.name("一"))
print(unicodedata.category("\U0001f600"))
print(unicodedata.name("\U0001f600"))
print(repr(unicodedata.normalize("NFC", "가")))
print(repr(unicodedata.normalize("NFD", "가")))
print(unicodedata.lookup("HANGUL SYLLABLE GA"))
print(unicodedata.numeric("¼"))
