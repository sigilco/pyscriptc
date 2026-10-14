"""x is None and x is not None are == and != against None.

One value is None and one is a str. The four flags are 1, 0, 0, 1.
is against anything other than None stays fenced. A list cannot compare with None.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def probe() -> int:
    empty: None = None
    text: str = "Ada"
    a: int = 0
    if empty is None:
        a = 1
    b: int = 0
    if empty is not None:
        b = 1
    c: int = 0
    if text is None:
        c = 1
    d: int = 0
    if text is not None:
        d = 1
    if not (None is empty):
        return 0
    if None is not empty:
        return 0
    if None is text:
        return 0
    if not (None is not text):
        return 0
    return a * 1000 + b * 100 + c * 10 + d
"""


def test_is_none_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "is_none.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "1001"


def test_is_other_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "pair.py",
            (
                "def probe() -> int:\n"
                "    left: str = 'Ada'\n"
                "    right: str = 'Ada'\n"
                "    if left is right:\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "is is not lowered",
        ),
        (
            "true.py",
            "def probe() -> int:\n    text: str = 'Ada'\n    if text is True:\n        return 1\n    return 0\n",
            "is is not lowered",
        ),
        (
            "false.py",
            "def probe() -> int:\n    text: str = 'Ada'\n    if text is False:\n        return 1\n    return 0\n",
            "is is not lowered",
        ),
        (
            "call.py",
            (
                "def other() -> str:\n"
                "    return 'Ada'\n"
                "\n"
                "def probe() -> int:\n"
                "    text: str = 'Ada'\n"
                "    if text is other():\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "is is not lowered",
        ),
        (
            "listed.py",
            "def probe() -> int:\n    xs: list[int] = [1]\n    if xs is None:\n        return 1\n    return 0\n",
            "list[int] cannot compare with None",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
