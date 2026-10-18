"""Real-Python backlog R26: ``T | None`` as a tagged optional value.

Annotations ``int | None`` / ``Optional[T]`` lower to a synthetic ``opt_*``
heap cell (`_tag` flag + `_val` payload, allocated zeroed so a fresh cell is
None). ``x is None`` / ``is not None`` narrow a local to the payload type for
the remainder of the body; ``os.getenv(k)`` and ``dict.get(k)`` (one-arg
forms) return the tagged value. Each probe diffs stdout against the CPython
oracle element-for-element.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_r26_optional_param_and_narrowing(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "opt_param.py",
        "\n".join(
            [
                "def check(x: int | None) -> int:",
                "    if x is None:",
                "        return -1",
                "    return x + 1",
                "",
                "def run() -> int:",
                "    print(check(None))",
                "    print(check(41))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r26_typing_optional_and_is_not_none(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "opt_typing.py",
        "\n".join(
            [
                "from typing import Optional",
                "",
                "def shout(x: Optional[str]) -> int:",
                "    if x is not None:",
                "        return len(x) + 1",
                "    return 0",
                "",
                "def run() -> int:",
                "    print(shout('abcd'))",
                "    print(shout(None))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r26_getenv_one_arg(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "envget.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    home: str | None = os.getenv('HOME')",
                "    if home is not None:",
                "        print(len(home))",
                "    else:",
                "        print(-1)",
                "    missing: str | None = os.getenv('PYSC_R26_MISSING_XYZ')",
                "    if missing is None:",
                "        print(7)",
                "    print(missing or 'fallback')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r26_dict_get_one_arg(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "dictget.py",
        "\n".join(
            [
                "def run() -> int:",
                "    d: dict[str, int] = {'a': 3}",
                "    hit: int | None = d.get('a')",
                "    if hit is not None:",
                "        print(hit + 10)",
                "    miss: int | None = d.get('zz')",
                "    if miss is None:",
                "        print(99)",
                "    again: int | None = d.get('b', 5)",
                "    print(again or 0)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r26_optional_default_and_reassign(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "opt_default.py",
        "\n".join(
            [
                "def bump(x: int | None = None) -> int:",
                "    if x is None:",
                "        return 0",
                "    return x * 2",
                "",
                "def run() -> int:",
                "    print(bump())",
                "    print(bump(4))",
                "    e: int | None = None",
                "    e = 6",
                "    if e is not None:",
                "        print(e)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r26_union_of_two_values_still_fences(tmp_path: Path) -> None:
    path = write_case(tmp_path, "union2.py", "def f(x: int | str) -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "u.ir.json"))
    assert got.returncode == 3
    assert "union types are not in the IR" in got.stderr
