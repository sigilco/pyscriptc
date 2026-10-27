"""Broad sanitizer suites: the whole test tree compiled under ASan+UBSan.

Nightly-only expansion of the PR-CI smoke (issue #588, test strategy 3.6):
instead of one hello-world binary per sanitizer, broad runtime suites run as
sanitized binaries. ASan+UBSan covers `ps_seq`, `ps_map` and `ps_grow`
(buffer overruns, unaligned reads, signed overflow) through the list, dict
and set surfaces; the thread suites run under TSan in the companion
`test_threading_tsan.py`, which this file drives as a suite instead of job
by job.

The tests are marked `linux_container`, so PR CI never runs them - PR CI
keeps the existing `sanitize-smoke` and `tsan-smoke` jobs and its wall time
does not change. The nightly sanitizer job runs exactly this module.

This module never runs sanitizers itself: the sanitizer runtime must own
the process, so a sanitized binary cannot run inside an already-instrumented
pytest. What it pins is the packaging: the flag set, the option pins and the
per-suite compile+run+clean receipts the nightly performs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import write_case

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

_ASAN_ENV = {
    **os.environ,
    # detect_leaks=0: the runtime has no GC and never frees by design
    # (ps_rt.c, "the exit registry" comment): every string buffer
    # ps_buf_copy/ps_grow_add/ps_str_join hands out lives until the process
    # dies, and only sequences and maps are anchored for LSan. The strategy
    # bug classes are overruns, unaligned reads and UB, which halt_on_error
    # still catches; LeakSanitizer would only report deliberate exit-time
    # retention.
    "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1",
    "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
}

# One source per runtime surface the strategy names. Each is compiled once
# per sanitizer list and must run silent: no sanitizer report on either
# stream and exit code 0.
_SEQ_SOURCE = """
def checklist(n: int) -> int:
    xs: list[int] = []
    i: int = 0
    while i < n:
        xs.append(i * i)
        i = i + 1
    total: int = 0
    for x in xs:
        total = total + x
    if len(xs) != n:
        return 1
    if xs[0] != 0 or xs[n - 1] != (n - 1) * (n - 1):
        return 2
    return total % 256
"""

_MAP_SOURCE = """
def checklist(n: int) -> int:
    d: dict[str, int] = {}
    i: int = 0
    while i < n:
        d["k" + str(i)] = i
        i = i + 1
    total: int = 0
    for k in d:
        total = total + d[k]
    if len(d) != n:
        return 1
    return total % 256
"""

_SET_SOURCE = """
def checklist(n: int) -> int:
    seen: set[int] = set()
    i: int = 0
    while i < n:
        seen.add(i % 97)
        i = i + 1
    total: int = 0
    for v in seen:
        total = total + v
    if len(seen) != 97:
        return 1
    return total % 256
"""

_STR_SOURCE = """
def checklist(n: int) -> int:
    parts: list[str] = []
    i: int = 0
    while i < n:
        parts.append("item" + str(i))
        i = i + 1
    joined: str = ", ".join(parts)
    if len(joined) == 0:
        return 1
    if joined.upper().lower() != joined:
        return 2
    return len(joined) % 256
"""

SUITES: list[tuple[str, str, str, str]] = [
    # (name, source, called function, expected exact stdout of `--call`)
    ("seq-grow", _SEQ_SOURCE, "checklist", "118"),
    ("map-buckets", _MAP_SOURCE, "checklist", "78"),
    ("set-buckets", _SET_SOURCE, "checklist", "48"),
    ("str-buffers", _STR_SOURCE, "checklist", "36"),
]


def compile_sanitized(tmp_path: Path, source: Path, binary: Path, sanitize: str) -> None:
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "-o",
            str(binary),
            "--call",
            "checklist",
            "--args",
            "[500]",
            f"--sanitize={sanitize}",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr


def run_silent(binary: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary)], cwd=binary.parent, env=env, capture_output=True, text=True)


def assert_clean(proc: subprocess.CompletedProcess[str], expected: str) -> None:
    """Exit 0, exact stdout, no sanitizer report on stderr."""
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected, (proc.stdout, expected)
    assert "AddressSanitizer" not in proc.stderr
    assert "runtime error" not in proc.stderr
    assert proc.stderr == "", proc.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
@pytest.mark.parametrize(
    "name,source,fn,expected",
    SUITES,
    ids=[suite[0] for suite in SUITES],
)
def test_asan_ubsan_runtime_suite_is_clean(tmp_path: Path, name: str, source: str, fn: str, expected: str) -> None:
    """The ASan+UBSan build of each runtime surface runs silent and exact."""
    path = write_case(tmp_path, f"{name}.py", source)
    binary = tmp_path / name
    compile_sanitized(tmp_path, path, binary, "address,undefined")
    assert_clean(run_silent(binary, _ASAN_ENV), expected)


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_tsan_thread_suites_pass(tmp_path: Path) -> None:
    """The TSan suite runs as a whole, not just the smoke pair of PR CI."""
    tsan = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_threading_tsan.py"],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert tsan.returncode == 0, tsan.stdout + tsan.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_sanitized_build_rebuilds_the_runtime(tmp_path: Path) -> None:
    """A sanitized build never links a plain cached runtime object."""
    source = write_case(tmp_path, "grow.py", _SEQ_SOURCE)

    plain_env = {**ENV, "PYSC_RT_CACHE_DIR": str(tmp_path / "cache-plain")}
    san_env = {**ENV, "PYSC_RT_CACHE_DIR": str(tmp_path / "cache-san")}
    for env, sanitize in ((plain_env, None), (san_env, "address,undefined")):
        cmd = [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / ("out" if sanitize is None else "out-san")),
            "--call",
            "checklist",
            "--args",
            "[64]",
        ]
        if sanitize:
            cmd.append(f"--sanitize={sanitize}")
        got = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        assert got.returncode == 0, got.stderr
    plain_cache = list((tmp_path / "cache-plain").glob("*.o"))
    san_cache = list((tmp_path / "cache-san").glob("*.o"))
    assert plain_cache, "plain build cached no runtime objects"
    assert san_cache, "sanitized build cached no runtime objects"
    assert {p.name for p in plain_cache}.isdisjoint({p.name for p in san_cache})
