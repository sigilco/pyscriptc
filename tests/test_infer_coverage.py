# SPDX-License-Identifier: Apache-2.0
"""`pyscriptc infer --report`: the inference coverage report (issue #1304).

Stage 1 of the loose-typing and default-inference rollout ships the
measurement instrument: the pin rate per module and per tree, so the
stage 2 decision reads a number instead of a guess.

The contract these tests hold:

- every unannotated parameter of every module is counted (annotated
  parameters and typed defaults are not part of the surface);
- a pin the package proved marks its parameter pinned, and an unannotated
  parameter no pin reaches stays unpinned;
- the tree total is the sum of the module rows;
- the text report and the JSON report carry the same numbers;
- the report is diagnostics only: the pins and the lowering are
  untouched, so the ordinary `infer` report still reads the same.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_gates import run_cli, write_case


def _report(root: Path, *extra: str) -> dict:
    out = run_cli("infer", str(root), "--report", "--json", *extra)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _module(report: dict, name: str) -> dict:
    rows = {row["module"]: row for row in report["modules"]}
    assert name in rows, f"the report misses module {name}: {sorted(rows)}"
    return rows[name]


def test_report_counts_unannotated_pinned_and_unpinned_per_module(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    report = _report(root)
    lib = _module(report, "lib")
    assert lib["unannotated_params"] == 1
    assert lib["pinned_params"] == 1
    assert lib["unpinned_params"] == 0
    assert lib["pin_rate"] == 1.0
    assert lib["params"] == [{"param": "lib.shout.text", "pinned": True, "type": "str"}]


def test_report_leaves_an_unproven_parameter_unpinned(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n\n\ndef quiet(sink):\n    return sink\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    report = _report(root)
    lib = _module(report, "lib")
    assert lib["unannotated_params"] == 2
    assert lib["pinned_params"] == 1
    assert lib["unpinned_params"] == 1
    assert lib["pin_rate"] == 0.5
    unpinned = [row["param"] for row in lib["params"] if not row["pinned"]]
    assert unpinned == ["lib.quiet.sink"]


def test_report_ignores_annotated_parameters_and_typed_defaults(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(
        root,
        "lib.py",
        "def typed(text: str) -> str:\n    return text\n\n\ndef defaulted(count=0):\n    return count\n",
    )
    write_case(root, "__main__.py", "from lib import typed\n\ntyped('a')\n")
    report = _report(root)
    lib = _module(report, "lib")
    # The annotated parameter needs no proof, and the default already
    # types its parameter (`def f(x=0)`, issue #500): neither is part of
    # the surface the pins would lift.
    assert lib["unannotated_params"] == 0
    assert lib["pin_rate"] is None


def test_report_tree_total_is_the_sum_of_the_module_rows(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "extra.py", "def quiet(sink):\n    return sink\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    report = _report(root)
    total = report["total"]
    assert total["unannotated_params"] == sum(row["unannotated_params"] for row in report["modules"])
    assert total["pinned_params"] == sum(row["pinned_params"] for row in report["modules"])
    assert total["unpinned_params"] == sum(row["unpinned_params"] for row in report["modules"])
    assert total["unannotated_params"] == 2
    assert total["pinned_params"] == 1
    assert total["pin_rate"] == 0.5


def test_report_text_carries_the_same_numbers_as_the_json(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    out = run_cli("infer", str(root), "--report")
    assert out.returncode == 0, out.stderr
    assert "module" in out.stdout
    assert "unannotated" in out.stdout
    assert "pin rate" in out.stdout
    assert "total" in out.stdout
    assert "lib" in out.stdout


def test_report_leaves_the_ordinary_infer_report_untouched(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    plain = json.loads(run_cli("infer", str(root), "--json").stdout)
    assert "pins" in plain
    assert "modules" not in plain
    # The flag adds a report of its own; the ordinary report keeps its
    # shape, so a script reading it is unaffected.
    report = json.loads(run_cli("infer", str(root), "--report", "--json").stdout)
    assert "modules" in report
    assert "pins" not in report


def test_report_works_on_the_own_arm(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    report = _report(root, "--bridge", "own")
    assert report["provider"] == "own"
    lib = _module(report, "lib")
    assert lib["unannotated_params"] == 1
    assert lib["pinned_params"] == 1
    assert lib["pin_rate"] == 1.0


def test_report_flag_is_in_the_cli_help(tmp_path: Path) -> None:
    out = run_cli("infer", "--help")
    assert out.returncode == 0
    assert "--report" in out.stdout
