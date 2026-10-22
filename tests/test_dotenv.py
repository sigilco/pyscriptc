"""dotenv.dotenv_values / dotenv.load_dotenv lower through a C dotenv-to-JSON pass.

The parsing reference is python-dotenv itself (`benchmarks/compat/top100/src/dotenv`,
pinned 1.2.4); the expectations here are written out against its behavior, so
the test does not import the library.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

PROGRAM = """import dotenv
import os


def values(d: str) -> int:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    return len(v)


def unquoted(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["A"]
    if got is None:
        return "MISS"
    return got


def quoted(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["B"]
    if got is None:
        return "MISS"
    return got


def none_value(d: str) -> int:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["E"]
    return 1 if got is None else 0


def comment_cut(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["C"]
    if got is None:
        return "MISS"
    return got


def hash_kept(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["Z"]
    if got is None:
        return "MISS"
    return got


def escape(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["X"]
    if got is None:
        return "MISS"
    return got


def literal_escape(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["K"]
    if got is None:
        return "MISS"
    return got


def empty_after_hash(d: str) -> str:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    got: str | None = v["C"]
    if got is None:
        return "MISS"
    return got


def missing_file(d: str) -> int:
    v: dict[str, str | None] = dotenv.dotenv_values(d)
    return len(v)


def load(d: str) -> int:
    if dotenv.load_dotenv(d):
        return 1
    return 0


def load_env_value(d: str) -> str:
    dotenv.load_dotenv(d)
    return os.getenv("A", "MISS")
"""

FUNCS = [
    "values",
    "unquoted",
    "quoted",
    "none_value",
    "comment_cut",
    "hash_kept",
    "escape",
    "literal_escape",
    "empty_after_hash",
    "missing_file",
    "load",
    "load_env_value",
]

DOC = (
    "A=1\n"
    'B="two words"\n'
    "C=x  # trail\n"
    "Z=y# kept\n"
    "E\n"
    "A=second\n"
    "export A=third\n"
    'X="a\\nb"\n'
    "K='no\\uescape'\n"
    "C= # empty\n"
)


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@pytest.mark.parametrize("func", FUNCS)
def test_dotenv_matches_cpython(tmp_path: Path, func: str) -> None:
    env_file = tmp_path / "case.env"
    env_file.write_text(DOC, encoding="utf-8")
    path = write_case(tmp_path, "dotenv_case.py", PROGRAM)
    got = compile_run(tmp_path, path, func, repr([str(env_file)]))
    assert got.returncode == 0, got.stderr
    expected = {
        "values": "7\n",
        "unquoted": "third\n",
        "quoted": "two words\n",
        "none_value": "1\n",
        "comment_cut": "\n",
        "hash_kept": "y# kept\n",
        "escape": "a\nb\n",
        "literal_escape": "no\\uescape\n",
        "empty_after_hash": "\n",
        "missing_file": "7\n",
        "load": "1\n",
        "load_env_value": "third\n",
    }[func]
    assert got.stdout == expected


def test_load_sets_the_environment(tmp_path: Path) -> None:
    """load_dotenv writes keys that are not already in the environment."""
    env_file = tmp_path / "case.env"
    env_file.write_text("PS_DOTENV_PROBE=from-file\n", encoding="utf-8")
    os.environ.pop("PS_DOTENV_PROBE", None)
    source = (
        "import dotenv\n"
        "import os\n\n\n"
        "def f() -> str:\n"
        f"    dotenv.load_dotenv({str(env_file)!r})\n"
        "    return os.getenv('PS_DOTENV_PROBE', 'MISS')\n"
    )
    path = write_case(tmp_path, "load_env.py", source)
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "from-file\n"


def test_load_keeps_an_existing_variable(tmp_path: Path) -> None:
    env_file = tmp_path / "case.env"
    env_file.write_text("PS_DOTENV_PROBE=from-file\n", encoding="utf-8")
    source = (
        "import dotenv\n"
        "import os\n\n\n"
        "def f() -> str:\n"
        f"    if not dotenv.load_dotenv({str(env_file)!r}):\n"
        "        return 'load-false'\n"
        "    return os.getenv('PS_DOTENV_PROBE', 'MISS')\n"
    )
    path = write_case(tmp_path, "keep_env.py", source)
    env = {**os.environ, "PS_DOTENV_PROBE": "from-env"}
    got = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--run", "--call", "f", "-o", str(tmp_path / "f")],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == "from-env\n"


BAD_LINES = [
    "A='v' junk\n",
    'K="unterminated\n',
    '"quoted"=1\n',
    "A 1\n",
]


@pytest.mark.parametrize("line", BAD_LINES)
def test_bad_lines_raise_value_error(tmp_path: Path, line: str) -> None:
    env_file = tmp_path / "bad.env"
    env_file.write_text(line, encoding="utf-8")
    source = (
        "import dotenv\n\n\n"
        "def f(d: str) -> int:\n"
        "    v: dict[str, str | None] = {}\n"
        "    try:\n"
        "        v = dotenv.dotenv_values(d)\n"
        "    except ValueError:\n"
        "        return 1\n"
        "    return len(v)\n"
    )
    path = write_case(tmp_path, "bad.py", source)
    got = compile_run(tmp_path, path, "f", repr([str(env_file)]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_other_members_stay_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "other.py",
        "import dotenv\n\ndef f(s: str) -> str:\n    return dotenv.find_dotenv(s)\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "dotenv.find_dotenv" in got.stderr + got.stdout


def test_keyword_arguments_are_refused(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kw.py",
        "import dotenv\n\ndef f(s: str) -> int:\n"
        "    v: dict[str, str | None] = dotenv.dotenv_values(s, verbose=True)\n"
        "    return len(v)\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2003" in got.stderr + got.stdout


def test_dotenv_values_needs_the_annotation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "import dotenv\n\ndef f(s: str) -> int:\n    return len(dotenv.dotenv_values(s))\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1001" in got.stderr + got.stdout


def test_dotenv_lowers(tmp_path: Path) -> None:
    env_file = tmp_path / "case.env"
    env_file.write_text("A=1\n", encoding="utf-8")
    path = write_case(
        tmp_path,
        "ok.py",
        "import dotenv\n\n"
        "def f() -> int:\n"
        f"    v: dict[str, str | None] = dotenv.dotenv_values({str(env_file)!r})\n"
        "    return len(v)\n",
    )
    got = run_cli("compile", str(path), "--emit", "llvm")
    assert got.returncode == 0, got.stderr
    ll = (tmp_path / "ok.ll").read_text()
    assert "@ps_dotenv_values" in ll
    assert "@ps_json_loads" in ll
