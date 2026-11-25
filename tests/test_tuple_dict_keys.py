# SPDX-License-Identifier: Apache-2.0
"""A tuple as a dict key (issue #1368).

`dict[tuple[str, str | None], int]` is the shape iniconfig 2.3.1's
`_parse.py` uses for its `_sources` map, and it fences on the pilot-2
lowering slice (#417).

The key hashes and compares field-wise, the way CPython hashes and compares
a tuple: `(section, None)` equals another `(section, None)`, and
`(section, "x")` is a different key. The three sites the issue names are the
item store, the read and the membership test; `dict.get` reads the same
way.

The map holds the key as a tuple-key record: the field count, the per-field
kinds and a flat payload buffer. The compiler packs the fields (it knows
each field's static type) and the runtime hashes and compares them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

from pyscriptc.lower import lower_source


def test_a_tuple_key_annotates_clean() -> None:
    module = lower_source(
        "def probe() -> int:\n"
        "    sources: dict[tuple[str, str | None], int] = {}\n"
        "    sources['a', None] = 1\n"
        "    return sources['a', None]\n",
        "case.py",
    )
    assert module["fences"] == [], module["fences"]


def test_a_tuple_key_of_an_unsupported_field_fences() -> None:
    """A field the tuple-key record cannot carry stays fenced."""
    module = lower_source(
        "def probe() -> int:\n    sources: dict[tuple[str, list[int]], int] = {}\n    return 0\n",
        "case.py",
    )
    assert any(fence["code"] == "PS2002" for fence in module["fences"]), module["fences"]


def test_the_store_the_read_and_the_membership_test(tmp_path: Path) -> None:
    source = (
        "def probe() -> str:\n"
        "    sources: dict[tuple[str, str | None], int] = {}\n"
        "    sources['alpha', 'one'] = 1\n"
        "    sources['alpha', None] = 2\n"
        "    sources['beta', 'two'] = 3\n"
        "    out: str = ''\n"
        "    out += str(sources['alpha', 'one'])\n"
        "    out += str(sources['alpha', None])\n"
        "    out += str(sources['beta', 'two'])\n"
        "    out += str(('alpha', 'one') in sources)\n"
        "    out += str(('alpha', None) in sources)\n"
        "    out += str(('alpha', 'two') in sources)\n"
        "    out += str(('beta', None) in sources)\n"
        "    out += str(sources.get(('alpha', None), -1))\n"
        "    out += str(sources.get(('alpha', 'two'), -1))\n"
        "    return out\n"
    )
    path = write_case(tmp_path, "case.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "123TrueTrueFalseFalse2-1\n", got.stdout


def test_the_absent_arm_and_the_present_arm_are_different_keys(tmp_path: Path) -> None:
    """`(s, None)` and `(s, "x")` are two keys, not one."""
    source = (
        "def probe() -> str:\n"
        "    sources: dict[tuple[str, str | None], int] = {}\n"
        "    sources['a', None] = 1\n"
        "    sources['a', 'x'] = 2\n"
        "    out: str = ''\n"
        "    out += str(sources['a', None])\n"
        "    out += str(sources['a', 'x'])\n"
        "    out += str(('a', None) in sources)\n"
        "    out += str(('a', 'x') in sources)\n"
        "    return out\n"
    )
    path = write_case(tmp_path, "case.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "12TrueTrue\n", got.stdout


def test_a_key_read_out_of_one_map_finds_the_entry_in_another(tmp_path: Path) -> None:
    """The keys are compared by value, not by the record's address."""
    source = (
        "def probe() -> str:\n"
        "    first: dict[tuple[str, str | None], int] = {}\n"
        "    first['a', None] = 1\n"
        "    second: dict[tuple[str, str | None], int] = {}\n"
        "    second['a', None] = 2\n"
        "    out: str = ''\n"
        "    out += str(second[('a', None)])\n"
        "    out += str(first[('a', None)])\n"
        "    return out\n"
    )
    path = write_case(tmp_path, "case.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "21\n", got.stdout


def test_the_missing_key_message_is_the_tuples_repr(tmp_path: Path) -> None:
    """A missing tuple key raises the KeyError CPython raises."""
    source = (
        "def probe() -> str:\n"
        "    sources: dict[tuple[str, str | None], int] = {}\n"
        "    try:\n"
        "        value: int = sources['gamma', 'one']\n"
        "    except KeyError as err:\n"
        "        return str(err)\n"
        "    return ''\n"
    )
    path = write_case(tmp_path, "case.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "('gamma', 'one')\n", got.stdout


def test_an_int_field_key(tmp_path: Path) -> None:
    """A tuple key may mix the field shapes the record carries."""
    source = (
        "def probe() -> str:\n"
        "    table: dict[tuple[int, str], int] = {}\n"
        "    table[1, 'a'] = 10\n"
        "    table[2, 'b'] = 20\n"
        "    out: str = ''\n"
        "    out += str(table[1, 'a'])\n"
        "    out += str(table[2, 'b'])\n"
        "    out += str((1, 'a') in table)\n"
        "    out += str((3, 'c') in table)\n"
        "    return out\n"
    )
    path = write_case(tmp_path, "case.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1020TrueFalse\n", got.stdout


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/tuple_dict_keys.py, the AGENTS.md rule)."""
    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "tuple_dict_keys.py"
    out = str(tmp_path / "probe.bin")
    got = run_cli("compile", str(probe), "--skip-check", "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
