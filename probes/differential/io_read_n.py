# SPDX-License-Identifier: Apache-2.0
"""`read(n)` over a file handle and a memory handle (issue #1227).

The compiled runtime reads at most `n` items from the current position: a
text handle counts code points (the bytes of the last one included), a
binary handle counts bytes, a memory handle (BytesIO, StringIO) counts
its stored bytes. The read stops short at EOF, returns `b""` / `""`
there, and the position ends where the read ended so a later `read`,
`read(n)` or `readline` continues after it. Text mode applies the
universal-newline translation inside the budget, the way CPython counts
the translated characters. Both sides run this same program and the lane
byte-compares stdout.
"""

import io
import os

CONTENT: str = "héllo wörld\nline2\r\nline3\rlast"

with open("read_n_probe.txt", "w") as out:
    out.write(CONTENT)

with open("read_n_probe.txt") as fh:
    print(repr(fh.read(3)))
    print(repr(fh.read(5)))
    print(repr(fh.readline()))
    print(repr(fh.read(4)))
    print(repr(fh.read(999)))
    print(repr(fh.read(1)))

with open("read_n_probe.txt", "rb") as fh:
    print(repr(fh.read(6)))
    print(repr(fh.readline()))
    print(repr(fh.read(2)))
    print(repr(fh.read()))
    print(repr(fh.read(8)))

with open("read_n_probe.txt") as fh:
    print(repr(fh.read(0)))
    print(repr(fh.read()))

with open("read_n_probe.bin", "wb") as out:
    out.write(b"\x00\x01\x02\xff\xfe")

with open("read_n_probe.bin", "rb") as fh:
    print(repr(fh.read(3)))
    print(repr(fh.read(2)))
    print(repr(fh.read(2)))

buf: io.BytesIO = io.BytesIO(b"abcdef")
print(repr(buf.read(2)))
print(repr(buf.read(10)))
print(repr(buf.read(1)))

mem: io.StringIO = io.StringIO("abcdef")
print(repr(mem.read(2)))
print(repr(mem.read(10)))
print(repr(mem.read(1)))

os.unlink("read_n_probe.txt")
os.unlink("read_n_probe.bin")
