"""`match` lowers to an if chain over the stored subject.

Literal patterns compare `subject == literal`, `None` compares `is None`,
`|` joins alternatives with `or`, and a guard joins with `and` — both ride
the BoolOp short-circuit, so a guard only runs when the pattern matched.
A wildcard always matches; a capture binds the subject to the name. The
subject runs once. A matching case falls through to the next statement
unless its body returns. Other patterns stay fenced.
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

def wild() -> int:
    x: int = 7
    match x:
        case 1:
            return 1
        case _:
            return 9
    return 0

def wild_only() -> int:
    x: int = 3
    match x:
        case _:
            return 4
    return 0

def capture() -> int:
    x: int = 5
    match x:
        case 1:
            return 1
        case y:
            return y + 10
    return 0

def capture_declared() -> int:
    x: int = 5
    y: int = 0
    match x:
        case 9:
            return 0
        case y:
            pass
    return y

def orpat() -> int:
    x: int = 2
    match x:
        case 1 | 2 | 3:
            return 7
        case _:
            return 0
    return 0

def or_miss() -> int:
    x: int = 9
    match x:
        case 1 | 2 | 3:
            return 7
        case _:
            return 4
    return 0

def or_wild() -> int:
    x: int = 9
    match x:
        case 1 | _:
            return 3
    return 0

def or_none() -> int:
    x: int = 2
    match x:
        case None | 1:
            return 1
        case _:
            return 0
    return 0

def guard_falls() -> int:
    x: int = 1
    match x:
        case 1 if x > 5:
            return 5
        case 1:
            return 1
    return 0

def guard_captures() -> int:
    x: int = 9
    match x:
        case y if y > 5:
            return y
        case _:
            return 0
    return 0

def guard_misses() -> int:
    x: int = 2
    match x:
        case y if y > 5:
            return y
        case _:
            return 0
    return 0

def guard_skipped() -> int:
    box: Counter = Counter(0)
    x: int = 1
    match x:
        case 1:
            pass
        case _ if bump(box) == 1:
            pass
    return box.n

def nonesuch() -> int:
    x: int = 0
    match x:
        case None:
            return 1
        case _:
            return 2
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
        "wild": "9",
        "wild_only": "4",
        "capture": "15",
        "capture_declared": "5",
        "orpat": "7",
        "or_miss": "4",
        "or_wild": "3",
        "or_none": "0",
        "guard_falls": "1",
        "guard_captures": "9",
        "guard_misses": "0",
        "guard_skipped": "0",
        "nonesuch": "2",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_match_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "as.py",
            "def probe(x: int) -> int:\n    match x:\n        case 1 as y:\n            return y\n    return 0\n",
            "a capture pattern is not lowered",
        ),
        (
            "or_capture.py",
            "def probe(x: int) -> int:\n    match x:\n        case y | 1:\n            return 1\n    return 0\n",
            "a capture pattern is not lowered",
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
            "single.py",
            "def probe(x: int) -> int:\n    match x:\n        case True:\n            return 1\n    return 0\n",
            "a singleton pattern is not lowered",
        ),
        (
            "dotted.py",
            "def probe(x: int) -> int:\n    match x:\n        case C.K:\n            return 1\n    return 0\n",
            "a match value that is not a literal is not lowered",
        ),
        (
            "unary.py",
            "def probe(x: int) -> int:\n    match x:\n        case -1:\n            return 1\n    return 0\n",
            "a match value that is not a literal is not lowered",
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
