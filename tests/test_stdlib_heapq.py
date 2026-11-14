"""heapq closed subset, diffed against the CPython oracle."""

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


def test_heap_ops_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "heap_ops.py",
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[int] = [5, 1, 4, 2, 3]",
                "    heapq.heapify(xs)",
                "    print(xs)",
                "    heapq.heappush(xs, 0)",
                "    print(xs)",
                "    print(heapq.heappop(xs))",
                "    print(xs)",
                "    print(heapq.heappushpop(xs, 10))",
                "    print(xs)",
                "    print(heapq.heapreplace(xs, 10))",
                "    print(xs)",
                "    return 0",
                "",
            ]
        ),
    )


def test_heap_layout_matches_cpython(tmp_path: Path) -> None:
    """heapify leaves CPython's exact array layout, ties included."""
    _oracle(
        tmp_path,
        "heap_layout.py",
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    a: list[int] = [3, 1, 4, 1, 5, 9, 2, 6]",
                "    heapq.heapify(a)",
                "    print(a)",
                "    b: list[int] = [1, 1, 1, 0]",
                "    heapq.heapify(b)",
                "    print(b)",
                "    c: list[int] = [5, 5, 5, 5, 5]",
                "    heapq.heapify(c)",
                "    print(c)",
                "    return 0",
                "",
            ]
        ),
    )


def test_nlargest_nsmallest_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "heap_topn.py",
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[int] = [5, 1, 4, 2, 3]",
                "    print(heapq.nlargest(3, xs))",
                "    print(heapq.nsmallest(3, xs))",
                "    print(heapq.nlargest(0, xs))",
                "    print(heapq.nsmallest(9, xs))",
                "    print(heapq.nlargest(2, [2, 2, 1, 1]))",
                "    print(heapq.nsmallest(2, [2, 2, 1, 1]))",
                "    return 0",
                "",
            ]
        ),
    )


def test_heap_float_and_str_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "heap_scalar.py",
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    fs: list[float] = [3.5, 1.25, 2.0, 0.5]",
                "    heapq.heapify(fs)",
                "    print(fs)",
                "    print(heapq.heappop(fs))",
                "    print(heapq.nlargest(2, fs))",
                "    ss: list[str] = ['pear', 'apple', 'fig', 'date']",
                "    heapq.heapify(ss)",
                "    print(ss)",
                "    print(heapq.heappop(ss))",
                "    heapq.heappush(ss, 'aa')",
                "    print(ss)",
                "    print(heapq.nsmallest(2, ss))",
                "    return 0",
                "",
            ]
        ),
    )


def test_heap_empty_pop_matches_cpython(tmp_path: Path) -> None:
    """heappop and heapreplace on an empty heap raise CPython's IndexError."""
    _oracle(
        tmp_path,
        "heap_empty.py",
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    e: list[int] = []",
                "    try:",
                "        heapq.heappop(e)",
                "    except IndexError as exc:",
                "        print('pop', exc)",
                "    try:",
                "        heapq.heapreplace(e, 1)",
                "    except IndexError as exc:",
                "        print('replace', exc)",
                "    print(heapq.heappushpop(e, 5), e)",
                "    return 0",
                "",
            ]
        ),
    )


def test_heapify_tuple_element_fences() -> None:
    _fence(
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[tuple[int, int]] = [(1, 2)]",
                "    heapq.heapify(xs)",
                "    return 0",
                "",
            ]
        ),
        "PS1003",
        "list of int, float or str",
    )


def test_heapq_merge_fences() -> None:
    _fence(
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1]",
                "    ys: list[int] = heapq.merge(xs, xs)",
                "    return 0",
                "",
            ]
        ),
        "PS2001",
        "heapq.merge is not in the lowered subset of heapq",
    )


def test_heapq_max_family_fences() -> None:
    _fence(
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1]",
                "    heapq.heapify_max(xs)",
                "    return 0",
                "",
            ]
        ),
        "PS2001",
        "heapq.heapify_max is not in the lowered subset of heapq",
    )


def test_nsmallest_key_fences() -> None:
    _fence(
        "\n".join(
            [
                "import heapq",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 2]",
                "    ys: list[int] = heapq.nsmallest(1, xs, key=abs)",
                "    return 0",
                "",
            ]
        ),
        "PS2003",
        "keyword",
    )
