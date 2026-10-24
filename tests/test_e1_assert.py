"""E1 item 4: `assert` lowers in the entry exactly as in a function.

The statement is `if not cond: raise AssertionError`, and the message
sits on that raise. Decisions pinned here:

- Assertions are KEPT: there is no `-O` stripping semantics in the subset,
  so a failing assert aborts the run with a nonzero exit and the message
  text, and a passing assert falls through to the next statement.
- The message expression is evaluated only on the failing branch: a call
  in the message of a passing assert never runs (no side effect).
- The condition accepts anything `if` accepts: bool plus the truthy
  kinds (`_TRUTHY` in `_core.py`: str, bytes, int, list, dict), so
  `assert 1` and `assert "nonempty"` pass like CPython's truthiness; a
  float condition is fenced (PS2004) - pinned in test_assert.py's
  existing fence cases, so not re-pinned here.
- AssertionError is a lowered exception (#515): `except AssertionError`
  catches the failure, and the message is a `str`.

Both forms (`assert cond`, `assert cond, msg`) are pinned in the
synthesized entry (module-level script) and in a function, against the
CPython oracle.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case
from tests.test_spike import ROOT

from pyscriptc.lower import lower_source


def oracle_script(path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def test_entry_assert_pass_and_fail_run_like_cpython(tmp_path: Path) -> None:
    # The entry keeps assertions: the passing lines fall through, the
    # failing one aborts with the message on stderr and a nonzero exit.
    path = write_case(
        tmp_path,
        "entry_assert.py",
        "\n".join(
            [
                "assert 1 + 1 == 2",
                "assert (2 > 1) and not (1 > 2)",
                'assert 2 == 1, "entry guard tripped"',
                'print("unreached")',
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "entry_assert"), "--run")
    assert got.returncode != 0, got.stdout
    assert got.returncode == 1, got.returncode
    assert "entry guard tripped" in got.stderr
    assert "unreached" not in got.stdout
    # CPython: AssertionError, message text on stderr, nonzero exit.
    cpython = subprocess_run(path)
    assert cpython.returncode != 0
    assert "entry guard tripped" in cpython.stderr
    assert "AssertionError" in cpython.stderr


def subprocess_run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_entry_passing_assert_falls_through_like_cpython(tmp_path: Path) -> None:
    # A clean entry script: every assert passes, the prints run, output
    # matches CPython byte for byte.
    path = write_case(
        tmp_path,
        "entry_pass.py",
        "\n".join(
            [
                "assert True",
                "assert 1",
                'assert "nonempty"',
                "x: int = 3",
                'assert x == 3, "x moved"',
                'print("kept", x)',
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "entry_pass"), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path) == "kept 3\n"


def test_function_assert_forms_match_oracle(tmp_path: Path) -> None:
    # One- and two-arg forms in a plain function: truthy scalars pass,
    # the failing branch raises, and the message call is skipped on pass.
    path = write_case(
        tmp_path,
        "fn_assert.py",
        "\n".join(
            [
                "def noisy() -> str:",
                '    print("side effect ran")',
                '    return "boom"',
                "",
                "",
                "def passes() -> int:",
                "    assert 1",
                '    assert "nonempty"',
                "    assert 2 + 2 == 4, noisy()",
                "    return 7",
                "",
                "",
                "def fails_silently() -> int:",
                "    assert 1 == 2",
                "    return 7",
                "",
                "",
                "def fails_loudly() -> int:",
                '    assert 1 == 2, "guard tripped"',
                "    return 7",
            ]
        ),
    )
    passing = run_cli("compile", str(path), "--run", "--call", "passes", "-o", str(tmp_path / "passes"))
    assert passing.returncode == 0, passing.stderr
    assert passing.stdout.strip() == "7"
    assert "side effect ran" not in passing.stdout  # message expr skipped on pass
    silent = run_cli("compile", str(path), "--run", "--call", "fails_silently", "-o", str(tmp_path / "silent"))
    assert silent.returncode != 0
    assert "fails_loudly" in path.read_text()
    loud = run_cli("compile", str(path), "--run", "--call", "fails_loudly", "-o", str(tmp_path / "loud"))
    assert loud.returncode != 0
    assert "guard tripped" in loud.stderr


def test_assert_lowers_to_if_not_with_raise() -> None:
    # The IR shape: an `if` on the negated condition whose then-branch is
    # exactly one raise; the message node sits on the raise, so a passing
    # run never evaluates it. Same shape in a function and in the entry.
    mod = lower_source(
        """
def probe() -> int:
    assert 1 + 1 == 2, "math broke"
    return 1


assert probe() == 1, "entry broke"
"""
    )
    assert mod["fences"] == []
    probe = next(fn for fn in mod["functions"] if fn["name"] == "probe")
    (guard,) = probe["body"][:1]
    assert guard["kind"] == "if"
    assert guard["cond"]["kind"] == "unary" and guard["cond"]["op"] == "not"
    (raised,) = guard["then"]
    assert raised["kind"] == "raise" and raised["exc"] == "AssertionError"
    assert raised["message"]["kind"] == "const" and raised["message"]["value"] == "math broke"
    entry = _entry(mod)
    assert entry is not None
    (guard,) = entry["body"][:1]
    assert guard["kind"] == "if" and guard["cond"]["op"] == "not"
    (raised,) = guard["then"]
    assert raised["kind"] == "raise" and raised["message"]["value"] == "entry broke"


def test_assert_kept_no_minus_o_semantics() -> None:
    # Assertions are kept in every mode: a failing assert aborts even when
    # the module carries the usual -O-adjacent shapes (a docstring, a
    # __debug__-free body). There is no strip flag in the subset, so the
    # guard is unconditional IR, not an option.
    mod = lower_source(
        """
def probe() -> int:
    assert False
    return 1
"""
    )
    probe = next(fn for fn in mod["functions"] if fn["name"] == "probe")
    kinds = [stmt["kind"] for stmt in probe["body"]]
    assert "if" in kinds  # the guard survived lowering
    assert mod["fences"] == []
