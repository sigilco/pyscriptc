"""The capability summary: native, fenced and island per file and per import.

The audit surface (coverage, imports, `--debug=stages` and `summary.json`)
counts a definition as island when a fence in it traces to runtime reflection
(issue #556).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_gates import run_cli

from pyscriptc.coverage import build_report as coverage_report
from pyscriptc.imports import build_report as imports_report
from pyscriptc.imports import render_text as render_imports

CAPABILITY_SOURCE = "\n".join(
    [
        "import inspect",
        "from typing import Union",
        "",
        "def native(x: int) -> int:",
        "    return x",
        "",
        "def optional(x: Union[int, str]) -> int:",
        "    return 1",
        "",
        "def signature(fn: int) -> int:",
        "    return inspect.signature(fn)",
        "",
        "def annotations(model: int) -> int:",
        "    return model.__annotations__",
        "",
    ]
)


def write_capability(tmp_path: Path) -> Path:
    path = tmp_path / "cap.py"
    path.write_text(CAPABILITY_SOURCE, encoding="utf-8")
    return path


def test_coverage_capability_is_split_per_file(tmp_path: Path) -> None:
    path = write_capability(tmp_path)
    report = coverage_report(path)
    caps = report["capabilities"]
    assert caps["totals"]["definitions"] == 4
    assert caps["totals"]["native"] == 1
    assert caps["totals"]["fenced"] == 1
    assert caps["totals"]["island"] == 2
    assert caps["totals"]["fences"] == 3
    assert caps["totals"]["files"] == 1
    assert caps["totals"]["mechanisms"] == ["__annotations__", "inspect.signature"]
    by_name = {item["name"]: item["capability"] for item in report["definitions"]}
    assert by_name == {
        "annotations": "island",
        "optional": "fenced",
        "signature": "island",
    }
    text = run_cli("coverage", str(path))
    assert text.returncode == 0, text.stderr
    assert "capability: 1 native, 1 fenced, 2 island definitions of 4" in text.stdout
    assert "reflection: __annotations__, inspect.signature" in text.stdout


def test_imports_capability_ladder_is_the_default_view(tmp_path: Path) -> None:
    path = write_capability(tmp_path)
    report = imports_report(path)
    by_name = {item["name"]: item["capability"] for item in report["imports"]}
    assert by_name["inspect"]["routing"] == "island"
    assert by_name["inspect"]["requires"] == "libpython"
    assert by_name["inspect"]["mechanisms"] == ["inspect.signature"]
    assert by_name["typing"]["routing"] == "native"
    assert report["summary"]["byCapability"] == {"island": 1, "native": 1}
    text = render_imports(report)
    assert "inspect: island (requires libpython) [stdlib, lane native]" in text
    assert "typing: native [stdlib, lane native]" in text


def test_an_import_table_fence_is_the_fenced_rung(tmp_path: Path) -> None:
    # `json.load` lowers now (#509); the fenced rung example is `json.dump`,
    # which keeps the PS2001 the import-table fence reports.
    path = tmp_path / "a.py"
    path.write_text(
        "import json\n\n\ndef f(d: dict[str, int], fp: str) -> int:\n    json.dump(d, fp)  # type: ignore[arg-type]\n"
        "    return 1\n",
        encoding="utf-8",
    )
    report = imports_report(path)
    capability = report["imports"][0]["capability"]
    assert capability["routing"] == "fenced"
    assert capability["codes"] == ["PS2001"]
    assert "json: native: fences (PS2001) [stdlib, lane native]" in render_imports(report)


def test_debug_stages_prints_the_capability_summary(tmp_path: Path) -> None:
    path = write_capability(tmp_path)
    got = run_cli(
        "compile", str(path), "--emit", "ir", "-o", str(tmp_path / "cap.ir.json"), "--skip-check", "--debug=stages"
    )
    assert got.returncode == 3
    assert "[debug] capability: 1 native, 1 fenced, 2 island definitions of 4" in got.stderr
    assert "reflection: __annotations__, inspect.signature" in got.stderr


def test_debug_dir_summary_carries_capabilities(tmp_path: Path) -> None:
    path = write_capability(tmp_path)
    debug_dir = tmp_path / "dbg"
    got = run_cli(
        "compile",
        str(path),
        "--emit",
        "ir",
        "-o",
        str(tmp_path / "cap.ir.json"),
        "--skip-check",
        "--debug-dir",
        str(debug_dir),
    )
    assert got.returncode == 3
    summary = json.loads((debug_dir / "summary.json").read_text())
    assert summary["capabilities"]["totals"]["island"] == 2
    assert summary["capabilities"]["totals"]["native"] == 1
