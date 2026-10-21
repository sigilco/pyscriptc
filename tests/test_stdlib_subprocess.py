"""subprocess: the native run, call and check_call subset.

`subprocess.run(cmd, capture_output=True)` returns the CompletedProcess
record with `returncode` and the captured stdout bytes, `subprocess.call`
returns the exit code, and `subprocess.check_call` raises RuntimeError on a
non-zero code. The command is a `list[str]`, the shape scripts use. Each
probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_subprocess_run_captures_stdout(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "run.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['echo', 'hello'], capture_output=True)",
                "    print(r.returncode)",
                "    print(r.stdout)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_without_capture(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "nocapture.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['true'])",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_call_return_codes(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "call.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    print(subprocess.call(['true']))",
                "    print(subprocess.call(['false']))",
                "    print(subprocess.check_call(['true']))",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_check_call_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "checkfail.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    subprocess.check_call(['false'])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "non-zero exit code" in got.stderr, got.stderr
