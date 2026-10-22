"""tomllib.loads lowers through a C TOML-to-JSON pass and the json.loads reader."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

PROGRAM = """import tomllib


def ints() -> int:
    d: dict[str, int] = tomllib.loads("a = 1\\nb = 0x10\\nc = 0o17\\nd = 0b101\\ne = 1_000\\nf = -7\\n")
    return d["a"] + d["b"] + d["c"] + d["d"] + d["e"] + d["f"]


def floats() -> float:
    d: dict[str, float] = tomllib.loads("a = 2.0\\nb = -1.5e2\\nc = 1_0.5\\nd = 1e3\\n")
    return d["a"] + d["b"] + d["c"] + d["d"]


def special() -> float:
    d: dict[str, float] = tomllib.loads("a = inf\\nb = -inf\\n")
    return d["a"] + 1.0 if d["b"] < 0.0 else 0.0


def arrays() -> int:
    d: dict[str, list[int]] = tomllib.loads("a = [1, 2,\\n  3, # c\\n]\\nb = []\\n")
    return len(d["a"]) * 10 + len(d["b"]) + d["a"][2]


def tables() -> str:
    d: dict[str, dict[str, str]] = tomllib.loads(
        '[t]\\nk = "v"\\n[v]\\n"q r" = "w"\\n[w]\\nx = \\'lit\\\\n\\'\\n'
    )
    return d["t"]["k"] + d["v"]["q r"] + d["w"]["x"]


def dotted() -> int:
    d: dict[str, dict[str, dict[str, int]]] = tomllib.loads("[a.b]\\nx = 1\\n[a.c]\\nx = 2\\n")
    return d["a"]["b"]["x"] * 10 + d["a"]["c"]["x"]


def aot() -> int:
    d: dict[str, list[dict[str, int]]] = tomllib.loads("[[p]]\\nx = 1\\n[[p]]\\nx = 2\\n")
    return d["p"][0]["x"] * 10 + d["p"][1]["x"]


def inline() -> int:
    d: dict[str, dict[str, int]] = tomllib.loads("p = { x = 3, y = 4 }\\nq = {}\\n")
    return d["p"]["x"] * 10 + d["p"]["y"] + len(d["q"])


def multiline() -> str:
    q3: str = chr(34) + chr(34) + chr(34)
    s3: str = chr(39) + chr(39) + chr(39)
    bs: str = chr(92)
    nl: str = chr(10)
    tab: str = chr(9)
    doc: str = (
        "a = " + q3 + nl + "l1" + bs + nl + "   l2" + q3 + nl
        + "b = " + s3 + nl + "raw" + bs + "n" + nl + s3 + nl
        + "c = " + chr(34) + "t" + bs + "t" + bs + chr(34) + "s" + chr(34) + nl
    )
    d: dict[str, str] = tomllib.loads(doc)
    return d["a"] + "|" + d["b"] + "|" + d["c"]


def dottedkeys() -> int:
    d: dict[str, dict[str, int]] = tomllib.loads("a.b = 1\\na.c = 2\\n")
    return d["a"]["b"] * 10 + d["a"]["c"]
"""

FUNCS = [
    "ints",
    "floats",
    "special",
    "arrays",
    "tables",
    "dotted",
    "aot",
    "inline",
    "multiline",
    "dottedkeys",
]


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
@pytest.mark.parametrize("func", FUNCS)
def test_tomllib_matches_cpython(tmp_path: Path, func: str) -> None:
    path = write_case(tmp_path, "toml_case.py", PROGRAM)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


BAD_DOCS = [
    "a = 1\na = 2\n",
    "[t]\n[t]\n",
    "a = 1979-05-27\n",
    "a = 07:32:00\n",
    'a = "open\n',
    "a = 01\n",
    "a = \n",
    "a = [1, 2\n",
    "a = 1 b = 2\n",
    "a = 99999999999999999999\n",
    "a.b = 1\na = 2\n",
    "[[p]]\n[p]\n",
]


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
@pytest.mark.parametrize("doc", BAD_DOCS)
def test_tomllib_bad_documents_raise_value_error(tmp_path: Path, doc: str) -> None:
    source = (
        "import tomllib\n\n\n"
        "def f(s: str) -> int:\n"
        "    d: dict[str, int] = {}\n"
        "    try:\n"
        "        d = tomllib.loads(s)\n"
        "    except ValueError:\n"
        "        return 1\n"
        "    return len(d)\n"
    )
    path = write_case(tmp_path, "bad.py", source)
    got = compile_run(tmp_path, path, "f", repr([doc]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_tomllib_load_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "load.py",
        "import tomllib\n\ndef f(fh: bytes) -> int:\n    tomllib.load(fh)  # type: ignore[arg-type]\n    return 1\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "tomllib.load" in got.stderr + got.stdout


def test_tomllib_loads_needs_the_annotation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "import tomllib\n\ndef f() -> int:\n    return len(tomllib.loads('a = 1'))\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1001" in got.stderr + got.stdout


def test_tomllib_loads_lowers(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ok.py",
        "import tomllib\n\ndef f() -> int:\n    d: dict[str, int] = tomllib.loads('a = 1')\n    return d['a']\n",
    )
    got = run_cli("compile", str(path), "--emit", "llvm")
    assert got.returncode == 0, got.stderr
    ll = (tmp_path / "ok.ll").read_text()
    assert "@ps_toml_to_json" in ll
    assert "@ps_json_loads" in ll
