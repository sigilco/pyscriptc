"""Support data drift test: scripts/support_data.toml against compat/data/support.json.

The generated data file is developer data, not user docs: it lives under
`compat/data/support.json` and the user-facing summary links to the compat site.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_support_table import DEFAULT_DATA, build  # noqa: E402


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
        [sys.executable, str(ROOT / "scripts" / "gen_support_table.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"check failed: {result.stderr}\n{result.stdout}"


def test_support_json_structure() -> None:
    document = json.loads(DEFAULT_DATA.read_text(encoding="utf-8"))
    assert document["schema"] == "1"
    for key in ("sources", "summary", "builtins", "stdlib", "packages"):
        assert key in document, f"missing key {key}"
    assert "green" in document["summary"]["builtins"]
    assert len(document["builtins"]) > 0
    assert len(document["stdlib"]) > 0
