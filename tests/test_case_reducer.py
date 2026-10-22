"""`pyscriptc reduce`: the delta-debugging reducer."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from pyscriptc.reduce import CommandPredicate, CrashPredicate, FencePredicate, reduce_file, reduce_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

# An `Any`-typed parameter fences with PS1104 on use; dropping it stops the fence.
FENCED = """import math
from typing import Any


def helper(x: int) -> int:
    return x * 2


def unused(y: int) -> int:
    return y + 1


def broken(z: Any) -> int:
    return 1


def main() -> int:
    return helper(2)
"""

# A class instance used as a decorator crashes lowering (KeyError in _calls).
CRASHING = """import os


class A:
    def __call__(self) -> int:
        return 1


@A()
def beta(y: int) -> int:
    return y


def gamma(z: int) -> int:
    return z * 3


def main() -> int:
    return gamma(2)
"""


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )


def test_fence_reduction_drops_unrelated_code(tmp_path: Path) -> None:
    path = tmp_path / "fenced.py"
    path.write_text(FENCED, encoding="utf-8")
    result = reduce_file(path, FencePredicate("PS1104"))
    assert result.verdict.holds
    assert result.changed
    tree = ast.parse(result.source)
    names = {stmt.name for stmt in tree.body if isinstance(stmt, ast.FunctionDef)}
    assert names == {"broken"}
    assert (tmp_path / "fenced.min.py").read_text(encoding="utf-8") == result.source


def test_input_file_is_never_modified(tmp_path: Path) -> None:
    path = tmp_path / "fenced.py"
    path.write_text(FENCED, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    reduce_file(path, FencePredicate("PS1104"))
    assert path.read_text(encoding="utf-8") == before


def test_crash_reduction_keeps_the_failing_construct(tmp_path: Path) -> None:
    path = tmp_path / "crash.py"
    path.write_text(CRASHING, encoding="utf-8")
    result = reduce_file(path, CrashPredicate())
    assert result.verdict.holds
    assert "KeyError" in result.verdict.reason
    tree = ast.parse(result.source)
    functions = [stmt for stmt in tree.body if isinstance(stmt, ast.FunctionDef)]
    assert [fn.name for fn in functions] == ["beta"]


def test_command_predicate_receives_the_candidate_path(tmp_path: Path) -> None:
    seen = tmp_path / "seen.txt"
    command = 'sh -c \'cat "$PSC_SOURCE" >> "$PSC_SEEN"\''
    old = os.environ.get("PSC_SEEN")
    os.environ["PSC_SEEN"] = str(seen)
    try:
        predicate = CommandPredicate(command, directory=tmp_path)
        result = reduce_source("x = 1\ny = 2\n", predicate)
    finally:
        if old is None:
            os.environ.pop("PSC_SEEN", None)
        else:
            os.environ["PSC_SEEN"] = old
    assert result.verdict.holds
    # the command saw the candidate file, and its text is the candidate source
    assert seen.read_text(encoding="utf-8").startswith("x = 1\ny = 2\n")
    # the candidate file is a temporary, not the input
    assert not list(tmp_path.glob("tmp*.py"))


def test_command_predicate_false_stops_reduction(tmp_path: Path) -> None:
    predicate = CommandPredicate("false", directory=tmp_path)
    result = reduce_source("x = 1\n", predicate)
    assert not result.verdict.holds
    assert result.candidates == 0
    assert result.detail == "the input does not satisfy the predicate; nothing to reduce"


def test_predicate_keeps_only_necessary_lines(tmp_path: Path) -> None:
    source = """import math
import os
import sys


def one() -> int:
    return 1


def marker() -> int:
    print("keep me")
    return 2


def main() -> int:
    return one() + marker()
"""
    predicate = CommandPredicate('grep -q "keep me" "$PSC_SOURCE"', directory=tmp_path)
    result = reduce_source(source, predicate)
    assert result.verdict.holds
    assert result.changed
    assert "keep me" in result.source
    assert "def one" not in result.source
    assert "import math" not in result.source


def test_starting_variant_must_hold() -> None:
    result = reduce_source("x = 1\n", FencePredicate("PS9999"))
    assert not result.verdict.holds
    assert result.candidates == 0
    assert not result.changed


def test_reduced_source_still_parses() -> None:
    result = reduce_source(FENCED, FencePredicate("PS1104"))
    ast.parse(result.source)


def test_step_log_records_kept_and_dropped() -> None:
    result = reduce_source(FENCED, FencePredicate("PS1104"))
    assert result.steps
    kinds = {step.kind for step in result.steps}
    assert kinds <= {"statement", "import-name"}
    payload = result.as_dict()
    assert payload["verdict"] == "holds"
    assert payload["candidates"] == len(result.steps)
    assert all(set(step) == {"step", "kind", "detail", "kept", "reason"} for step in payload["steps"])


def test_an_empty_module_is_never_a_candidate() -> None:
    # `x = 1` alone: dropping it leaves an empty module, which does not parse.
    result = reduce_source("x = 1\n", FencePredicate("PS1104"))
    assert not result.verdict.holds
    assert not result.changed


def test_cli_fence_writes_min_file(tmp_path: Path) -> None:
    path = tmp_path / "fenced.py"
    path.write_text(FENCED, encoding="utf-8")
    proc = run_cli("reduce", str(path), "--fence", "PS1104")
    assert proc.returncode == 0, proc.stderr
    assert "wrote" in proc.stdout
    reduced = (tmp_path / "fenced.min.py").read_text(encoding="utf-8")
    assert "def broken" in reduced
    assert "def helper" not in reduced


def test_cli_crash_json(tmp_path: Path) -> None:
    path = tmp_path / "crash.py"
    path.write_text(CRASHING, encoding="utf-8")
    proc = run_cli("reduce", str(path), "--crash", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "holds"
    assert payload["changed"] is True
    assert "def beta" in payload["reduced"]
    assert not (tmp_path / "crash.min.py").exists()


def test_cli_print_mode(tmp_path: Path) -> None:
    path = tmp_path / "fenced.py"
    path.write_text(FENCED, encoding="utf-8")
    proc = run_cli("reduce", str(path), "--fence", "PS1104", "--print")
    assert proc.returncode == 0, proc.stderr
    assert "def broken" in proc.stdout
    assert not (tmp_path / "fenced.min.py").exists()


def test_cli_requires_a_predicate(tmp_path: Path) -> None:
    path = tmp_path / "x.py"
    path.write_text("x = 1\n", encoding="utf-8")
    proc = run_cli("reduce", str(path))
    assert proc.returncode == 2
    assert "--predicate" in proc.stderr or "one of the arguments" in proc.stderr


def test_cli_predicate_command(tmp_path: Path) -> None:
    path = tmp_path / "prog.py"
    path.write_text("x = 1\nprint('marker')\n", encoding="utf-8")
    proc = run_cli("reduce", str(path), "--predicate", 'grep -q marker "$PSC_SOURCE"')
    assert proc.returncode == 0, proc.stderr
    reduced = (tmp_path / "prog.min.py").read_text(encoding="utf-8")
    assert "marker" in reduced
    assert "x = 1" not in reduced
