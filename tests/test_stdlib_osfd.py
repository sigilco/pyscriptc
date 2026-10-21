"""os file descriptors: open, close, read, write, fstat, lstat, utime.

The fd family is the low-level spelling scripts use when `open()` is not
enough: `os.open` with the `O_*` flags, `os.write` and `os.read` over the
descriptor, `os.close` to release it, `os.fstat` and `os.lstat` for the
metadata, and `os.utime` to touch a path. Each probe diffs stdout against
the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_os_open_write_read_close(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "openwrite.py",
        "\n".join(
            [
                "import os",
                "import tempfile",
                "",
                "def run() -> int:",
                "    root: str = tempfile.mkdtemp()",
                "    path: str = root + '/data.txt'",
                "    fd: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)",
                "    print(fd > 0)",
                "    print(os.write(fd, b'hello fd') == 8)",
                "    os.close(fd)",
                "    reader: int = os.open(path, os.O_RDONLY)",
                "    print(os.read(reader, 5) == b'hello')",
                "    print(os.read(reader, 3) == b' fd')",
                "    os.close(reader)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_open_readonly_missing_file_fails(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "openmissing.py",
        "\n".join(
            [
                "import os",
                "import tempfile",
                "",
                "def run() -> int:",
                "    path: str = tempfile.mkdtemp() + '/absent'",
                "    os.open(path, os.O_RDONLY)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "open failed" in got.stderr


def test_os_fstat_and_lstat(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "statfd.py",
        "\n".join(
            [
                "import os",
                "import tempfile",
                "",
                "def run() -> int:",
                "    path: str = tempfile.mkdtemp() + '/f.txt'",
                "    fd: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)",
                "    os.write(fd, b'1234567890')",
                "    os.close(fd)",
                "    info: os.stat_result = os.fstat(os.open(path, os.O_RDONLY))",
                "    print(info.st_size)",
                "    print(info.st_mode > 0)",
                "    print(os.stat(path).st_size)",
                "    other: os.stat_result = os.lstat(path)",
                "    print(other.st_size)",
                "    print(other.st_mode > 0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_utime_sets_the_mtime(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "utime.py",
        "\n".join(
            [
                "import os",
                "import tempfile",
                "import time",
                "",
                "def run() -> int:",
                "    path: str = tempfile.mkdtemp() + '/t.txt'",
                "    fd: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)",
                "    os.close(fd)",
                "    before: float = os.stat(path).st_mtime",
                "    time.sleep(0.01)",
                "    os.utime(path)",
                "    after: float = os.stat(path).st_mtime",
                "    print(after >= before)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_read_needs_a_size(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_source

    mod = lower_source("import os\nos.read(1)\n")
    assert mod["fences"], "os.read without a size must fence"
    assert "os.read expects 2 argument(s)" in mod["fences"][0]["message"]
