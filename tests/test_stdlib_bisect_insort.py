"""bisect: the insort family matches CPython 3.14 (#492).

`insort_left`, `insort_right` and the `insort` alias mutate the list in
place at the position their bisect form returns. Each lowered form diffs
stdout against the CPython 3.14 oracle; the fenced forms keep their PS code.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, lines: list[str]) -> None:
    source = "\n".join(
        [
            "import bisect",
            "",
            "def show(xs: list[int]) -> str:",
            "    parts: list[str] = []",
            "    for v in xs:",
            "        parts.append(str(v))",
            "    return ','.join(parts)",
            "",
            "def run() -> int:",
            *[f"    {line}" for line in lines],
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    want = oracle(path, "run")
    assert got.stdout == want, f"compiled:\n{got.stdout}\ncpython:\n{want}"


def test_insort_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "insort.py",
        [
            "a: list[int] = [1, 3, 5, 7]",
            "bisect.insort(a, 4)",
            "print(show(a))",
            "bisect.insort_left(a, 4)",
            "print(show(a))",
            "bisect.insort_right(a, 4)",
            "print(show(a))",
            "e: list[int] = []",
            "bisect.insort(e, 9)",
            "print(show(e))",
            "one: list[int] = [2]",
            "bisect.insort_left(one, 1)",
            "bisect.insort_right(one, 3)",
            "print(show(one))",
        ],
    )


def test_insort_ties_take_their_side(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "ties.py",
        [
            "b: list[int] = [2, 2, 2]",
            "bisect.insort_left(b, 2)",
            "print(show(b))",
            "c: list[int] = [2, 2, 2]",
            "bisect.insort_right(c, 2)",
            "print(show(c))",
            "d: list[int] = [1, 2, 2, 3]",
            "bisect.insort_left(d, 2)",
            "print(show(d))",
            "f: list[int] = [1, 2, 2, 3]",
            "bisect.insort_right(f, 2)",
            "print(show(f))",
            "print(bisect.bisect_left(d, 2))",
            "print(bisect.bisect_right(d, 2))",
        ],
    )


def test_bisect_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        ("lo.py", "bisect.insort([1], 2, 0)", "PS2003"),
        ("hi.py", "bisect.insort_right([1], 2, 0, 1)", "PS2003"),
        ("key.py", "bisect.insort_left([1], 2, key=lambda v: v)", "PS2003"),
        ("strs.py", "bisect.insort(['a'], 'b')", "PS1003"),
    ]
    for name, call, code in cases:
        path = write_case(
            tmp_path,
            name,
            f"import bisect\n\ndef run() -> int:\n    {call}\n    return 0\n",
        )
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0, name
        assert code in got.stderr, f"{name}: {got.stderr}"
