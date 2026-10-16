"""flipcheck cross-checks predicted flips against achieved flips.

Fixture-based: the predicted list is synthesized from the scan tables in
``docs/e1-entrypoint-detector-spec.md`` (the permanent E1 detector lives inside
E1 and has not landed); the achieved files mirror the two irscore output
formats, the ``--out`` baseline JSON and the stdout diff sections.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.compat2.flipcheck import (
    cross_check,
    load_achieved,
    load_achieved_text,
    load_predicted,
    main,
    path_to_probe_id,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "flipcheck"
PREDICTED = FIXTURES / "predicted.json"
ACHIEVED_BASELINE = FIXTURES / "achieved-baseline.json"
ACHIEVED_STDOUT = FIXTURES / "achieved-stdout.txt"


def test_path_to_probe_id_maps_scripts() -> None:
    assert path_to_probe_id("benchmarks/compat2/corpus/scripts/pi_digits/main.py") == "scripts.pi_digits"
    assert path_to_probe_id("benchmarks/compat2/corpus/scripts/linked_list/main.py") == "scripts.linked_list"


def test_path_to_probe_id_passes_through_other_paths() -> None:
    nested = "benchmarks/compat2/corpus/scripts/nope/dir/main.py"
    assert path_to_probe_id(nested) == nested
    assert path_to_probe_id("examples/floor_div.py") == "examples/floor_div.py"
    assert path_to_probe_id("benchmarks/compat2/corpus/scripts/pi_digits/expected.txt") == (
        "benchmarks/compat2/corpus/scripts/pi_digits/expected.txt"
    )


def test_load_predicted_keeps_scripts_only() -> None:
    records = json.loads(PREDICTED.read_text(encoding="utf-8"))
    assert load_predicted(records) == [
        "benchmarks/compat2/corpus/scripts/bigint_factorial/main.py",
        "benchmarks/compat2/corpus/scripts/nbody/main.py",
        "benchmarks/compat2/corpus/scripts/pi_digits/main.py",
    ]


def test_load_predicted_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        load_predicted({"path": "x"})
    with pytest.raises(ValueError):
        load_predicted([42])


def test_load_achieved_from_baseline_json() -> None:
    doc = json.loads(ACHIEVED_BASELINE.read_text(encoding="utf-8"))
    assert load_achieved(doc) == ["builtins.sum", "scripts.bigint_factorial", "scripts.pi_digits"]


def test_load_achieved_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError):
        load_achieved([1, 2])
    with pytest.raises(ValueError):
        load_achieved({"no_probes": {}})

    text = ACHIEVED_STDOUT.read_text(encoding="utf-8")
    expected = ["builtins.sum", "scripts.bigint_factorial", "scripts.nbody", "scripts.pi_digits"]
    assert load_achieved_text(text) == expected


def test_cross_check_counts() -> None:
    report = cross_check(
        ["scripts.bigint_factorial", "scripts.pi_digits", "scripts.nbody"],
        ["builtins.sum", "scripts.bigint_factorial", "scripts.pi_digits"],
    )
    assert report == {
        "achieved": 3,
        "hit_rate": round(2 / 3, 4),
        "hits": ["scripts.bigint_factorial", "scripts.pi_digits"],
        "misses": ["scripts.nbody"],
        "predicted": 3,
        "surprises": ["builtins.sum"],
    }


def test_cross_check_empty_predicted_gives_zero_rate() -> None:
    report = cross_check([], ["scripts.pi_digits"])
    assert report == {
        "achieved": 1,
        "hit_rate": 0.0,
        "hits": [],
        "misses": [],
        "predicted": 0,
        "surprises": ["scripts.pi_digits"],
    }


def test_cross_check_empty_both() -> None:
    report = cross_check([], [])
    assert report == {
        "achieved": 0,
        "hit_rate": 0.0,
        "hits": [],
        "misses": [],
        "predicted": 0,
        "surprises": [],
    }


def _run_main(predicted: Path, achieved: Path) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([str(predicted), str(achieved)])
    assert code == 0
    return buffer.getvalue()


def test_main_baseline_json_output_shape() -> None:
    out = _run_main(PREDICTED, ACHIEVED_BASELINE)
    doc = json.loads(out)
    assert doc == {
        "achieved": 3,
        "hit_rate": round(2 / 3, 4),
        "hits": ["scripts.bigint_factorial", "scripts.pi_digits"],
        "misses": ["scripts.nbody"],
        "predicted": 3,
        "surprises": ["builtins.sum"],
    }
    assert out == json.dumps(doc, sort_keys=True, indent=1) + "\n"


def test_main_accepts_irsore_stdout_text() -> None:
    out = _run_main(PREDICTED, ACHIEVED_STDOUT)
    doc = json.loads(out)
    assert doc["hits"] == ["scripts.bigint_factorial", "scripts.nbody", "scripts.pi_digits"]
    assert doc["misses"] == []
    assert doc["hit_rate"] == 1.0
    assert doc["surprises"] == ["builtins.sum"]


def test_main_output_byte_identical_across_runs() -> None:
    first = _run_main(PREDICTED, ACHIEVED_BASELINE)
    second = _run_main(PREDICTED, ACHIEVED_BASELINE)
    assert first == second


def test_main_output_byte_identical_via_subprocess() -> None:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "benchmarks.compat2.flipcheck", str(PREDICTED), str(ACHIEVED_BASELINE)]
    first = subprocess.run(cmd, capture_output=True, text=True, cwd=root, check=True)
    second = subprocess.run(cmd, capture_output=True, text=True, cwd=root, check=True)
    assert first.stdout == second.stdout
    assert first.stdout.endswith("\n")
    assert first.stdout == json.dumps(json.loads(first.stdout), sort_keys=True, indent=1) + "\n"


def test_main_bad_inputs_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(PREDICTED), "/nonexistent/flipcheck-input.json"]) == 2
    assert "cannot read input" in capsys.readouterr().err


def test_main_bad_predicted_shape_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    bad = FIXTURES / "predicted-bad.json"
    assert main([str(bad), str(ACHIEVED_BASELINE)]) == 2
    assert "bad predicted file" in capsys.readouterr().err
