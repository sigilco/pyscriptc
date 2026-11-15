# SPDX-License-Identifier: Apache-2.0
"""`sys.stdin.readline` and the `sys.stdout.flush` / `sys.stderr.flush`
no-ops over the lowered sys surface (issue #1225).

The probe feeds a multi-line payload through stdin (a pipe is the shape an
MCP-style stdio server runs on), reads the lines with `sys.stdin.readline`
and writes with `sys.stdout.write` + `sys.stdout.flush`. The compiled
runtime reads a line the way CPython does: the trailing newline stays in
the returned string, a read at EOF returns the empty string, and the
flushes push the buffered bytes out without changing the byte stream.
"""

import sys

line1: str = sys.stdin.readline()
sys.stdout.write("first: " + line1)
sys.stdout.flush()
line2: str = sys.stdin.readline()
sys.stdout.write("second: " + line2)
sys.stdout.flush()
line3: str = sys.stdin.readline()
sys.stdout.write("third: " + line3)
sys.stdout.flush()
# A read at EOF returns the empty string, like CPython.
line4: str = sys.stdin.readline()
sys.stdout.write("eof: [" + line4 + "]")
sys.stdout.write("\n")
sys.stdout.flush()
sys.stderr.write("stderr line\n")
sys.stderr.flush()
