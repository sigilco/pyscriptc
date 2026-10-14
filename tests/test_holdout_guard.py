"""The hook that keeps generator agents out of the holdout."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "holdout_guard.py"
CWD = "/work"


def run(tool: str, env: dict[str, str] | None = None, **tool_input: str) -> int:
    event = json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": CWD})
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=event,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin", **(env or {})},
    )
    return result.returncode


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Read", {"file_path": "/work/benchmarks/compat2/corpus/holdout/pins.json"}),
        ("Read", {"file_path": "benchmarks/compat2/corpus/holdout/scripts/lcs_dp/main.py"}),
        ("Read", {"file_path": "/work/benchmarks/compat2/corpus/../corpus/holdout/pins.json"}),
        ("Edit", {"file_path": "/work/benchmarks/compat2/corpus/holdout/README.md"}),
        ("Grep", {"pattern": "def ", "path": "/work/benchmarks/compat2/corpus/holdout"}),
        ("Glob", {"pattern": "benchmarks/compat2/corpus/holdout/**/*.py"}),
        ("Bash", {"command": "cat benchmarks/compat2/corpus/holdout/pins.json"}),
        ("Bash", {"command": "uv run python -m benchmarks.compat2.score --holdout --out /tmp/x.json"}),
        ("Bash", {"command": "PYSCRIPTC_EVALUATOR=1 true"}),
        ("Bash", {"command": "git cat-file -p HEAD:some/path"}),
        ("Bash", {"command": "grep -r . benchmarks/compat2/corpus/HOLDOUT"}),
    ],
)
def test_blocks_holdout_access(tool: str, args: dict[str, str]) -> None:
    assert run(tool, **args) == 2


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Read", {"file_path": "/work/benchmarks/compat2/corpus/load.py"}),
        ("Read", {"file_path": "/work/benchmarks/compat2/corpus/pins.json"}),
        ("Grep", {"pattern": "probe", "path": "/work/benchmarks/compat2"}),
        ("Bash", {"command": "uv run pytest -q tests/compat2"}),
        ("Bash", {"command": "git log --oneline -5"}),
        ("Edit", {"file_path": "/work/src/pyscriptc/lower.py"}),
    ],
)
def test_allows_everything_else(tool: str, args: dict[str, str]) -> None:
    assert run(tool, **args) == 0


def test_evaluator_session_passes() -> None:
    cmd = "uv run python -m benchmarks.compat2.score --holdout --out /tmp/x.json"
    assert run("Bash", env={"PYSCRIPTC_EVALUATOR": "1"}, command=cmd) == 0


def test_bad_input_does_not_block() -> None:
    result = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert result.returncode == 0
