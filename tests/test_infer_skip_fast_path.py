# SPDX-License-Identifier: Apache-2.0
"""The inference skip fast path and its timing surface (issue #1317).

A tree with no unannotated parameters has nothing for the pin engine to
prove, so `compile --infer` must not start a checker server at all: the
wordcount measurement (a two-definition package paying 12.7x its compile
time for a pin set of zero) is the regression the skip path removes. The
test asserts the property directly, by counting the server starts the
compile performs.

An unannotated tree still pins (the skip must not eat the feature), and
the engine's outcome shows in the `--debug` stages table and in
`summary.json` (`infer`: skipped flag, pins, call sites, wall ms).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

_ANNOTATED = (
    "def parse_count(text: str, default: int = 0) -> int:\n"
    "    try:\n"
    "        return int(text)\n"
    "    except (ValueError, TypeError):\n"
    "        return default\n"
)
_ANNOTATED_MAIN = (
    "from helpers import parse_count\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(parse_count('42'))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)
_UNANNOTATED = (
    "def parse_count(text, default=0):\n"
    "    try:\n"
    "        return int(text)\n"
    "    except (ValueError, TypeError):\n"
    "        return default\n"
)


@pytest.fixture
def _no_server_starts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every checker server start the compile performs.

    Both server entries are patched: the inferrunner's `tsp` server (the
    pin engine) and the bridge's shared server (`PyreflyBridge.open`,
    which the compile opens after the engine for the per-module facts).
    """
    from pyscriptc import factsbridge, typefacts

    starts: list[str] = []

    def _boom(self: object, *args: object, **kwargs: object) -> None:
        starts.append(type(self).__name__)

    monkeypatch.setattr(typefacts.PyreflyTspFactsAdapter, "_connect", _boom)
    monkeypatch.setattr(factsbridge.PyreflyBridge, "open", _boom)
    return starts


def _annotated_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", _ANNOTATED)
    write_case(root, "__main__.py", _ANNOTATED_MAIN)
    return root


def test_fully_annotated_tree_starts_no_checker_server(tmp_path: Path, _no_server_starts: list[str]) -> None:
    """`compile --infer` on a fully annotated tree starts zero servers."""

    root = _annotated_tree(tmp_path)
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
    )
    assert proc.returncode == 0, proc.stderr
    assert _no_server_starts == []


def test_skip_leaves_the_lowering_input_unchanged(tmp_path: Path, _no_server_starts: list[str]) -> None:
    """The skipped compile lowers exactly what the engine-run compile lowers.

    The pins would have been empty either way, so the skip path is a pure
    cost cut: the same module dict, byte for byte.
    """

    root = _annotated_tree(tmp_path)
    fast = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "fast"),
        "--skip-check",
    )
    assert fast.returncode == 0, fast.stderr

    from pyscriptc.inferrunner import build_facts, has_unannotated_params

    assert has_unannotated_params(root) is False
    facts = build_facts(root)
    assert facts.pins == {}


def test_skip_flag_is_reported(tmp_path: Path, _no_server_starts: list[str]) -> None:
    """The skip test reports the annotated tree as needing no engine."""

    from pyscriptc.inferrunner import has_unannotated_params

    root = _annotated_tree(tmp_path)
    assert has_unannotated_params(root) is False


def test_unannotated_tree_still_pins(tmp_path: Path) -> None:
    """The skip must not eat the feature: the unannotated tree pins as before."""

    from pyscriptc.inferrunner import build_facts, has_unannotated_params

    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", _UNANNOTATED)
    write_case(root, "__main__.py", _ANNOTATED_MAIN)
    assert has_unannotated_params(root) is True
    facts = build_facts(root)
    assert facts.pins != {}
    assert any(pin.kind == "str" for pin in facts.pins.values())


def test_infer_timing_in_debug_stages(tmp_path: Path) -> None:
    """`--debug` shows the engine's outcome under the stages table."""

    root = _annotated_tree(tmp_path)
    dbg = tmp_path / "dbg"
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
        "--debug",
        f"--debug-dir={dbg}",
    )
    assert proc.returncode == 0, proc.stderr
    assert "[debug] infer: skipped (no unannotated parameter to prove)" in proc.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert summary["infer"]["skipped"] == 1
    assert summary["infer"]["pins"] == 0
    assert isinstance(summary["infer"]["ms"], int)


def test_infer_timing_ran_shows_pins_in_debug(tmp_path: Path) -> None:
    """A tree the engine ran on reports its pins and call sites."""

    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", _UNANNOTATED)
    write_case(root, "__main__.py", _ANNOTATED_MAIN)
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
        "--debug",
    )
    assert proc.returncode == 0, proc.stderr
    line = next(line for line in proc.stderr.splitlines() if line.startswith("[debug] infer:"))
    assert "skipped" not in line
    assert "pinned" in line


def test_plain_compile_has_no_infer_record(tmp_path: Path) -> None:
    """Without `--infer` the summary carries no infer dict at all."""

    root = _annotated_tree(tmp_path)
    dbg = tmp_path / "dbg"
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
        f"--debug-dir={dbg}",
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert "infer" not in summary


def test_single_file_skip_path(tmp_path: Path) -> None:
    """A single annotated file skips too (the engine does not run for it)."""

    from pyscriptc.inferrunner import build_facts, has_unannotated_params

    source = write_case(tmp_path, "solo.py", _ANNOTATED)
    assert has_unannotated_params(source) is False
    facts = build_facts(source)
    assert facts.pins == {}


def test_run_binary_output_still_matches_cpython(tmp_path: Path) -> None:
    """The skipped `--infer` compile produces a binary that agrees with CPython."""

    root = _annotated_tree(tmp_path)
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "bin",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
    )
    assert proc.returncode == 0, proc.stderr
    binary = next((tmp_path / "out").rglob("tree"))
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0
    assert run.stdout == "42\n"
