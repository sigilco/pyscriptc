"""E2 M3: the exception object surface and isinstance.

`except ValueError as e` binds a value that answers `str(e)`, `repr(e)`,
`e.args` and `isinstance(e, SomeError)`. `repr(e)` prints CPython's
`ValueError('boom')`, and `isinstance` walks the same parent chain the
`except` clause uses, so `isinstance(e, LookupError)` is true for a
`KeyError`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_repr_of_exception_is_the_cpython_spelling(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "repr.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError as e:",
                "        return repr(e)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "ValueError('boom')\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_repr_without_a_message_has_no_argument(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "repr_bare.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                "        raise ValueError()",
                "    except ValueError as e:",
                "        return repr(e)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "ValueError()\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_repr_of_a_user_class_names_the_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "repr_user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "",
                "def measure() -> str:",
                "    try:",
                '        raise AppError("nope")',
                "    except AppError as e:",
                "        return repr(e)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "AppError('nope')\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_isinstance_matches_the_raised_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "isinstance.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError as e:",
                "        if isinstance(e, ValueError):",
                '            return "exact"',
                '        return "other"',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "exact\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_isinstance_walks_the_parent_chain(tmp_path: Path) -> None:
    """A `KeyError` is a `LookupError`, so `isinstance(e, LookupError)` is true."""

    path = write_case(
        tmp_path,
        "isinstance_parent.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise KeyError("k")',
                "    except LookupError as e:",
                "        if isinstance(e, LookupError):",
                '            return "parent"',
                '        return "no"',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "parent\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_isinstance_is_false_for_a_sibling_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "isinstance_sibling.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise KeyError("k")',
                "    except LookupError as e:",
                "        if isinstance(e, IndexError):",
                '            return "sibling"',
                '        return "no"',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "no\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_isinstance_of_a_user_class_walks_to_exception(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "isinstance_user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "",
                "def measure() -> str:",
                "    try:",
                '        raise AppError("nope")',
                "    except AppError as e:",
                "        if isinstance(e, Exception):",
                '            return "exception"',
                '        return "no"',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "exception\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_args_holds_the_message(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "args.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError as e:",
                "        return e.args[0]",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "boom\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_type_name_gives_the_runtime_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "type_name.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise KeyError("k")',
                "    except KeyError as e:",
                "        return type(e).__name__",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "KeyError\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_type_name_of_a_user_class_names_the_class(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "type_name_user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "",
                "def measure() -> str:",
                "    try:",
                '        raise AppError("nope")',
                "    except AppError as e:",
                "        return type(e).__name__",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "AppError\n"
    assert got.stdout == oracle(path, "measure")


def test_the_handler_binding_is_deleted_at_the_handler_end(tmp_path: Path) -> None:
    """CPython deletes `e` when the handler ends; the subset fences the read."""
    path = write_case(
        tmp_path,
        "deleted.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("boom")',
                "    except ValueError as e:",
                "        pass",
                "    return str(e)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr
    assert "deleted by the except handler" in got.stderr
