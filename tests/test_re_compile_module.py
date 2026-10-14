"""A module re.compile of one str constant is the Pattern call, inlined at each use.

The probe prints the start index of a match, or -1 when findall is empty.
findall and str.find are already lowered. search and match are not.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import re

_NORMALIZE_PATTERN = re.compile(r"[-_.]+")


def probe() -> int:
    text: str = "ab--cd"
    hits: list[str] = _NORMALIZE_PATTERN.findall(text)
    if len(hits) == 0:
        return -1
    return text.find(hits[0])


def miss() -> int:
    text: str = "abcd"
    hits: list[str] = _NORMALIZE_PATTERN.findall(text)
    if len(hits) == 0:
        return -1
    return text.find(hits[0])


def local_probe() -> int:
    text: str = "ab--cd"
    hits: list[str] = re.compile(r"[-_.]+").findall(text)
    if len(hits) == 0:
        return -1
    return text.find(hits[0])


def twice() -> int:
    text: str = "x_y"
    first: list[str] = _NORMALIZE_PATTERN.findall(text)
    second: list[str] = _NORMALIZE_PATTERN.findall("none")
    start: int = -1
    if len(first) != 0:
        start = text.find(first[0])
    if len(second) != 0:
        return -2
    return start
"""


def test_module_re_compile_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "normalize.py", _PROBE)
    for func, expected in (("probe", "2"), ("miss", "-1"), ("local_probe", "2"), ("twice", "1")):
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == expected


def test_module_re_compile_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "flags.py",
            "import re\nPAT = re.compile(r'[-_.]+', re.IGNORECASE)\n\ndef probe() -> int:\n    return 0\n",
            "re.compile expects 1 argument(s)",
        ),
        (
            "keyword.py",
            "import re\nPAT = re.compile(r'a', flags=0)\n\ndef probe() -> int:\n    return 0\n",
            "keyword arguments are not lowered",
        ),
        (
            "name.py",
            "import re\nWORD = 'a'\nPAT = re.compile(WORD)\n\ndef probe() -> int:\n    return 0\n",
            "a module assignment must be a constant",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
