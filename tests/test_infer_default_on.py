# SPDX-License-Identifier: Apache-2.0
"""Inference by default and the `noinfer` debug topic (issue #1305).

The call-site pins are the default: every compile runs the pin engine,
so an unannotated parameter whose call sites agree lowers without the
source being annotated. The `--infer` option is gone, and there is no
`--no-infer`: the only way to take the engine out is the `noinfer` debug
topic (`PYSC_DEBUG=noinfer`), a developer bisect tool that is not in
`--help` and is documented only in `.agents/docs/debug.md`.

The properties this file pins down:

- the engine runs by default (an unannotated tree pins and lowers
  natively without any option);
- the topic takes the engine out (zero checker server starts, no pins);
- the topic is never silent (the stages row says `disabled (noinfer)` and
  `summary.json` records `infer.skipped = "disabled"`);
- the topic is not discoverable from `--help` (there is no user switch).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, run_cli, write_case

_HELPERS = "def parse_count(text, default=0):\n    return text\n"
_MAIN = (
    "from helpers import parse_count\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(parse_count('42'))\n"
    "    print(parse_count('41'))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", _HELPERS)
    write_case(root, "__main__.py", _MAIN)
    return root


def _compile(
    root: Path,
    out_dir: Path,
    *,
    env_extra: dict[str, str] | None = None,
    debug_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if env_extra:
        env.update(env_extra)
    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(root),
        "--program",
        "--emit",
        "ir",
        "--out-dir",
        str(out_dir),
        "--skip-check",
    ]
    if debug_dir is not None:
        command.append(f"--debug-dir={debug_dir}")
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="latin-1",
    )


def test_the_engine_runs_without_any_option(tmp_path: Path) -> None:
    """An unannotated parameter lowers natively: the pins are the default."""

    root = _tree(tmp_path)
    proc = _compile(root, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    names = [fn["name"] for fn in json.loads((tmp_path / "out" / "tree.ir.json").read_text())["functions"]]
    assert "helpers.parse_count" in names


def test_noinfer_takes_the_engine_out(tmp_path: Path) -> None:
    """With the topic the tree fences again: the engine proved nothing."""

    root = _tree(tmp_path)
    proc = _compile(root, tmp_path / "out", env_extra={"PYSC_DEBUG": "noinfer"})
    assert proc.returncode == 3, proc.stderr
    fences = json.loads((tmp_path / "out" / "tree.ir.json").read_text())["fences"]
    assert any(fence["code"] == "PS1001" for fence in fences)


def test_noinfer_starts_no_checker_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The disabled engine starts no checker server at all.

    The compile runs in-process, so the patched server starts are the
    ones the compile really performs.
    """

    from pyscriptc import factsbridge, typefacts

    starts: list[str] = []

    def _boom(self: object, *args: object, **kwargs: object) -> None:
        starts.append(type(self).__name__)

    monkeypatch.setattr(typefacts.PyreflyTspFactsAdapter, "_connect", _boom)
    monkeypatch.setattr(factsbridge.PyreflyBridge, "open", _boom)
    monkeypatch.setenv("PYSC_DEBUG", "noinfer")

    root = _tree(tmp_path)
    from pyscriptc.cli import main

    code = main(
        [
            "compile",
            str(root),
            "--program",
            "--emit",
            "ir",
            "--out-dir",
            str(tmp_path / "out"),
            "--skip-check",
        ]
    )
    assert code == 3
    assert starts == []


def test_noinfer_is_reported_in_the_stages_row_and_the_summary(tmp_path: Path) -> None:
    """The topic is never silent: the row says so and the summary records it."""

    root = tmp_path / "annotated"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", "def parse_count(text: str) -> str:\n    return text\n")
    write_case(
        root, "__main__.py", "from helpers import parse_count\n\n\ndef main() -> None:\n    print(parse_count('42'))\n"
    )
    dbg = tmp_path / "dbg"
    proc = _compile(root, tmp_path / "out", env_extra={"PYSC_DEBUG": "noinfer,stages"}, debug_dir=dbg)
    assert proc.returncode == 0, proc.stderr
    assert "[debug] infer: disabled (noinfer)" in proc.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert summary["infer"]["skipped"] == "disabled"
    assert summary["infer"]["pins"] == 0


def test_noinfer_is_not_in_the_help_text() -> None:
    """The topic is a developer tool: no user-facing switch ships."""

    proc = run_cli("compile", "--help")
    assert proc.returncode == 0, proc.stderr
    assert "--no-infer" not in proc.stdout
    assert "noinfer" not in proc.stdout
    assert "--infer" not in proc.stdout


def test_the_infer_option_is_gone() -> None:
    """The dropped option is not accepted any more."""

    proc = run_cli("compile", "--infer", "x.py")
    assert proc.returncode != 0
    assert "--infer" in proc.stderr
