# SPDX-License-Identifier: Apache-2.0
# Divergence D111 (issue #1416), fixed by the same change: a bytes slice or
# index applied code-point bounds over non-ASCII bytes. b"caf\xc3\xa9
# xyz"[2:4] came back as the 3-byte split of the UTF-8 pair where CPython
# returns b'f\xc3'. The bytes kind now dispatches to ps_buf_slice, so len,
# indexing, stepped slices and negative bounds count bytes. The row replays
# byte-identical; probes/differential/bytes_slice_nonascii.py pins the seam.

data: bytes = b"caf\xc3\xa9 xyz"
part = data[2:4]
print(len(part), part[0], part[1])
print(part)
print(data[3], data[-1], len(data[2:8:2]), data[::-1][0])
