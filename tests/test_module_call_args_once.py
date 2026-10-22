"""Module-call arguments are evaluated exactly once (issue #183)."""

from __future__ import annotations

from pathlib import Path

from tests.test_e1_del import oracle_script
from tests.test_gates import run_cli, write_case

SCRIPT = """import sys


def main() -> int:
    print("hello")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def test_sys_exit_main_runs_main_once(tmp_path: Path) -> None:
    path = write_case(tmp_path, "exit_once.py", SCRIPT)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "exit_once"), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path) == "hello\n"
