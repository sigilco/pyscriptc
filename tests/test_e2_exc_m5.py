"""E2 M5: the uncaught exception report.

An uncaught exception prints `Name: message` on stderr and exits 1. The
message rules follow CPython: a `KeyError` shows its argument as a repr,
a `ValueError` shows its message as is, and a runtime-raised exception
carries the message CPython gives it. The cause chain prints the cause
first, then the "direct cause" line, then the exception itself.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, compile_run, oracle, write_case


def oracle_report(path: Path, func: str) -> tuple[int, str]:
    """The exit code and the report lines CPython prints for the same source.

    The traceback frames are dropped: the subset has no frames to list, so
    only the exception lines are comparable.
    """

    source = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"value = mod.{func}()\n"
    )
    proc = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True)
    lines = []
    for line in proc.stderr.splitlines():
        if not line.strip():
            continue
        if line.startswith("Traceback") or line.startswith("  File"):
            continue
        if line.startswith("    ") and not line.startswith("    The above"):
            continue  # the echoed source line
        lines.append(line)
    return proc.returncode, "\n".join(lines)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_exception_prints_name_and_message(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "uncaught.py",
        'def measure() -> int:\n    raise ValueError("boom")\n',
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "ValueError: boom"
    assert oracle_report(path, "measure") == (1, "ValueError: boom")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_exception_without_a_message_prints_the_name(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nomessage.py",
        "def measure() -> int:\n    raise ValueError()\n",
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "ValueError"
    assert oracle_report(path, "measure") == (1, "ValueError")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_keyerror_quotes_its_key(tmp_path: Path) -> None:
    """CPython shows a KeyError's argument as a repr: KeyError: 'missing'."""

    path = write_case(
        tmp_path,
        "key.py",
        'def measure() -> int:\n    raise KeyError("missing")\n',
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "KeyError: 'missing'"
    assert oracle_report(path, "measure") == (1, "KeyError: 'missing'")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_user_exception_names_the_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "",
                "def measure() -> int:",
                '    raise AppError("nope")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "AppError: nope"
    # The oracle loads the module as `mod`, so CPython qualifies the class
    # name there; the compiled binary is a __main__ script and does not.
    assert oracle_report(path, "measure") == (1, "mod.AppError: nope")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_user_exception_subclass_names_its_own_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sub.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "",
                "class Sub(AppError):",
                "    pass",
                "",
                "",
                "def measure() -> int:",
                '    raise Sub("nope")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "Sub: nope"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_the_cause_prints_before_the_exception(tmp_path: Path) -> None:
    """`raise X from e` prints the cause, then the link, then the exception."""

    path = write_case(
        tmp_path,
        "cause.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("first")',
                "    except ValueError as e:",
                '        raise RuntimeError("second") from e',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == (
        "ValueError: first\nThe above exception was the direct cause of the following exception.\nRuntimeError: second"
    )
    assert oracle_report(path, "measure") == (
        1,
        "ValueError: first\nThe above exception was the direct cause of the following exception:\nRuntimeError: second",
    )


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_raise_from_none_suppresses_the_chain(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "suppress.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("first")',
                "    except ValueError:",
                '        raise RuntimeError("second") from None',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "RuntimeError: second"
    assert oracle_report(path, "measure") == (1, "RuntimeError: second")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_bare_raise_reports_the_original_exception(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "reraise.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError:",
                "        raise",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "ValueError: boom"
    assert oracle_report(path, "measure") == (1, "ValueError: boom")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_an_unmatched_handler_lets_the_exception_escape(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "unmatched.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("boom")',
                "    except KeyError:",
                "        print('not this one')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "ValueError: boom"
    assert oracle_report(path, "measure") == (1, "ValueError: boom")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_runtime_index_error_names_the_container(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "index.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    xs: list[int] = [1]",
                "    return xs[5]",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "IndexError: list index out of range"
    assert oracle_report(path, "measure") == (1, "IndexError: list index out of range")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_runtime_string_index_error_names_the_container(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "strindex.py",
        "\n".join(
            [
                "def measure() -> str:",
                '    s: str = "ab"',
                "    return s[5]",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "IndexError: string index out of range"
    assert oracle_report(path, "measure") == (1, "IndexError: string index out of range")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_missing_dict_key_reports_the_key(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "missing.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    d: dict[str, int] = {}",
                "    return d['missing']",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "KeyError: 'missing'"
    assert oracle_report(path, "measure") == (1, "KeyError: 'missing'")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_missing_int_dict_key_reports_the_key(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "missing_int.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    d: dict[int, int] = {}",
                "    return d[42]",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stderr.strip() == "KeyError: 42"
    assert oracle_report(path, "measure") == (1, "KeyError: 42")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_division_by_zero_names_the_operation(tmp_path: Path) -> None:
    """CPython 3.13 and earlier name the operation; 3.14 unified them."""

    path = write_case(
        tmp_path,
        "zdiv.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    return 1 // 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    code, report = oracle_report(path, "measure")
    assert code == 1
    assert got.stderr.strip() == report


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_float_division_by_zero_names_the_operation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "fzdiv.py",
        "\n".join(
            [
                "def measure() -> float:",
                "    return 1.0 / 0.0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    code, report = oracle_report(path, "measure")
    assert code == 1
    assert got.stderr.strip() == report


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_the_report_goes_to_stderr_and_stdout_stays_clean(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "clean.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    print('before')",
                '    raise ValueError("boom")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stdout == "before\n"
    assert got.stderr.strip() == "ValueError: boom"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_the_uncaught_report_does_not_print_a_traceback(tmp_path: Path) -> None:
    """The subset has no frames to list, so the report is the one line."""

    path = write_case(
        tmp_path,
        "notraceback.py",
        'def measure() -> int:\n    raise ValueError("boom")\n',
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert "Traceback" not in got.stderr
    assert len(got.stderr.strip().splitlines()) == 1


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_caught_exception_prints_nothing(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "caught.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError as e:",
                "        return str(e)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "boom\n"
    assert got.stderr == ""
    assert got.stdout == oracle(path, "measure")
