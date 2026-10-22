"""E2 M1: PsExc record + ps_exc_isa parent matching.

`raise ValueError` must be caught by `except ValueError` (name) and by
`except Exception` (parent), and a non-matching handler must re-raise.
User exception classes (codes >= USER_EXC_BASE) still match
`except Exception` and their own class.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_value_error_caught_by_name_and_parent(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "exc.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise ValueError("a")',
                "    except ValueError:",
                "        caught = caught + 1",
                "    try:",
                '        raise ValueError("b")',
                "    except Exception:",
                "        caught = caught + 2",
                "    try:",
                "        try:",
                '            raise ValueError("c")',
                "        except KeyError:",
                "            caught = caught + 100",
                "    except ValueError:",
                "        caught = caught + 4",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_user_exception_still_matches_exception(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "class Bad(ValueError):",
                "    pass",
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise AppError("no")',
                "    except Exception:",
                "        caught = caught + 1",
                "    try:",
                '        raise Bad("z")',
                "    except Exception:",
                "        caught = caught + 2",
                "    try:",
                '        raise Bad("z")',
                "    except ValueError:",
                "        caught = caught + 4",
                "    try:",
                "        try:",
                '            raise AppError("x")',
                "        except ValueError:",
                "            caught = caught + 100",
                "    except Exception:",
                "        caught = caught + 8",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_uncaught_reports_name_and_message(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "boom.py",
        (
            "def f() -> int:\n"
            "    try:\n"
            '        raise ValueError("bad input")\n'
            "    except KeyError:\n"
            "        return 0\n"
            "    return 1\n"
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1, got.stderr
    assert "ValueError: bad input" in got.stderr
