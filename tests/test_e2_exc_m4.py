"""E2 M4: `try...else` and `try...finally` ordering rules.

The lowering shipped with R9 (#84): `else` runs only when the body fell
through with no exception and no non-local exit, `finally` runs on every
outcome, and a raise or return inside `finally` replaces the in-flight
outcome. These tests pin those rules against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_full_ordering_fallthrough_raises_and_returns(tmp_path: Path) -> None:
    """One log per arm: fallthrough runs try+else+finally; a raise runs
    except+finally and skips else; a return skips else but runs finally."""

    path = write_case(
        tmp_path,
        "order.py",
        "\n".join(
            [
                "def via_return() -> str:",
                "    try:",
                '        return "ret"',
                "    finally:",
                '        print("fin-r")',
                "",
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        log = log + "try"',
                "    except ValueError:",
                '        log = log + "|except"',
                "    else:",
                '        log = log + "|else"',
                "    finally:",
                '        log = log + "|finally"',
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                '        log = log + "|except2"',
                "    else:",
                '        log = log + "|else2"',
                "    finally:",
                '        log = log + "|finally2"',
                '    return log + "|" + via_return()',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "fin-r\ntry|else|finally|except2|finally2|ret\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_else_runs_only_on_a_clean_fallthrough(tmp_path: Path) -> None:
    """The body fell through with no exception: else runs, then finally."""

    path = write_case(
        tmp_path,
        "else.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        log = log + "try"',
                "    except ValueError:",
                '        log = log + "|except"',
                "    else:",
                '        log = log + "|else"',
                "    finally:",
                '        log = log + "|finally"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "try|else|finally\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_else_is_skipped_when_the_body_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "else_raise.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                '        log = log + "|except"',
                "    else:",
                '        log = log + "|else"',
                "    finally:",
                '        log = log + "|finally"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "|except|finally\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_else_is_skipped_when_the_body_returns(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "else_return.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        return "ret"',
                "    except ValueError:",
                '        log = log + "|except"',
                "    else:",
                '        log = log + "|else"',
                "    finally:",
                '        log = log + "|finally"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "ret\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_finally_runs_before_the_unhandled_exception_escapes(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "escapes.py",
        'def f() -> int:\n    try:\n        raise KeyError("escapes")\n    finally:\n        print("fin")\n',
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1, got.stderr
    assert got.stdout == "fin\n"
    assert "KeyError: 'escapes'" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_finally_around_break_and_continue(tmp_path: Path) -> None:
    """finally wraps each non-local exit; else is skipped when the body jumps."""

    path = write_case(
        tmp_path,
        "loops.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    for i in range(4):",
                "        try:",
                "            if i == 2:",
                "                continue",
                "            if i == 3:",
                "                break",
                "            log = log + str(i)",
                "        except ValueError:",
                '            log = log + "|exc"',
                "        else:",
                '            log = log + "|else"',
                "        finally:",
                '            log = log + "|fin"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "0|else|fin1|else|fin|fin|fin\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_nested_finally_with_inner_catch(tmp_path: Path) -> None:
    """An inner finally runs while the exception is on its way to the outer catch."""

    path = write_case(
        tmp_path,
        "nested.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                "        try:",
                '            raise ValueError("inner")',
                "        finally:",
                '            log = log + "fin1"',
                "    except ValueError as e:",
                '        log = log + "|caught:" + str(e)',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "fin1|caught:inner\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_raise_inside_finally_replaces_the_in_flight_exception(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "replace.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                "        try:",
                '            raise ValueError("first")',
                "        finally:",
                '            raise RuntimeError("second")',
                "    except RuntimeError as e:",
                "        print(str(e))",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "second\n1\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_handler_chains_through_finally(tmp_path: Path) -> None:
    """A caught inner exception does not stop the outer finally from running."""

    path = write_case(
        tmp_path,
        "chain.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        raise ValueError("a")',
                "    except ValueError:",
                "        try:",
                '            raise KeyError("b")',
                "        except KeyError:",
                '            log = log + "inner"',
                '        log = log + "|outer"',
                "    finally:",
                '        log = log + "|fin"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "inner|outer|fin\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_try_inside_finally_nests_normally(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nested_in_fin.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    try:",
                '        raise ValueError("v")',
                "    finally:",
                "        try:",
                '            raise KeyError("k")',
                "        except KeyError:",
                '            print("fin-exc")',
                '        print("fin")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert got.stdout == "fin-exc\nfin\n"
    assert "ValueError: v" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_finally_sees_the_state_the_handler_left(tmp_path: Path) -> None:
    """A handler's assignments are visible to the finally block."""

    path = write_case(
        tmp_path,
        "state.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                '        log = log + "caught"',
                "    finally:",
                '        log = log + "|fin"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "caught|fin\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_nested_try_finally_chain_runs_both_finallys(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "chain2.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                "        try:",
                '            raise ValueError("a")',
                "        except ValueError:",
                '            log = log + "inner"',
                "        finally:",
                '            log = log + "|fin1"',
                "    except ValueError:",
                '        log = log + "|outer"',
                "    finally:",
                '        log = log + "|fin2"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "inner|fin1|fin2\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_bare_except_still_runs_the_finally(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "\n".join(
            [
                "def measure() -> str:",
                "    log: str = ''",
                "    try:",
                '        raise ValueError("v")',
                "    except:",
                '        log = log + "|bare"',
                "    finally:",
                '        log = log + "|fin"',
                "    return log",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "|bare|fin\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_return_inside_finally_replaces_the_in_flight_exception(tmp_path: Path) -> None:
    """CPython lets a `return` in `finally` swallow the exception.

    The pyrefly preflight flags this as PEP 765 syntax (an error in 3.15),
    so the case runs with `--skip-check`; the subset targets the 3.12
    window where it is legal.
    """

    path = write_case(
        tmp_path,
        "ret_in_fin.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("first")',
                "    finally:",
                "        return 7",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_break_inside_finally_leaves_the_loop(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "break_in_fin.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    for i in range(3):",
                "        try:",
                "            pass",
                "        finally:",
                "            break",
                "    return 5",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "5\n"
    assert got.stdout == oracle(path, "measure")
