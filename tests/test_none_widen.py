# SPDX-License-Identifier: Apache-2.0
"""Issue #1370: a None-initialised local widens to `T | None`.

A local whose first binding is a bare `None` used to fence with PS1001,
because a bare `None` names no type on its own. The later typed bindings
in the same body do name one: when every other plain `name = v` binding
lowers to one and the same type `T`, the local is `T | None`, the
value-level reading CPython gives the idiom.

The None itself never names a type, so the fence stands when no other
binding proves one, when two later bindings disagree, and when a later
binding the peek cannot lower says nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source

PROBE = Path(__file__).resolve().parents[1] / "probes" / "differential" / "none_widened_local.py"


def _fences(source: str) -> list[tuple[str, str]]:
    return [(f["code"], f["message"]) for f in lower_source(source, "case.py")["fences"]]


def _codes(source: str) -> list[str]:
    return [code for code, _ in _fences(source)]


def test_a_none_first_local_takes_the_type_its_later_binding_proves() -> None:
    source = (
        "def f(lines: list[str]) -> str | None:\n"
        "    section = None\n"
        "    for line in lines:\n"
        '        if line.startswith("["):\n'
        "            section = line[1:]\n"
        "    return section\n"
    )
    assert _fences(source) == []


def test_the_widening_holds_when_every_later_binding_agrees() -> None:
    source = (
        "def f(flag: bool) -> str | None:\n"
        "    section = None\n"
        "    if flag:\n"
        '        section = "a"\n'
        "    else:\n"
        '        section = "b"\n'
        "    return section\n"
    )
    assert _fences(source) == []


def test_a_none_with_no_other_binding_still_fences() -> None:
    # The None names no type on its own: the fence stands.
    source = "def f() -> None:\n    x = None\n    print(x)\n"
    assert "PS1001" in _codes(source)


def test_two_later_bindings_that_disagree_still_fence() -> None:
    source = "def f() -> None:\n    x = None\n    x = 1\n    x = 'a'\n"
    assert "PS1001" in _codes(source)


def test_a_later_binding_the_peek_cannot_lower_still_fences() -> None:
    # A value the compiler cannot type says nothing, so the proof fails.
    source = "def f() -> None:\n    x = None\n    x = lambda: 1\n    x = 1\n"
    assert "PS1001" in _codes(source)


def test_a_none_after_a_concrete_binding_does_not_widen() -> None:
    # The None is not the first binding: the local already proved its type,
    # so the None is a rebind of a live value, not the not-seen-yet state.
    source = "def f() -> None:\n    x = 1\n    x = None\n    print(x)\n"
    assert _codes(source) != []


def test_an_ifexp_with_a_none_arm_is_the_optional_itself() -> None:
    # `x if c else None` is `T | None` on its own, with no widening needed.
    source = "def f(c: bool) -> None:\n    x = 1 if c else None\n    print(x)\n"
    assert _codes(source) == ["PS2003"]


def test_the_probe_compiles_and_matches_cpython(tmp_path: Path) -> None:
    """The differential probe runs next to the pinned CPython and matches."""
    out = str(tmp_path / "none_widened_local")
    got = run_cli("compile", str(PROBE), "--run", "--emit", "bin", "-o", out)
    assert got.returncode == 0, got.stderr
    want = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(PROBE)], capture_output=True, text=True, check=True
    )
    assert got.stdout == want.stdout
    assert got.returncode == want.returncode


def test_the_probe_source_is_the_committed_one(tmp_path: Path) -> None:
    # The probe is the differential lane's input, so it must exist and carry
    # the shape the issue names.
    assert PROBE.is_file()
    source = PROBE.read_text(encoding="utf-8")
    assert "section = None" in source


def test_a_widened_local_reads_as_its_optional(tmp_path: Path) -> None:
    """A narrowed read of the widened local is the optional's value."""
    program = (
        "def f(flag: bool) -> str | None:\n"
        "    section = None\n"
        "    if flag:\n"
        '        section = "a"\n'
        "    if section is None:\n"
        '        return "none"\n'
        "    return section\n"
        "\n"
        "def main() -> None:\n"
        '    print(f(True) or "-")\n'
        '    print(f(False) or "-")\n'
        "\n"
        "main()\n"
    )
    path = write_case(tmp_path, "widened_read.py", program)
    out = str(tmp_path / "widened_read")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    assert got.returncode == 0, got.stderr
    want = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(path)], capture_output=True, text=True, check=True
    )
    assert got.stdout == want.stdout
