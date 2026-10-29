"""heapq: the closed heap-queue subset matches CPython 3.14 (#487).

The subset is `heapify`, `heappush`, `heappop`, `nlargest` and `nsmallest`
over a `list[int]`. Each lowered form diffs stdout against the CPython 3.14
oracle; the fenced names keep their PS code.

The heap lives in the list itself, so the tests read the array back: the
layout `heapify` leaves is part of the contract, and the oracle checks it.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, lines: list[str]) -> None:
    source = "\n".join(
        [
            "import heapq",
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


def test_heapify_leaves_cpython_layout(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "heapify.py",
        [
            "h: list[int] = [3, 1, 4, 1, 5, 9, 2, 6]",
            "heapq.heapify(h)",
            "print(show(h))",
            "e: list[int] = []",
            "heapq.heapify(e)",
            "print(show(e))",
            "one: list[int] = [7]",
            "heapq.heapify(one)",
            "print(show(one))",
            "two: list[int] = [2, 1]",
            "heapq.heapify(two)",
            "print(show(two))",
        ],
    )


def test_heappop_returns_sorted_values(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "pop.py",
        [
            "h: list[int] = [3, 1, 4, 1, 5, 9, 2, 6]",
            "heapq.heapify(h)",
            "while len(h) > 0:",
            "    print(heapq.heappop(h))",
            "print(show(h))",
        ],
    )


def test_heappush_keeps_the_heap_invariant(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "push.py",
        [
            "h: list[int] = []",
            "for v in [5, 3, 8, 1]:",
            "    heapq.heappush(h, v)",
            "print(show(h))",
            "print(heapq.heappop(h))",
            "print(show(h))",
            "heapq.heappush(h, 0)",
            "print(show(h))",
        ],
    )


def test_nlargest_and_nsmallest_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "extremes.py",
        [
            "xs: list[int] = [3, 1, 4, 1, 5, 9, 2, 6]",
            "print(show(heapq.nlargest(3, xs)))",
            "print(show(heapq.nsmallest(3, xs)))",
            "print(show(heapq.nlargest(0, xs)))",
            "print(show(heapq.nsmallest(10, xs)))",
            "ties: list[int] = [5, 5, 5, 1]",
            "print(show(heapq.nlargest(3, ties)))",
            "print(show(heapq.nsmallest(3, ties)))",
            "empty: list[int] = []",
            "print(show(heapq.nlargest(2, empty)))",
            "print(show(heapq.nsmallest(2, empty)))",
        ],
    )


def test_heapq_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        ("merge.py", "heapq.merge([1], [2])", "PS2001"),
        ("replace.py", "heapq.heapreplace([1], 2)", "PS2001"),
        ("pushpop.py", "heapq.heappushpop([1], 2)", "PS2001"),
        ("maxify.py", "heapq.heapify_max([1])", "PS2001"),
        ("key.py", "heapq.nlargest(2, [1, 2], key=lambda v: v)", "PS2003"),
    ]
    for name, call, code in cases:
        path = write_case(
            tmp_path,
            name,
            f"import heapq\n\ndef run() -> int:\n    {call}\n    return 0\n",
        )
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0, name
        assert code in got.stderr, f"{name}: {got.stderr}"


def test_heapq_rejects_a_non_int_list(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "strings.py",
        "import heapq\n\ndef run() -> int:\n    heapq.heapify(['a'])\n    return 0\n",
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS1003" in got.stderr, got.stderr
