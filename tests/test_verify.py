"""`pyscriptc verify`: the differential receipt against the pinned CPython 3.14 oracle.

The toolchain tests run the real compile and link path, so they are skipped where
neither clang nor zig is installed. The oracle tests run in-process, which means
the pinned interpreter is the one running pytest (the project requires 3.14).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pyscriptc import cli, verify

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

MATCHING = """
import sys


def main() -> int:
    print("stdout:", 6 * 7)
    print("argv:", ",".join(sys.argv[1:]))
    line: str = input()
    print("stdin:", line.upper(), file=sys.stderr)
    return 7


if __name__ == "__main__":
    sys.exit(main())
"""

OVERFLOW = """
def main() -> int:
    big: int = 9223372036854775807
    print(big + 1)
    return 0


if __name__ == "__main__":
    main()
"""

CALLABLE = """
def add(a: int, b: int) -> int:
    return a + b


print("module body output is not part of the call")
"""

FENCED = "from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n"


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(home))
    monkeypatch.delenv("PYSC_NO_RUN_CACHE", raising=False)
    return home


def _script(tmp_path: Path, text: str, name: str = "prog.py") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _check(receipt: dict[str, object], name: str) -> dict[str, object]:
    checks = receipt["checks"]
    assert isinstance(checks, list)
    return next(check for check in checks if check["name"] == name)


def test_first_difference_reports_the_line_and_both_texts() -> None:
    expected = b"ok\n9223372036854775808\n"
    actual = b"ok\nOverflowError\n"
    difference = verify.first_difference(expected, actual)
    assert difference is not None
    assert difference["offset"] == 3
    assert difference["line"] == 2
    assert difference["oracle"] == "9223372036854775808"
    assert difference["native"] == "OverflowError"
    assert verify.first_difference(expected, expected) is None


def test_first_difference_when_one_stream_is_a_prefix() -> None:
    difference = verify.first_difference(b"a\nb\n", b"a\n")
    assert difference is not None
    assert difference["offset"] == 2
    assert difference["line"] == 2
    assert difference["native"] == ""


@needs_cc
def test_timeout_names_the_side_that_hung() -> None:
    hung = verify.Observation(stdout=b"", stderr=b"", exit_code=124, timed_out=True)
    fine = verify.Observation(stdout=b"", stderr=b"", exit_code=0)
    for oracle, native, expected in [
        (hung, fine, "  exit: the oracle timed out"),
        (fine, hung, "  exit: the native run timed out"),
        (hung, hung, "  exit: both sides timed out"),
    ]:
        checks = verify.compare(oracle, native)
        exit_check = next(check for check in checks if check["name"] == "exit")
        assert exit_check["oracleTimedOut"] is oracle.timed_out
        assert exit_check["nativeTimedOut"] is native.timed_out
        assert verify._render_check(exit_check) == [expected]


def test_matching_program_emits_a_clean_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, MATCHING)
    stdin = tmp_path / "in.txt"
    stdin.write_text("hello\n", encoding="utf-8")
    code = cli.main(["verify", str(source), "--skip-check", "--input", str(stdin), "--", "a", "b"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "verify " in out and ": match" in out
    assert "stdout: 21 bytes, match" in out
    assert "stderr: 13 bytes, match" in out
    assert "exit: 7, match" in out


@needs_cc
def test_int_overflow_diverges_with_a_pointed_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, OVERFLOW)
    code = cli.main(["verify", str(source), "--skip-check", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert code == 1
    assert receipt["schemaVersion"] == 1
    assert receipt["match"] is False
    assert receipt["oracle"]["version"].startswith("3.14.")
    stdout = _check(receipt, "stdout")
    assert stdout["match"] is False
    difference = stdout["difference"]
    assert difference["line"] == 1
    assert difference["oracle"] == "9223372036854775808"
    assert difference["native"] == ""
    exit_check = _check(receipt, "exit")
    assert exit_check["oracle"] == 0
    assert exit_check["native"] == 1


@needs_cc
def test_divergence_is_rendered_as_text_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, OVERFLOW)
    assert cli.main(["verify", str(source), "--skip-check"]) == 1
    out = capsys.readouterr().out
    assert "divergence" in out
    assert "stdout: differs at line 1" in out
    assert "oracle: 9223372036854775808" in out
    assert "exit: oracle 0, native 1" in out


@needs_cc
def test_call_mode_compares_the_return_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, CALLABLE)
    code = cli.main(["verify", str(source), "--skip-check", "--call", "add", "--args", "[2, 3]"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "call: add(2, 3)" in out
    assert "stdout: 2 bytes, match" in out


@needs_cc
def test_fenced_program_exits_3_with_the_fence_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, FENCED)
    assert cli.main(["verify", str(source), "--skip-check"]) == 3
    out = capsys.readouterr().out
    assert "fenced this program" in out
    assert "PS1104" in out


@needs_cc
def test_receipt_names_the_shared_cache_hit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, CALLABLE)
    assert cli.main(["verify", str(source), "--skip-check", "--call", "add", "--args", "[1, 2]", "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["verify", str(source), "--skip-check", "--call", "add", "--args", "[1, 2]", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["compile"]["cacheHit"] is True


def test_wrong_explicit_oracle_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _script(tmp_path, OVERFLOW)
    monkeypatch.setenv("PYSC_ORACLE", "/bin/sh")
    assert cli.main(["verify", str(source), "--skip-check"]) == 4
    assert "pinned to CPython 3.14" in capsys.readouterr().err


def test_receipt_carries_target_version_and_micro_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, MATCHING)
    stdin = tmp_path / "in.txt"
    stdin.write_text("hello\n", encoding="utf-8")
    assert cli.main(["verify", str(source), "--skip-check", "--input", str(stdin), "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["targetVersion"] == "3.14.9"
    assert "microMismatch" in receipt
    assert "microMismatch" in receipt["oracle"]


def test_oracle_micro_mismatch_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_exe = tmp_path / "fake_python"
    fake_exe.write_text("#!/bin/sh\nexit 0\n")
    fake_exe.chmod(0o755)
    monkeypatch.setenv("PYSC_ORACLE", str(fake_exe))
    monkeypatch.setattr(verify, "_version_of", lambda _: "3.14.8")
    with pytest.warns(UserWarning, match="oracle is CPython 3.14.8, declared target is 3.14.9"):
        oracle = verify.find_oracle()
    assert oracle.version == "3.14.8"
