"""tempfile: `mkstemp` lowers to the native (fd, path) pair.

`tempfile.mkstemp()` creates a file with `O_CREAT|O_EXCL` in the system
temp directory and returns the file descriptor and the path. The path
differs between the two runs (the CPython oracle and the compiled program
each create their own file), so the probes print booleans instead of the
path. The fd is closed and the file unlinked by the program itself, like
CPython's contract (the caller owns both).
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_tempfile_mkstemp_pair(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "mkstemp.py",
        "\n".join(
            [
                "import os",
                "import tempfile",
                "",
                "def run() -> int:",
                "    fd, path = tempfile.mkstemp()",
                "    print(fd > 0)",
                "    print(os.path.isabs(path))",
                "    print(os.path.exists(path))",
                "    os.write(fd, b'payload')",
                "    os.close(fd)",
                "    with open(path, 'rb') as handle:",
                "        print(handle.read() == b'payload')",
                "    os.unlink(path)",
                "    print(os.path.exists(path))",
                "    return 0",
                "",
            ]
        ),
    )


def test_tempfile_mkstemp_fences_arguments(tmp_path: Path) -> None:
    # CPython's suffix/prefix/dir/text arguments are not lowered: the file
    # always lands in the system temp directory, like the lowered mkdtemp.
    from pyscriptc.diagnostics import DIAGNOSTICS

    source = "\n".join(
        [
            "import tempfile",
            "",
            "def run() -> int:",
            "    fd, path = tempfile.mkstemp(suffix='.txt')",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "mkstemp_args.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr
    assert "PS2003" in DIAGNOSTICS
