# SPDX-License-Identifier: Apache-2.0
"""`subprocess.Popen`: the synchronous child handle (issue #1345).

The handle spawns the child without waiting, so the caller drives it:
`poll()` reaps without blocking, `wait(timeout=)` bounds the blocking
wait and raises `subprocess.TimeoutExpired` when the bound passes, and
`terminate()`/`kill()` signal the child. The piped streams read and
write incrementally, the shape an interactive protocol over the child's
stdio needs (`communicate()` would drain both pipes to EOF and block
until the child exits). The probe runs the same program on both sides
and the lane byte-compares stdout, stderr and the exit code.

The shapes here are the ones a caller writes: the spawn and the exit
code, the non-blocking poll of a live child, the bounded wait that
expires (the child stays alive, the caller kills it), the terminate of
a sleeping child, the line-by-line read of a piped stdout, the write
into a piped stdin and the read of the answer, and the stderr pipe
alongside the stdout one.

The pid is not printed: it differs on every run. Its shape is checked
instead (the pid is a positive number), which is the property a caller
depends on.
"""

import subprocess

# The spawn and the exit code: the handle answers without waiting and
# the wait reads the code.
done = subprocess.Popen(["sh", "-c", "exit 3"])
print("pid-positive", done.pid > 0)
print("rc", done.wait())
print("rc-again", done.wait())

# The non-blocking poll of a live child: the child is still running, so
# the poll answers None, and the returncode reads None too.
live = subprocess.Popen(["sh", "-c", "sleep 5"])
print("poll-live", live.poll() is None)
print("rc-live", live.returncode is None)
live.kill()
print("poll-killed", live.wait())
print("rc-killed", str(live.returncode))

# The bounded wait that expires: the child outlives the bound, so the
# wait raises TimeoutExpired and the child stays alive. The caller
# kills it afterwards, and the wait reads the killed code.
slow = subprocess.Popen(["sh", "-c", "sleep 5"])
try:
    slow.wait(timeout=0.3)
    print("no timeout")
except subprocess.TimeoutExpired:
    print("expired")
slow.kill()
print("slow-killed", slow.wait(timeout=5))

# The terminate of a sleeping child: SIGTERM, so the code is the
# negated signal number.
term = subprocess.Popen(["sh", "-c", "sleep 5"])
term.terminate()
print("terminated", term.wait(timeout=5))

# The line-by-line read of a piped stdout: each line keeps its
# trailing newline, the way CPython's file iteration does.
lines = subprocess.Popen(["sh", "-c", "echo one; echo two; echo three"], stdout=subprocess.PIPE)
for line in lines.stdout:
    print("line", line)
print("lines-rc", lines.wait())

# The write into a piped stdin and the read of the answer: the child
# echoes what it read.
echo = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
echo.stdin.write(b"ping\n")
echo.stdin.flush()
print("echo", echo.stdout.readline())
echo.stdin.close()
print("echo-rest", echo.stdout.read())
print("echo-rc", echo.wait())

# The stderr pipe alongside the stdout one: the two streams are read
# separately.
both = subprocess.Popen(["sh", "-c", "echo to-out; echo to-err >&2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print("both-out", both.stdout.readline())
print("both-err", both.stderr.readline())
print("both-rc", both.wait())
