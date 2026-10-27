"""Regression tests for an island-routed function called inside an f-string
placeholder (issue #733).

Before the fix, `print(f"{digest(path)}  ...")` fenced with
`PS2002: formatting digest() is not lowered` because the placeholder's
island handle had no annotation to unmarshal against. The fix rides the
str want on the same island-fn trampoline instead.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_fallback_fstring_placeholder_island_call_matches_cpython(tmp_path: Path) -> None:
    """The #733 repro shape: an island-routed user function inside an f-string."""
    src = tmp_path / "checksum_files.py"
    src.write_text(
        '''"""Print the SHA-256 digest of each file given on argv."""

import hashlib
import sys
from pathlib import Path


def digest(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk: bytes = handle.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: checksum_files FILE [FILE ...]\\n")
        return 2
    for path in sys.argv[1:]:
        print(f"{digest(path)}  {Path(path).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''',
        encoding="utf-8",
    )
    fixture = tmp_path / "lines.txt"
    fixture.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    binary = tmp_path / "prog"
    comp = _run_cli("compile", str(src), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    got = subprocess.run([str(binary), str(fixture)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run(
        [sys.executable, str(src), str(fixture)], capture_output=True, text=True, timeout=60, cwd=tmp_path
    )
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr
    assert got.stdout == f"{hashlib.sha256(fixture.read_bytes()).hexdigest()}  lines.txt\n"


def test_fallback_fstring_placeholder_island_call_matches_oracle(tmp_path: Path) -> None:
    """The corpus program compiles and reproduces the recorded CPython stdout."""
    corpus = ROOT / "benchmarks" / "scripts_corpus"
    oracle = json.loads((corpus / "oracle" / "checksum_files.json").read_text(encoding="utf-8"))
    recorded = Path(oracle["argv"][0]).name
    fixture = corpus / "fixtures" / recorded
    assert fixture.exists(), f"missing fixture {fixture}"

    src = corpus / "scripts" / "checksum_files" / "main.py"
    binary = tmp_path / "prog"
    comp = _run_cli(
        "compile",
        str(src),
        "--fallback=cpython",
        "--emit",
        "bin",
        "-o",
        str(binary),
        "--embed-python",
        "host",
        cwd=tmp_path,
    )
    assert comp.returncode == 0, comp.stderr

    got = subprocess.run([str(binary), str(fixture)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == oracle["exitCode"]
    assert got.stdout == oracle["stdout"]
    assert got.stderr == oracle["stderr"]
