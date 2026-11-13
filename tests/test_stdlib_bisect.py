"""bisect.insort and friends, diffed against the CPython oracle."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run"), got.stdout


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_insort_int_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "insort_int.py",
        "\n".join(
            [
                "import bisect",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 3, 5]",
                "    bisect.insort(xs, 4)",
                "    bisect.insort_left(xs, 3)",
                "    bisect.insort_right(xs, 3)",
                "    i: int = 0",
                "    while i < len(xs):",
                "        print(xs[i])",
                "        i = i + 1",
                "    print(bisect.bisect_left(xs, 3), bisect.bisect_right(xs, 3))",
                "    empty: list[int] = []",
                "    bisect.insort(empty, 7)",
                "    print(len(empty), empty[0])",
                "    return 0",
                "",
            ]
        ),
    )


def test_insort_float_and_str_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "insort_scalar.py",
        "\n".join(
            [
                "import bisect",
                "",
                "def run() -> int:",
                "    fs: list[float] = [1.0, 2.5, 4.0]",
                "    bisect.insort(fs, 2.5)",
                "    bisect.insort_left(fs, 2.5)",
                "    i: int = 0",
                "    while i < len(fs):",
                "        print(fs[i])",
                "        i = i + 1",
                "    print(bisect.bisect_left(fs, 2.5), bisect.bisect_right(fs, 3.0))",
                "    ws: list[str] = ['a', 'c', 'e']",
                "    bisect.insort(ws, 'b')",
                "    bisect.insort_left(ws, 'c')",
                "    i = 0",
                "    while i < len(ws):",
                "        print(ws[i])",
                "        i = i + 1",
                "    print(bisect.bisect_left(ws, 'c'))",
                "    return 0",
                "",
            ]
        ),
    )


def test_insort_key_fences() -> None:
    _fence(
        "\n".join(
            [
                "import bisect",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 3]",
                "    bisect.insort(xs, 2, key=abs)",
                "    return 0",
                "",
            ]
        ),
        "PS2001",
        "key=",
    )


def test_insort_lo_hi_fences() -> None:
    _fence(
        "\n".join(
            [
                "import bisect",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 3]",
                "    bisect.insort_left(xs, 2, lo=0)",
                "    return 0",
                "",
            ]
        ),
        "PS2003",
        "lo=",
    )
