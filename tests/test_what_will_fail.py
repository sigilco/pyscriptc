"""What-will-fail data drift and coverage test.

Verifies that compat/data/what_will_fail.json is current with respect to the R0
corpus fences, has no errors, and covers every fence code produced by the corpus.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.real_scripts.measure import script_paths  # noqa: E402
from scripts.gen_what_will_fail import DEFAULT_DATA, SCRIPTS_DIR, build  # noqa: E402

from pyscriptc.coverage import build_report  # noqa: E402


def test_build_has_no_errors() -> None:
    _, errors = build()
    assert errors == []


def test_generated_data_is_current() -> None:
    files, errors = build(pin=DEFAULT_DATA)
    assert errors == []
    assert DEFAULT_DATA.exists()
    assert DEFAULT_DATA.read_text(encoding="utf-8") == files[str(DEFAULT_DATA)]


def test_cli_check_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_what_will_fail.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"check failed: {result.stderr}\n{result.stdout}"


def test_all_r0_fence_codes_represented() -> None:
    """Every non-derived fence code produced by R0 must appear in the data file."""
    files, errors = build()
    assert errors == []
    content = files[str(DEFAULT_DATA)]

    # Collect all non-derived fence codes from R0 scripts
    r0_codes: set[str] = set()
    for script in script_paths(SCRIPTS_DIR):
        report = build_report(script)
        for fence in report.get("fences", []):
            if not fence.get("derived", False):
                r0_codes.add(fence["code"])

    assert len(r0_codes) > 0
    for code in r0_codes:
        assert f'"{code}"' in content, f"missing fence code {code} in what-will-fail data"
