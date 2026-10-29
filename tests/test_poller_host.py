"""The ps_poller and ps_select host harness (#177, #485): the poller and the
selector compile with the runtime and their printed lines are the assertion
surface, the way the sqlite adapter harness links
(`tests/test_ffi_sqlite_adapter.py`). The harness itself is
`tests/poller_host_main.c`; it exercises the fd wait, the timer-shaped
kernel sleep, the self-pipe wake from a worker thread and the selector's
register/select/modify/unregister shapes."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"
HARNESS = ROOT / "tests" / "poller_host_main.c"

# The harness prints the facts; the bounds below are the issue's numbers.
# The waited times are wall clock, so they are bounded, not exact.
EXPECTED = [
    # Exactly the pipe the writer touched wakes, well inside the 100 ms cap;
    # the other row stays parked.
    (re.compile(r"^poll1 ready=1 a=1 b=0 waited=(\d+)ms$"), lambda ms: 30 <= ms <= 150),
    # Readiness is level-triggered: after the caller drains pipe a, pipe b's
    # writer wakes b alone.
    (re.compile(r"^poll2 ready=1 a=0 b=1 waited=(\d+)ms$"), lambda ms: 30 <= ms <= 150),
    # 100 waits of 10 ms through the poller: the kernel sleeps, no spin.
    (re.compile(r"^ten_k polls=100 elapsed=(\d+)ms$"), lambda ms: ms < 2500),
    # A wait with no rows and a 10 s cap returns as soon as a worker thread
    # writes the self-pipe byte.
    (re.compile(r"^self_pipe waited=(\d+)ms$"), lambda ms: ms < 5000),
    # The selector's keys carry the fd and the event bits.
    (re.compile(r"^keys fd=(\d+) events=(\d+) fd=(\d+) events=(\d+)$"), None),
    # get_key finds the registered fd.
    (re.compile(r"^get_key hit=1$"), None),
    # A poll with a 0 timeout finds nothing.
    (re.compile(r"^select0 empty=1$"), None),
    # The blocked select returns exactly the woken key.
    (re.compile(r"^select1 n=1 a=1 waited=(\d+)ms$"), lambda ms: 30 <= ms <= 500),
    # modify keeps the key identity.
    (re.compile(r"^modify same=1$"), None),
    # unregister drops the key.
    (re.compile(r"^unregister gone=1$"), None),
    # The remaining fd is not ready: the select returns empty inside its
    # 100 ms timeout.
    (re.compile(r"^select2 n=0 waited=(\d+)ms$"), lambda ms: ms < 500),
]


def test_poller_host_harness(tmp_path: Path) -> None:
    if os.uname().sysname == "Windows":
        pytest.skip("no poller on windows")
    cc = os.environ.get("CC", "cc")
    strict = ["-O1", "-Wall", "-Wextra", "-Werror", f"-I{RUNTIME}"]
    objects: list[str] = []
    for index, src in enumerate([HARNESS, *sorted(RUNTIME.glob("*.c"))]):
        if src.name == "ps_island.c":
            continue
        obj = tmp_path / f"{index}_{src.stem}.o"
        # The harness is strict; the runtime C bodies are not (the sqlite
        # harness compiles them `-w` for the same reason).
        flags = strict if src == HARNESS else ["-O1", "-w", f"-I{RUNTIME}"]
        done = subprocess.run([cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{src.name}: {done.stderr}"
        objects.append(str(obj))
    binary = tmp_path / "host_poller"
    link = subprocess.run(
        [cc, *objects, "-lm", "-lpthread", "-o", str(binary)], capture_output=True, text=True, check=False
    )
    assert link.returncode == 0, link.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, check=False, timeout=60)
    assert ran.returncode == 0, ran.stderr
    lines = ran.stdout.splitlines()
    assert len(lines) == len(EXPECTED), (lines, [p.pattern for p, _ in EXPECTED])
    for line, (pattern, bound) in zip(lines, EXPECTED, strict=True):
        match = pattern.match(line)
        assert match is not None, (line, pattern.pattern)
        if bound is None:
            continue
        assert bound(int(match.group(1))), line
