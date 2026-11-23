# SPDX-License-Identifier: Apache-2.0
"""D88: text I/O is strict UTF-8 at every OS boundary (issue #478).

A text file with a bad UTF-8 line raises UnicodeDecodeError on both sides.
CPython decodes a buffered chunk, so the error rises at the first read and
the good line before the bad one is never yielded; pyscriptc decodes line by
line, so the good line prints first. The argv, environment and directory
name cases of the entry need raw bytes the probe cannot supply and are
covered in tests/test_utf8_io.py.
"""

with open("d88.txt", "wb") as raw:
    raw.write(b"ok\n\xc3(\n")
try:
    with open("d88.txt") as fh:
        for line in fh:
            print(repr(line))
except UnicodeDecodeError:
    print("UnicodeDecodeError")
