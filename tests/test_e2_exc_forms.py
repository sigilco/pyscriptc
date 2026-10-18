"""E2 R8+R9: exception forms — `except E as e`, bare except, bare raise,
raise-from, and `try...else` / `try...finally`.

`except E as e` binds the PsExc for the handler suite; `str(e)` is the
message. CPython deletes the bound name at handler end, so post-handler
reads are a lowering fence here, matching `del e`. `raise E from cause`
stores a real cause link; the uncaught report prints the cause first with
CPython's "direct cause" line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_except_as_binds_exception_and_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bind.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                '        raise ValueError("bad input")',
                "    except ValueError as e:",
                "        print(str(e))",
                "        print(len(e.args))",
                "        print(e.args[0])",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "bad input\n1\nbad input\n1\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_bare_except_and_bare_reraise(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "\n".join(
            [
                "def inner() -> int:",
                "    try:",
                '        raise ValueError("deep")',
                "    except:",
                "        raise",
                "",
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                "        inner()",
                "    except ValueError as e:",
                "        print(str(e))",
                "        caught = 1",
                "    try:",
                '        raise KeyError("k")',
                "    except:",
                "        caught = caught + 2",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "deep\n3\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_raise_from_caught_shows_outer(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "chain.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                "        try:",
                '            raise ValueError("inner")',
                "        except ValueError as e:",
                '            raise RuntimeError("outer") from e',
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
    assert got.stdout == "outer\n1\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_raise_from_uncaught_reports_cause(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "chain2.py",
        (
            "def f() -> int:\n"
            "    try:\n"
            '        raise ValueError("inner")\n'
            "    except ValueError as e:\n"
            '        raise RuntimeError("outer") from e\n'
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1, got.stderr
    assert "ValueError: inner" in got.stderr
    assert "The above exception was the direct cause of the following exception." in got.stderr
    assert "RuntimeError: outer" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_non_str_arg_ctor_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "args.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    try:",
                "        raise RuntimeError(2.5)",
                "    except RuntimeError as e:",
                "        print(str(e))",
                "    try:",
                "        raise ValueError(7)",
                "    except ValueError as e:",
                "        print(str(e))",
                "    try:",
                "        raise RuntimeError(True)",
                "    except RuntimeError as e:",
                "        print(str(e))",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    # str(arg) parity: "2.5", "7", "True" (not C repr "1").
    assert got.stdout == "2.5\n7\nTrue\n1\n"
    assert got.stdout == oracle(path, "measure")


def test_multi_arg_ctor_fences() -> None:
    # Cheap choice per R8: `E(*args)` with >1 arg fences (repr(tuple) is not
    # in the subset), matching other not-lowered forms.
    from pyscriptc.lower import lower_file

    path = write_case(
        Path("/tmp"),
        "multi_raise.py",
        'def measure() -> int:\n    raise ValueError("a", 2)\n',
    )
    module, _source = lower_file(str(path))
    assert any(fence["code"] == "PS2003" for fence in module["fences"])


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_try_else_runs_only_without_exception(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "orelse.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    hit: int = 0",
                "    try:",
                "        hit = hit + 1",
                "    except ValueError:",
                "        hit = 100",
                "    else:",
                "        hit = hit + 2",
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                "        hit = hit + 4",
                "    else:",
                "        hit = 1000",
                "    return hit",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_finally_fallthrough_handled_and_return(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "fin.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    tag: int = 0",
                "    try:",
                "        tag = tag + 1",
                "    finally:",
                '        print("fin-a")',
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                "        tag = tag + 2",
                "    finally:",
                '        print("fin-b")',
                "    try:",
                "        return tag",
                "    finally:",
                '        print("fin-c")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "fin-a\nfin-b\nfin-c\n3\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_finally_on_break_and_continue(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "finloop.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    n: int = 0",
                "    while n < 3:",
                "        n = n + 1",
                "        try:",
                "            if n == 2:",
                "                continue",
                "            if n == 3:",
                "                break",
                "            print(n)",
                "        finally:",
                '            print("fin")',
                "    return n",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\nfin\nfin\nfin\n3\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_finally_runs_before_unhandled_propagates(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "finboom.py",
        ('def f() -> int:\n    try:\n        raise ValueError("escapes")\n    finally:\n        print("fin")\n'),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1, got.stderr
    assert got.stdout == "fin\n"
    assert "ValueError: escapes" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_raise_inside_finally_replaces(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "finraise.py",
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


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_else_and_finally_together(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "elf.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    out: int = 0",
                "    try:",
                '        raise ValueError("v")',
                "    except ValueError:",
                "        out = 1",
                "    else:",
                "        out = 100",
                "    finally:",
                "        out = out + 10",
                "    try:",
                "        out = out + 1",
                "    except ValueError:",
                "        out = 100",
                "    else:",
                "        out = out + 100",
                "    finally:",
                "        out = out + 1000",
                "    return out",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1112\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_else_skipped_when_try_breaks(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "elsebrk.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    n: int = 0",
                "    hits: int = 0",
                "    while n < 3:",
                "        n = n + 1",
                "        try:",
                "            if n == 2:",
                "                break",
                "            hits = hits + 1",
                "        except ValueError:",
                "            hits = 100",
                "        else:",
                "            hits = hits + 10",
                "    return hits",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "11\n"
    assert got.stdout == oracle(path, "measure")
