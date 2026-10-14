"""`match` of one literal is `if subject == literal`.

The subject runs once. A matching case falls through to the next statement
unless its body returns. A miss does not run the case body. Other patterns
stay fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
class Counter:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n

def bump(box: Counter) -> int:
    box.n = box.n + 1
    return 1

def checklist() -> int:
    x: int = 1
    match x:
        case 1:
            return 1
    return 0

def hit() -> int:
    n: int = 0
    x: int = 1
    match x:
        case 1:
            n = 1
    n = n + 1
    return n

def miss() -> int:
    n: int = 0
    x: int = 2
    match x:
        case 1:
            n = 5
    return n

def second() -> int:
    x: int = 2
    match x:
        case 1:
            return 1
        case 2:
            return 2
    return 0

def first_only() -> int:
    n: int = 0
    x: int = 1
    match x:
        case 1:
            n = 1
        case 2:
            n = 9
    return n

def once() -> int:
    box: Counter = Counter(0)
    match bump(box):
        case 1:
            return box.n
    return 0

def text() -> int:
    word: str = "ab"
    match word:
        case "ab":
            return 3
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_match_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "match.py", _PROBE)
    expected = {
        "checklist": "1",
        "hit": "2",
        "miss": "0",
        "second": "2",
        "first_only": "1",
        "once": "1",
        "text": "3",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_match_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "capture.py",
            "def probe(x: int) -> int:\n    match x:\n        case y:\n            return y\n    return 0\n",
            "a capture pattern is not lowered",
        ),
        (
            "wild.py",
            "def probe(x: int) -> int:\n    match x:\n        case _:\n            return 1\n    return 0\n",
            "a wildcard pattern is not lowered",
        ),
        (
            "orpat.py",
            "def probe(x: int) -> int:\n    match x:\n        case 1 | 2:\n            return 1\n    return 0\n",
            "an or pattern is not lowered",
        ),
        (
            "seq.py",
            "def probe(x: int) -> int:\n    match x:\n        case [1, 2]:\n            return 1\n    return 0\n",
            "a sequence pattern is not lowered",
        ),
        (
            "cls.py",
            "def probe(x: int) -> int:\n    match x:\n        case Point(a):\n            return 1\n    return 0\n",
            "a class pattern is not lowered",
        ),
        (
            "mapping.py",
            'def probe(x: int) -> int:\n    match x:\n        case {"a": 1}:\n            return 1\n    return 0\n',
            "a mapping pattern is not lowered",
        ),
        (
            "guard.py",
            "def probe(x: int) -> int:\n    match x:\n        case 1 if x > 0:\n            return 1\n    return 0\n",
            "a match guard is not lowered",
        ),
        (
            "single.py",
            "def probe(x: int) -> int:\n    match x:\n        case True:\n            return 1\n    return 0\n",
            "a singleton pattern is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
