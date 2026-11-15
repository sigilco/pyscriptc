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


def test_os_pipe_returns_the_read_write_pair(tmp_path: Path) -> None:
    """`os.pipe()` returns (read, write) in CPython's order (#1245).

    The fd numbers differ between the two runs, so the probe prints
    booleans about the pair and the bytes that travel through it.
    """
    _oracle(
        tmp_path,
        "pipe.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    r, w = os.pipe()",
                "    print(r > 0)",
                "    print(w > 0)",
                "    print(r != w)",
                "    print(os.write(w, b'pipe bytes') == 10)",
                "    print(os.read(r, 10) == b'pipe bytes')",
                "    os.close(r)",
                "    os.close(w)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_dup_and_dup2_copy_the_descriptor(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "dup.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    r, w = os.pipe()",
                "    d: int = os.dup(w)",
                "    print(d != w)",
                "    print(os.write(d, b'dup bytes') == 9)",
                "    print(os.read(r, 9) == b'dup bytes')",
                "    os.close(d)",
                "    os.close(r)",
                "    os.close(w)",
                "    r, w = os.pipe()",
                "    d2: int = os.dup(w)",
                "    os.close(d2)",
                "    print(os.dup2(w, d2) == d2)",
                "    print(os.write(d2, b'dup2 bytes') == 10)",
                "    print(os.read(r, 10) == b'dup2 bytes')",
                "    os.close(d2)",
                "    os.close(r)",
                "    os.close(w)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_pipe_argument_shapes_fence() -> None:
    from pyscriptc.lower import lower_source

    for source, message in (
        ("import os\nos.pipe(1)\n", "os.pipe lowers the 0-argument form only"),
        ("import os\nos.dup()\n", "os.dup expects 1 argument(s)"),
        ("import os\nos.dup2(1)\n", "os.dup2 expects 2 argument(s)"),
        ("import os\nos.dup('x')\n", "os.dup fd is int"),
    ):
        mod = lower_source(source)
        assert mod["fences"], f"{source!r} must fence"
        assert message in mod["fences"][0]["message"]
