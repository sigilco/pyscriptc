"""frozenset of one str constant is the set of its characters.

Characters stay in string order. A repeat keeps the first. The empty string is
an empty set. Bytes stays fenced. .add on a name bound to this value stays fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
CHARS = frozenset(".0123456789")
REPEAT = frozenset("aba")
EMPTY = frozenset("")

def probe() -> str:
    dot: int = 0
    if "." in CHARS:
        dot = 1
    letter: int = 0
    if "a" in CHARS:
        letter = 1
    return str(len(CHARS)) + str(dot) + str(letter) + str(len(REPEAT)) + str(len(EMPTY))
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_frozenset_str_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "frozen_str.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "111020"


def test_frozenset_str_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "bytes.py",
            "def probe() -> int:\n    return len(frozenset(b'ab'))\n",
            "call to frozenset is not lowered",
        ),
        (
            "wide.py",
            'CHARS = frozenset("\\u0100")\n\ndef probe() -> int:\n    return len(CHARS)\n',
            "string literals outside Latin-1 are not lowered",
        ),
        (
            "add.py",
            (
                'CHARS = frozenset(".0")\n'
                "\n"
                "def probe() -> int:\n"
                "    local: set[str] = CHARS\n"
                "    local.add('a')\n"
                "    return len(local)\n"
            ),
            "frozenset has no mutating methods",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
