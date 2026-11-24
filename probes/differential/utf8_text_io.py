# SPDX-License-Identifier: Apache-2.0
"""UTF-8 text I/O: files, `print`, the std streams and `encoding=` (issue #478).

The text I/O default is UTF-8 (PEP 540, PEP 686), whatever the host locale
says. A file written without an `encoding=` is the UTF-8 bytes of the str
(a code point above U+FFFF is four bytes) and reads back to the same str.
`print` and `sys.stdout.write` emit those bytes and `write` returns the
number of code points, not bytes. `encoding=` is honored for utf-8, ascii
and latin-1: ascii and latin-1 encode and decode one byte per code point
and refuse a code point out of range with UnicodeEncodeError, a byte out
of range with UnicodeDecodeError. A text read of bytes that are not valid
UTF-8 raises UnicodeDecodeError instead of replacing them. Both sides run
this same program and the lane byte-compares stdout, stderr and the exit
code.
"""

import os
import sys

PAYLOAD: str = "café naïve 日本 \U0001f600\nline two ü\n"
wrote: int = 0

with open("utf8_probe.txt", "w") as out:
    wrote = out.write(PAYLOAD)
print(wrote, len(PAYLOAD))

with open("utf8_probe.txt", "rb") as raw:
    print(raw.read())

with open("utf8_probe.txt") as fh:
    text: str = fh.read()
print(text == PAYLOAD, len(text))
print(text)

with open("utf8_probe.txt", encoding="utf-8") as fh:
    for line in fh:
        print(repr(line), len(line))

with open("utf8_probe.txt") as fh:
    print(repr(fh.read(3)), repr(fh.read(5)), repr(fh.readline()))

with open("utf8_probe.txt", "a") as out:
    out.write("tail \U0001f600\n")
with open("utf8_probe.txt", "rb") as raw:
    data: bytes = raw.read()
print(len(data), data.endswith(b"tail \xf0\x9f\x98\x80\n"))

print("café")
print("\U0001f600", len("\U0001f600"))
print("é", len("é"))
count: int = sys.stdout.write("w é\U0001f600\n")
print(count)
sys.stderr.write("err é\U0001f600\n")

with open("utf8_probe.txt", "wb") as raw:
    raw.write(b"caf\xe9 \xff\n")
with open("utf8_probe.txt", encoding="latin-1") as fh:
    latin: str = fh.read()
print(repr(latin), len(latin))

with open("utf8_probe.txt", "w", encoding="latin-1") as out:
    out.write("é¿\n")
with open("utf8_probe.txt", "rb") as raw:
    print(raw.read())

with open("utf8_probe.txt", "w", encoding="ascii") as out:
    out.write("plain\n")
with open("utf8_probe.txt", encoding="ASCII") as fh:
    print(repr(fh.read()))

try:
    with open("utf8_probe.txt", "w", encoding="ascii") as out:
        out.write("é")
except UnicodeEncodeError:
    print("ascii encode error")

try:
    with open("utf8_probe.txt", "w", encoding="latin-1") as out:
        out.write("\U0001f600")
except UnicodeEncodeError:
    print("latin-1 encode error")

with open("utf8_probe.txt", "wb") as raw:
    raw.write(b"ab\xff\xfe")
try:
    with open("utf8_probe.txt") as fh:
        print(fh.read())
except UnicodeDecodeError:
    print("utf-8 decode error")
try:
    with open("utf8_probe.txt", encoding="ascii") as fh:
        print(fh.read())
except UnicodeDecodeError:
    print("ascii decode error")

os.remove("utf8_probe.txt")
