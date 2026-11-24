# SPDX-License-Identifier: Apache-2.0
"""`subprocess.Popen` spawn environment: `env=`, `cwd=`, `start_new_session=` and `text=` (issue #1344).

The shapes a job-spawning daemon writes: a per-job environment block that
replaces the child's whole environment, a working directory, a child in
its own session so the daemon's death does not signal it, stdin and
stdout piped, and the text-mode pipes. A spawn that cannot start the
program raises in the parent with CPython's exception class and message.
The probe runs the same program on both sides and the lane byte-compares
stdout, stderr and the exit code.

The child's session id and pid vary per run, so the probe prints whether
they are equal (a session leader's id is its pid), never the numbers.
"""

import subprocess

# env= is the whole environment: the parent's HOME is gone, the map's
# entries arrive in the child.
env: dict[str, str] = {"PATH": "/usr/bin:/bin", "JOB_ID": "j-17", "NOTE": "two words"}
a = subprocess.Popen(
    ["sh", "-c", 'echo "$JOB_ID|$NOTE|${HOME:-unset}"'],
    env=env,
    stdout=subprocess.PIPE,
)
print("env", a.stdout.readline())
print("env-rc", a.wait())

# An empty env clears everything, PATH included: the child runs by an
# absolute path.
empty: dict[str, str] = {}
b = subprocess.Popen(["/bin/sh", "-c", 'echo "[${HOME:-none}${JOB_ID:-none}]"'], env=empty, stdout=subprocess.PIPE)
print("empty-env", b.stdout.readline())
print("empty-env-rc", b.wait())

# cwd= as a str, with the child reporting where it runs.
c = subprocess.Popen(["pwd"], cwd="/usr", stdout=subprocess.PIPE)
print("cwd", c.stdout.readline())
print("cwd-rc", c.wait())

# start_new_session=True: the child is a session leader, so its session
# id is its own pid; an ordinary child shares the parent's session.
d = subprocess.Popen(
    ["sh", "-c", "echo $$; ps -o pgid= -p $$"],
    start_new_session=True,
    stdout=subprocess.PIPE,
)
fields = d.stdout.read().decode().split()
print("new-session-leader", fields[0] == fields[1])
print("new-session-rc", d.wait())

# The consumer's shape: env, cwd, a new session, stdin and stdout piped,
# the child answers a line.
job = subprocess.Popen(
    ["sh", "-c", 'read line; echo "$JOB:$line:$(pwd)"'],
    env={"PATH": "/usr/bin:/bin", "JOB": "j2"},
    cwd="/usr",
    start_new_session=True,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
job.stdin.write(b"hello\n")
job.stdin.flush()
print("job", job.stdout.readline())
job.stdin.close()
print("job-rc", job.wait())

# Text mode: str in and out, universal newlines on the way back, the
# write answers the character count.
t = subprocess.Popen(
    ["sh", "-c", 'read a; printf "got:%s\\r\\n" "$a"; echo second'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)
print("text-write", t.stdin.write("héllo\n"))
t.stdin.flush()
print("text-line", repr(t.stdout.readline()))
for line in t.stdout:
    print("text-iter", repr(line))
t.stdin.close()
print("text-rc", t.wait())

# A spawn that cannot start raises in the parent, not an exit code.
try:
    subprocess.Popen(["pysc-no-such-program-xyz"])
except FileNotFoundError as e:
    print("missing-program", e)
try:
    subprocess.Popen(["true"], cwd="/pysc/no/such/dir")
except FileNotFoundError as e:
    print("missing-cwd", e)
