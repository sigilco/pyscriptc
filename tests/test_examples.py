"""The user-facing scripts in examples/ match CPython when compiled."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import EXAMPLES, oracle, run_cli

pytestmark = pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh


def _run(tmp_path: Path, name: str, func: str, args: str) -> str:
    source = EXAMPLES / name
    got = run_cli(
        "compile",
        str(source),
        "--run",
        "--call",
        func,
        "--args",
        args,
        "-o",
        str(tmp_path / f"{name}-{func}"),
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(source, func, args)
    return got.stdout


def test_invoice(tmp_path: Path) -> None:
    assert _run(tmp_path, "invoice.py", "charge", "[10, 2, 50]") == "600\n"
    assert _run(tmp_path, "invoice.py", "invoice", "[10]") == "4290\n"
    assert _run(tmp_path, "invoice.py", "bad_invoice", "[]") == "-1\n"


def test_access_log(tmp_path: Path) -> None:
    good = '{"method":"GET","path":"/api/health","status":"200"}'
    post = '{"method":"POST","path":"/api/health","status":"200"}'
    other = '{"method":"GET","path":"/health","status":"200"}'
    assert _run(tmp_path, "access_log.py", "admit", repr([good])) == "0\n"
    assert _run(tmp_path, "access_log.py", "admit", repr([post])) == "1\n"
    assert _run(tmp_path, "access_log.py", "admit", repr([other])) == "1\n"


def test_request_id(tmp_path: Path) -> None:
    assert _run(tmp_path, "request_id.py", "request_id", repr(["id=88421 status=500"])) == "88421\n"
    assert _run(tmp_path, "request_id.py", "request_id", repr(["no digits"])) == "\n"


def test_release_note(tmp_path: Path) -> None:
    dest = tmp_path / "release.txt"
    args = repr(["2020-01-01", 40, str(dest)])
    assert _run(tmp_path, "release_note.py", "stamp", args) == "2020-02-10\n"
    assert dest.read_text(encoding="utf-8") == "2020-02-10"


def test_blob_digest(tmp_path: Path) -> None:
    digest = "34fb5c825de7ca4aea6e712f19d439c1da0c92c37b423936c5f618545ca4fa1f"
    assert _run(tmp_path, "blob_digest.py", "fingerprint", "[1, 2]") == digest + "\n"


def test_url_gate(tmp_path: Path) -> None:
    assert _run(tmp_path, "url_gate.py", "allow", repr(["https://example.com/api/health", "/api/*"])) == "1\n"
    assert _run(tmp_path, "url_gate.py", "allow", repr(["https://example.com/health", "/api/*"])) == "0\n"
    assert _run(tmp_path, "url_gate.py", "quote_path", repr(["https://example.com/a b"])) == "/a%20b\n"


def test_route_label(tmp_path: Path) -> None:
    assert _run(tmp_path, "route_label.py", "label", repr(["/api/health", 201])) == "health 201\n"
    assert _run(tmp_path, "route_label.py", "label", repr(["/api/health"])) == "health 200\n"


def test_hostname_matches_cpython(tmp_path: Path) -> None:
    _run(tmp_path, "hostname.py", "hostname", "[]")


def test_wordcount_program_directory_matches_cpython(tmp_path: Path) -> None:
    source = EXAMPLES / "wordcount"
    got = run_cli("compile", str(source), "--run", "-o", str(tmp_path / "wordcount"))
    assert got.returncode == 0, got.stderr
    expected = subprocess.run(
        [sys.executable, "__main__.py"], cwd=source, capture_output=True, text=True, check=True
    )
    assert got.stdout == expected.stdout
    assert got.stdout == "three 3\n"
