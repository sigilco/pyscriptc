"""a if cond else b evaluates cond, then exactly one operand.

The taken str is printed for a truthy condition and a false one. The untaken
operand calls a function that increments a counter and one that raises. Neither
call runs.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def bump(hits: list[int]) -> str:
    hits[0] = hits[0] + 1
    return "no"

def die(hits: list[int]) -> str:
    hits[0] = hits[0] + 1
    raise ValueError("no")
    return "no"

def probe() -> str:
    hits: list[int] = [0]
    text: str = "Ada"
    empty: str = ""
    yes: str = "Ada" if text else (die(hits) if text else bump(hits))
    no: str = (bump(hits) if text else die(hits)) if empty else "Lovelace"
    seen: int = hits[0]
    return (yes if seen == 0 else "bad") + "\\n" + no + "\\n" + str(seen)
"""


def test_ifexp_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ifexp.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout == "Ada\nLovelace\n0\n"


def test_ifexp_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "types.py",
            "def probe() -> str:\n    return 'Ada' if True else 1\n",
            "conditional expression across two types is not lowered",
        ),
        (
            "float.py",
            "def probe() -> str:\n    n: float = 1.0\n    return 'Ada' if n else 'Lovelace'\n",
            "condition must be a bool, not a truthy value",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
