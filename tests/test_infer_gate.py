# SPDX-License-Identifier: Apache-2.0
"""The inference gate: the engine runs only when the plain compile asks (issue #1305).

The engine is the expensive half of a compile: a checker server, a tree
walk, and a second lowering pass. It only proves types, so it can only
clear the fences a missing type caused. The compile therefore lowers the
tree once with no bridge, reads the fences that pass produced, and runs
the engine only when one of them is a missing-type fence.

The properties this file pins down:

- an annotated tree starts no checker server at all;
- a tree whose fence is not a missing-type one starts no checker server
  either (the gate closed);
- a tree with an unannotated parameter still pins and lowers natively;
- the gate's reason is never silent (the stages row and `summary.json`
  say why the engine stayed out);
- the classifier reads the coverage report's own families, so the gate
  and the report classify a fence the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

_ANNOTATED = "def parse_count(text: str, default: int = 0) -> int:\n    return len(text) + default\n"
_UNANNOTATED = "def parse_count(text, default=0):\n    return len(text) + default\n"
_MAIN = (
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


@pytest.fixture
def _no_server_starts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every checker server start the compile performs."""

    from pyscriptc import factsbridge, typefacts

    starts: list[str] = []

    def _boom(self: object, *args: object, **kwargs: object) -> None:
        starts.append(type(self).__name__)

    monkeypatch.setattr(typefacts.PyreflyTspFactsAdapter, "_connect", _boom)
    monkeypatch.setattr(factsbridge.PyreflyBridge, "open", _boom)
    return starts


def _tree(tmp_path: Path, helpers: str) -> Path:
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", helpers)
    write_case(root, "__main__.py", _MAIN)
    return root


def _compile(root: Path, tmp_path: Path, *extra: str) -> object:
    return run_cli(
        "compile",
        str(root),
        "--program",
        "--emit",
        "ir",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
        *extra,
    )


def test_an_annotated_tree_starts_no_checker_server(tmp_path: Path, _no_server_starts: list[str]) -> None:
    """An annotated tree lowers once and the engine never starts."""

    root = _tree(tmp_path, _ANNOTATED)
    proc = _compile(root, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert _no_server_starts == []


def test_a_foreign_fence_starts_no_checker_server(tmp_path: Path, _no_server_starts: list[str]) -> None:
    """A fence the engine cannot clear keeps the engine out.

    An `os` attribute outside the lowered subset is not a type question:
    the engine proves nothing about it, so the gate closes and no checker
    server starts.
    """

    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "__main__.py", "import os\n\n\ndef f() -> None:\n    x = 1\n    os.not_a_real_attr(x)\n")
    proc = _compile(root, tmp_path)
    assert proc.returncode == 3, proc.stderr
    assert _no_server_starts == []


def test_an_unannotated_parameter_still_pins(tmp_path: Path) -> None:
    """The gate must not eat the feature: the unannotated tree pins."""

    root = _tree(tmp_path, _UNANNOTATED)
    proc = _compile(root, tmp_path)
    assert proc.returncode == 0, proc.stderr
    module = json.loads((tmp_path / "out" / "tree.ir.json").read_text())
    names = [fn["name"] for fn in module["functions"]]
    assert "helpers.parse_count" in names


def test_the_gate_reason_is_never_silent(tmp_path: Path) -> None:
    """A closed gate says why in the stages row and in `summary.json`."""

    root = _tree(tmp_path, _ANNOTATED)
    dbg = tmp_path / "dbg"
    proc = _compile(root, tmp_path, "--debug", f"--debug-dir={dbg}")
    assert proc.returncode == 0, proc.stderr
    assert "[debug] infer: skipped (no fence the engine could clear)" in proc.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert summary["infer"]["skipped"] == 1
    assert summary["infer"]["reason"] == "no fence the engine could clear"
    assert summary["infer"]["pins"] == 0


def test_the_gate_reads_the_coverage_families() -> None:
    """The classifier and the coverage report classify a fence the same way."""

    from pyscriptc.coverage import family_of
    from pyscriptc.infergate import is_rescuable

    cases = [
        ({"code": "PS1001", "message": "parameter text needs an annotation"}, True),
        ({"code": "PS1001", "message": "total needs an annotation before use"}, True),
        ({"code": "PS1001", "message": "main needs a return annotation"}, True),
        ({"code": "PS1107", "message": "the element type of this read is unresolved"}, True),
        ({"code": "PS2002", "message": "helpers.parse_count is not exported by this package"}, True),
        ({"code": "PS2002", "message": "call to parse_count is not lowered"}, True),
        ({"code": "PS2002", "message": "async functions do not lower"}, False),
        ({"code": "PS2001", "message": "os.not_a_real_attr is not in the lowered subset of os"}, False),
        ({"code": "PS2001", "message": "x is not a field"}, False),
        ({"code": "PS1001", "message": "an island subscript needs an annotation before use"}, False),
    ]
    for fence, expected in cases:
        assert is_rescuable(fence) is expected, fence
        # The gate and the report never disagree about a family.
        if fence["code"] == "PS1001":
            assert family_of(fence["code"], fence["message"]) in {
                "param-inference",
                "local-inference",
                "return-inference",
                "annotation-other",
            }


def test_a_closed_gate_keeps_the_plain_fences(tmp_path: Path) -> None:
    """A tree the gate closed reports exactly the plain compile's fences."""

    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "__main__.py", "import os\n\n\ndef f() -> None:\n    x = 1\n    os.not_a_real_attr(x)\n")
    proc = _compile(root, tmp_path)
    module = json.loads((tmp_path / "out" / "tree.ir.json").read_text())
    assert any(fence["code"] == "PS2001" for fence in module["fences"])
    assert proc.returncode == 3
