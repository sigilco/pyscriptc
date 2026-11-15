# SPDX-License-Identifier: Apache-2.0
"""`subprocess.run(..., timeout=)` bounds a wedged child (issue #1223).

The timeout is the number of seconds the child may take. A child that
outlives the bound is killed and the run raises `subprocess.TimeoutExpired`,
so a program that shells out to a slow tool has a deadline instead of
hanging. The probe runs the same program on both sides and the lane
byte-compares stdout, stderr and the exit code.

The shapes here are the ones a caller writes: a fast child inside the
bound (the record wins), a child that outlives the bound (the timeout
path), the bound with `capture_output=True` (the child is still killed,
the capture is dropped) and `timeout=None` (no bound, CPython's default).
The child is `sleep 5`, long enough that a one-second bound always
expires.
"""

import subprocess

SLEEP: list[str] = ["sleep", "5"]

# A child that finishes inside the bound: the record carries the code.
done: subprocess.CompletedProcess = subprocess.run(["true"], timeout=30, capture_output=True)
print("returncode", done.returncode)

# A child that outlives the bound: the run raises TimeoutExpired.
try:
    subprocess.run(SLEEP, timeout=1)
    print("no timeout")
except subprocess.TimeoutExpired as exc:
    print("timed out", str(exc))

# The bound with the capture on: the same timeout path.
try:
    subprocess.run(SLEEP, timeout=1, capture_output=True)
    print("no timeout")
except subprocess.TimeoutExpired:
    print("timed out with capture")

# `timeout=None` is CPython's default: no bound, the child runs out.
none: subprocess.CompletedProcess = subprocess.run(["true"], timeout=None, capture_output=True)
print("returncode", none.returncode)
