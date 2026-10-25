"""Regression tests for unmarshalling island function return values (issue #602).

A typed local receives the result of calling an island function across the seam
when annotated with int, float, bool, str, bytes, list[T], dict[K,V].
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_island_return_scalar_repro_fails_before_fix(tmp_path: Path) -> None:
    """Repro from issue #602: unmarshalling a function return value into typed int."""
    mod = tmp_path / "calc.py"
    mod.write_text("def first(items):\n    return items[0]\n", encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncalc = "island"\n', encoding="utf-8")

    src = tmp_path / "repro.py"
    src.write_text(
        """import calc

def main() -> None:
    val: int = calc.first([10, 20])
    print(val)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "repro_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    # Before the fix, this fails with PS1003
    assert comp.returncode == 0, comp.stderr
