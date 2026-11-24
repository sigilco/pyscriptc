# SPDX-License-Identifier: Apache-2.0
"""UTF-8 stdin, `input()`, `sys.argv` and `os.environ` (issue #478).

`input()` and `sys.stdin.readline` read UTF-8 bytes and decode them to str:
the line keeps its code points (a code point above U+FFFF is one), `input`
drops the trailing newline, `readline` keeps it, and a lone `\\r` is an
ordinary character on POSIX. `sys.argv` and `os.environ` decode the OS
bytes as UTF-8. The lane feeds a non-ASCII payload through stdin, one
non-ASCII argument and `PYSC_PROBE_ENV`; with none supplied the program
prints the empty forms and both sides still agree. Both sides run this
same program and the lane byte-compares stdout, stderr and the exit code.
"""

import os
import sys

first: str = input()
print(repr(first), len(first))
second: str = sys.stdin.readline()
print(repr(second), len(second))
print(first.upper(), second.strip().title())

args: list[str] = sys.argv[1:]
for arg in args:
    print(repr(arg), len(arg), arg.encode("utf-8"))

value: str = os.environ.get("PYSC_PROBE_ENV", "unset")
print(repr(value), len(value))
