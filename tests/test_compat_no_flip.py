"""Receipt-2: compat outcome no-flip tool (handoff 6)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.compat_no_flip import compare, load_results, main, receipt_line, render_markdown

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compat_no_flip.py"


def _score(rows: list[tuple[str, str]], weights: dict[str, float] | None = None) -> dict:
    payload: dict = {
        "oracle": "3.14.9",
        "rows": [
            {"id": probe_id, "outcome": outcome, "area": "language", "category": "language"}
            for probe_id, outcome in rows
        ],
        "counts": {},
    }
    if weights is not None:
        payload["weights"] = weights
    return payload


def test_zero_flips_matches_v080_receipt_shape() -> None:
    rows = [("lang.a", "pass"), ("lang.b", "fence")]
    weights = {"lang.a": 1.0, "lang.b": 1.0}
    report = compare(_score(rows, weights), _score(rows, weights))
    assert report["flips_either_direction"] == 0
    assert report["a_to_b"] == []
    assert report["b_to_a"] == []
    line = receipt_line(report)
    assert "2 probes" in line
    assert "0 outcome flips in either direction" in line
    assert "oracle 3.14.9" in line
    assert "scorer v2" in line


def test_flips_are_reported_in_both_directions() -> None:
    before = _score([("p1", "pass"), ("p2", "fence"), ("p3", "crash")])
    after = _score([("p1", "fence"), ("p2", "pass"), ("p3", "crash")])
    report = compare(before, after)
    assert report["flips_either_direction"] == 2
    a_to_b = {row["id"]: (row["from"], row["to"]) for row in report["a_to_b"]}
    b_to_a = {row["id"]: (row["from"], row["to"]) for row in report["b_to_a"]}
    assert a_to_b["p1"] == ("pass", "fence")
    assert a_to_b["p2"] == ("fence", "pass")
    assert b_to_a["p1"] == ("fence", "pass")
    assert b_to_a["p2"] == ("pass", "fence")
    assert "p3" not in a_to_b
    table = render_markdown(report)
    assert "p1" in table
    assert "A to B: 2" in table
    assert "B to A: 2" in table


def test_weighted_pass_uses_file_weights() -> None:
    before = _score([("a", "pass"), ("b", "fence")], {"a": 2.0, "b": 5.0})
    after = _score([("a", "pass"), ("b", "pass")], {"a": 2.0, "b": 5.0})
    report = compare(before, after)
    assert report["weighted_pass_before"] == 2.0
    assert report["weighted_pass_after"] == 7.0
    assert report["flips_either_direction"] == 1


def test_missing_ids_are_not_silent() -> None:
    before = _score([("keep", "pass"), ("gone", "pass")])
    after = _score([("keep", "pass"), ("new", "fence")])
    report = compare(before, after)
    assert report["missing_after"] == ["gone"]
    assert report["missing_before"] == ["new"]
    assert report["flips_either_direction"] == 0


def test_cli_markdown_and_json(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_score([("x", "pass")])) + "\n", encoding="utf-8")
    b.write_text(json.dumps(_score([("x", "fence")])) + "\n", encoding="utf-8")
    code = main([str(a), str(b)])
    assert code == 0
    json_out = tmp_path / "out.json"
    md_out = tmp_path / "out.md"
    code = main([str(a), str(b), "--json-out", str(json_out), "--md-out", str(md_out), "--json"])
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["flips_either_direction"] == 1
    assert "outcome flips in either direction" in md_out.read_text(encoding="utf-8")


def test_load_rejects_non_score_json(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"
    path.write_text("{}\n", encoding="utf-8")
    try:
        load_results(path)
    except ValueError as exc:
        assert "rows" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_source_does_not_touch_closed_corpus() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "holdout" not in text.lower()
    assert "from benchmarks" not in text
    assert "EVALUATOR" not in text
    assert "import corpus" not in text


def test_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "flips" in proc.stdout
