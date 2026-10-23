"""The pyscriptc-orjson pack: orjson.dumps and orjson.loads over ps_json.

orjson is not vendored in the compat corpus, so the parity reference is the
documented behaviour of orjson itself: `dumps` returns bytes with no space
after the separators, `OPT_SORT_KEYS` sorts the dict keys and `OPT_INDENT_2`
puts every item on its own line with a two-space pad.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

IMPORT = "import orjson\n\n\n"


def _run(tmp_path: Path, name: str, body: str, args: str = "[]") -> str:
    path = write_case(tmp_path, name, IMPORT + body)
    got = run_cli("compile", str(path), "--run", "--call", name, "--args", args, "-o", str(tmp_path / name))
    assert got.returncode == 0, got.stderr
    return got.stdout


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_compact(tmp_path: Path) -> None:
    body = "def dumps_compact() -> str:\n    data: bytes = orjson.dumps({'b': 2, 'a': 1})\n    return data.decode()\n"
    assert _run(tmp_path, "dumps_compact", body) == '{"b":2,"a":1}\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_sorted(tmp_path: Path) -> None:
    body = (
        "def dumps_sorted() -> str:\n"
        "    data: bytes = orjson.dumps({'b': 2, 'a': 1}, option=orjson.OPT_SORT_KEYS)\n"
        "    return data.decode()\n"
    )
    assert _run(tmp_path, "dumps_sorted", body) == '{"a":1,"b":2}\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_indent(tmp_path: Path) -> None:
    body = (
        "def dumps_indent() -> str:\n"
        "    data: bytes = orjson.dumps({'b': 2, 'a': 1}, option=orjson.OPT_INDENT_2)\n"
        "    return data.decode()\n"
    )
    assert _run(tmp_path, "dumps_indent", body) == '{\n  "b": 2,\n  "a": 1\n}\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_both_options(tmp_path: Path) -> None:
    body = (
        "def dumps_both() -> str:\n"
        "    data: bytes = orjson.dumps({'b': 2, 'a': 1}, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)\n"
        "    return data.decode()\n"
    )
    assert _run(tmp_path, "dumps_both", body) == '{\n  "a": 1,\n  "b": 2\n}\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_list(tmp_path: Path) -> None:
    body = "def dumps_list() -> str:\n    data: bytes = orjson.dumps([1, 2, 3])\n    return data.decode()\n"
    assert _run(tmp_path, "dumps_list", body) == "[1,2,3]\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_dumps_nested(tmp_path: Path) -> None:
    body = "def dumps_nested() -> str:\n    data: bytes = orjson.dumps({'a': [1, 2]})\n    return data.decode()\n"
    assert _run(tmp_path, "dumps_nested", body) == '{"a":[1,2]}\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_loads_str(tmp_path: Path) -> None:
    body = (
        "def loads_str() -> int:\n    value: dict[str, int] = orjson.loads('{\"a\": 41}')\n    return value['a'] + 1\n"
    )
    assert _run(tmp_path, "loads_str", body) == "42\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_loads_bytes(tmp_path: Path) -> None:
    body = (
        "def loads_bytes() -> int:\n"
        "    value: dict[str, int] = orjson.loads(b'{\"a\": 40}')\n"
        "    return value['a'] + 2\n"
    )
    assert _run(tmp_path, "loads_bytes", body) == "42\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_round_trip(tmp_path: Path) -> None:
    body = (
        "def round_trip() -> int:\n"
        "    data: bytes = orjson.dumps({'k': [10, 20]})\n"
        "    value: dict[str, list[int]] = orjson.loads(data)\n"
        "    return value['k'][0] + value['k'][1]\n"
    )
    assert _run(tmp_path, "round_trip", body) == "30\n"


def test_default_stays_fenced(tmp_path: Path) -> None:
    body = "def f() -> bytes:\n    data: bytes = orjson.dumps({'a': 1}, default=str)\n    return data\n"
    path = write_case(tmp_path, "f.py", IMPORT + body)
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2003" in got.stderr + got.stdout


def test_unknown_option_stays_fenced(tmp_path: Path) -> None:
    body = "def f() -> bytes:\n    data: bytes = orjson.dumps({'a': 1}, option=orjson.OPT_NAIVE_UTC)\n    return data\n"
    path = write_case(tmp_path, "f.py", IMPORT + body)
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2003" in got.stderr + got.stdout


def test_other_members_stay_fenced(tmp_path: Path) -> None:
    body = "def f() -> bytes:\n    return orjson.Fragment(b'x')\n"
    path = write_case(tmp_path, "f.py", IMPORT + body)
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "orjson.Fragment" in got.stderr + got.stdout


def test_loads_needs_the_annotation(tmp_path: Path) -> None:
    body = "def f() -> int:\n    return len(orjson.loads('{}'))\n"
    path = write_case(tmp_path, "f.py", IMPORT + body)
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1001" in got.stderr + got.stdout


def test_lowers_onto_the_json_symbols(tmp_path: Path) -> None:
    body = (
        "def dumps_compact() -> bytes:\n    data: bytes = orjson.dumps({'b': 2})\n    return data\n\n\n"
        "def loads_str() -> dict[str, int]:\n    value: dict[str, int] = orjson.loads('{\"a\": 1}')\n    return value\n"
    )
    path = write_case(tmp_path, "ok.py", IMPORT + body)
    got = run_cli("compile", str(path), "--emit", "llvm", "--skip-check", "-o", str(tmp_path / "ok.ll"))
    assert got.returncode == 0, got.stderr
    ll = (tmp_path / "ok.ll").read_text()
    assert "@ps_json_dumps" in ll
    assert "@ps_json_loads" in ll
