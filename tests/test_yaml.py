"""yaml.safe_load lowers through a C YAML-to-JSON pass and the json.loads reader.

The closed subset lives in the header of `src/pyscriptc/runtime/ps_yaml.c`;
runtime parity runs in the Linux container (PyYAML is not a project dep).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

PROGRAM = """import yaml


def mapping() -> int:
    d: dict[str, int] = yaml.safe_load("a: 1\\nb: 2\\nc: 0x10\\nd: 1_000\\ne: -7\\n")
    return d["a"] + d["b"] + d["c"] + d["d"] + d["e"]


def floats() -> float:
    d: dict[str, float] = yaml.safe_load("a: 2.0\\nb: -1.5e+2\\nc: .inf\\nd: -3.25\\n")
    return d["a"] + d["b"] + (d["c"] * 2 if d["d"] < 0 else 0.0)


def nested() -> int:
    d: dict[str, dict[str, int]] = yaml.safe_load("top:\\n  a: 1\\n  b: 2\\nother:\\n  c: 3\\n")
    return d["top"]["a"] * 100 + d["top"]["b"] * 10 + d["other"]["c"]


def seqs() -> int:
    d: dict[str, list[int]] = yaml.safe_load("list:\\n  - 1\\n  - 2\\n  - 3\\n")
    return len(d["list"]) * 100 + d["list"][0] + d["list"][2]


def seqmaps() -> int:
    d: dict[str, list[dict[str, int]]] = yaml.safe_load(
        "items:\\n  - name: 1\\n    n: 5\\n  - name: 2\\n    n: 6\\n"
    )
    return d["items"][0]["n"] * 10 + d["items"][1]["n"]


def flow() -> int:
    d: dict[str, list[int]] = yaml.safe_load("xs: [1, 2, 3]\\n")
    return d["xs"][0] + d["xs"][2]


def flowmap() -> int:
    d: dict[str, dict[str, int]] = yaml.safe_load("m: {x: 4, y: 5}\\n")
    return d["m"]["x"] * 10 + d["m"]["y"]


def strings() -> str:
    d: dict[str, str] = yaml.safe_load(
        "q: \\"double # not\\"\\ns: 'single '' quote'\\np: plain text here\\n"
    )
    return d["q"] + "|" + d["s"] + "|" + d["p"]


def scalars() -> int:
    x: int | None = yaml.safe_load("41")
    if x is None:
        return 0
    return x + 1


def comments() -> int:
    d: dict[str, int] = yaml.safe_load("# lead\\na: 1 # trail\\n\\nb: 2\\n")
    return d["a"] * 10 + d["b"]
"""

FUNCS = [
    "mapping",
    "floats",
    "nested",
    "seqs",
    "seqmaps",
    "flow",
    "flowmap",
    "strings",
    "scalars",
    "comments",
]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@pytest.mark.parametrize("func", FUNCS)
def test_yaml_matches_cpython(tmp_path: Path, func: str) -> None:
    path = write_case(tmp_path, "yaml_case.py", PROGRAM)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


BAD_DOCS = [
    "a: 1\na: 2\n",  # duplicate key at the json.loads layer
    "a: &x 1\nb: *x\n",  # anchors and aliases
    "a: |\\n  block\\n",  # block scalar
    "a: >\\n  folded\\n",  # folded scalar
    "a: !!str 1\\n",  # tags
    "a: 1\\n---\\nb: 2\\n",  # multiple documents
    "? a\\n: 1\\n",  # complex key
    "\\ta: 1\\n",  # tab indentation
    "a: 2026-01-02\\n",  # timestamp value
    "a: 1:30\\n",  # sexagesimal value
    "42: a\\n",  # non-string key
    "a: [1, 2\\n",  # flow collection is not closed
    "a: 'open\\n",  # unterminated quoted scalar
]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@pytest.mark.parametrize("doc", BAD_DOCS)
def test_yaml_outside_the_subset_raises_value_error(tmp_path: Path, doc: str) -> None:
    source = (
        "import yaml\n\n\n"
        "def f(s: str) -> int:\n"
        "    d: dict[str, int] = {}\n"
        "    try:\n"
        "        d = yaml.safe_load(s)\n"
        "    except ValueError:\n"
        "        return 1\n"
        "    return len(d)\n"
    )
    path = write_case(tmp_path, "bad.py", source)
    got = compile_run(tmp_path, path, "f", repr([doc]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_yaml_load_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "load.py",
        "import yaml\n\ndef f(fh: bytes) -> int:\n    yaml.load(fh)  # type: ignore[arg-type]\n    return 1\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "yaml.load" in got.stderr + got.stdout


def test_yaml_dump_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "dump.py",
        "import yaml\n\ndef f(d: dict[str, int]) -> int:\n    yaml.dump(d)  # type: ignore[arg-type]\n    return 1\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "yaml.dump" in got.stderr + got.stdout


def test_yaml_safe_load_needs_the_annotation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "import yaml\n\ndef f() -> int:\n    return len(yaml.safe_load('a: 1'))\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1001" in got.stderr + got.stdout


def test_yaml_bool_result_fences_like_json(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "boolres.py",
        "import yaml\n\ndef f() -> dict[str, bool]:\n    return yaml.safe_load('a: true')\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2002" in got.stderr + got.stdout


def test_yaml_safe_load_lowers(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ok.py",
        "import yaml\n\ndef f() -> int:\n    d: dict[str, int] = yaml.safe_load('a: 1')\n    return d['a']\n",
    )
    got = run_cli("compile", str(path), "--emit", "llvm")
    assert got.returncode == 0, got.stderr
    ll = (tmp_path / "ok.ll").read_text()
    assert "@ps_yaml_to_json" in ll
    assert "@ps_json_loads" in ll


def test_yaml_keyword_arguments_fence(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kw.py",
        "import yaml\n\n\n"
        "def f(s: str) -> int:\n"
        "    d: dict[str, int] = yaml.safe_load(s, Loader=None)  # type: ignore[call-arg]\n"
        "    return len(d)\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2003" in got.stderr + got.stdout
