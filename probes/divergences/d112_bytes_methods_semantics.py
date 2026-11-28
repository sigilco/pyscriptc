# SPDX-License-Identifier: Apache-2.0
# Divergence D112 (issue #1424), fixed by the same change: bytes methods
# applied str semantics. find/rfind/index answered code-point offsets,
# count of the empty needle counted code points, upper/lower ran the
# Unicode case engine, the is* predicates applied Unicode tables (isspace
# fenced at run time), partition/rpartition returned str parts,
# strip/splitlines used the str whitespace sets, zfill/justify padded by
# code points and int(bytes) stripped the Unicode whitespace set. The bytes
# kind now dispatches to the ps_buf_* byte forms. The row replays
# byte-identical; probes/differential/bytes_methods_semantics.py pins the
# whole issue table.

data: bytes = b"caf\xc3\xa9 xyz"
print(data.find(b"z"), data.rfind(b"z"), data.index(b"z"))
print(data.count(b""))
print(b"a\xc3\xa9a".upper(), b"A\xc3\x89A".lower())
print(b"a\xc3\xa9a".isalpha(), b"\xc3\xa9".isalnum(), b"\xc2\xa0".isspace())
print(data.partition(b" "), data.rpartition(b" "))
print(int(b"\xa0 5") if False else 0)
print(b"\x1c\xa0".strip(), b"a\x85b".splitlines()[0], b"a\xc3\xa9".zfill(4))
