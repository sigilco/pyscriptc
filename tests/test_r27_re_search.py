"""Real-Python backlog R27: ``re.search``/``re.match`` -> ``Match | None``.

`re.search`, `re.match`, `re.fullmatch`, and the same methods on a
`re.Pattern` lower to the tagged `opt_Match` cell (R26) over a synthetic
`Match` class of the subject text plus a flat span list. `Match` lowers
`group`, `start`, `end`, and `span`; `group(i)` for `i > 0` is `str | None`.
Flag arguments fold CPython's int constants into the call: the runtime
honors `I`/`M`/`S`/`X`/`A`/`U` and raises `ValueError` for `L`/`T`/`DEBUG`.
`re.subn` returns the `(str, int)` tuple and `sub`/`split` take their
`count`/`maxsplit` argument. Each probe diffs stdout against the CPython
oracle element-for-element.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_r27_search_groups_and_narrowing(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "search.py",
        "\n".join(
            [
                "import re",
                "",
                "def run() -> int:",
                "    m: re.Match | None = re.search('(a)(b)?', 'xa')",
                "    if m is not None:",
                "        print(m.group(0))",
                "        print(m.group())",
                "        one: str | None = m.group(1)",
                "        if one is not None:",
                "            print(one)",
                "        two: str | None = m.group(2)",
                "        if two is None:",
                "            print('none')",
                "    miss: re.Match | None = re.search('z', 'ab')",
                "    if miss is None:",
                "        print('miss')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_match_and_fullmatch(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "match.py",
        "\n".join(
            [
                "import re",
                "",
                "def run() -> int:",
                "    m: re.Match | None = re.match('ab', 'abc')",
                "    if m is not None:",
                "        print(m.group(0))",
                "    m2: re.Match | None = re.match('bc', 'abc')",
                "    if m2 is None:",
                "        print('no match')",
                "    f: re.Match | None = re.fullmatch('a.c', 'abc')",
                "    if f is not None:",
                "        print('full')",
                "    f2: re.Match | None = re.fullmatch('a.c', 'abcx')",
                "    if f2 is None:",
                "        print('not full')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_start_end_span(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "spans.py",
        "\n".join(
            [
                "import re",
                "",
                "def run() -> int:",
                "    m: re.Match | None = re.search('b+', 'aabbcc')",
                "    if m is not None:",
                "        print(m.start())",
                "        print(m.end())",
                "        both: tuple[int, int] = m.span()",
                "        print(both[0])",
                "        print(both[1])",
                "        off: tuple[int, int] = m.span(0)",
                "        print(off[0])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_flags(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "flags.py",
        "\n".join(
            [
                "import re",
                "",
                "def run() -> int:",
                "    m: re.Match | None = re.search('a+', 'AAA', re.IGNORECASE)",
                "    if m is not None:",
                "        print(m.group(0))",
                "    mm: re.Match | None = re.search('^b', 'a\\nb', re.M)",
                "    if mm is not None:",
                "        print(mm.group(0))",
                "    ds: re.Match | None = re.search('a.c', 'a\\nc', re.S)",
                "    if ds is not None:",
                "        print(ds.end())",
                "    vb: re.Match | None = re.search('a +  # tail\\n b', 'aab', re.X)",
                "    if vb is not None:",
                "        print(vb.group(0))",
                "    kw: re.Match | None = re.search('z', 'Z', flags=re.I)",
                "    if kw is not None:",
                "        print(kw.group(0))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_pattern_methods(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "pattern.py",
        "\n".join(
            [
                "import re",
                "",
                "def run() -> int:",
                "    p: re.Pattern = re.compile('b+', re.I)",
                "    m: re.Match | None = p.search('aaBBcc')",
                "    if m is not None:",
                "        print(m.group(0))",
                "    mm: re.Match | None = p.match('BBaa')",
                "    if mm is not None:",
                "        print(mm.group(0))",
                "    fm: re.Match | None = p.fullmatch('bb')",
                "    if fm is not None:",
                "        print(fm.group(0))",
                "    hits: list[str] = p.findall('abBbb')",
                "    for hit in hits:",
                "        print(hit)",
                "    done: str = p.sub('-', 'abBbb')",
                "    print(done)",
                "    pair: tuple[str, int] = p.subn('-', 'abBbb', 1)",
                "    print(pair[0])",
                "    print(pair[1])",
                "    parts: list[str] = p.split('aBBcc', 1)",
                "    print(parts[0])",
                "    print(parts[1])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_subn_count_and_module_pattern(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "subn.py",
        "\n".join(
            [
                "import re",
                "",
                "PAT: re.Pattern = re.compile('x+', re.I)",
                "",
                "def run() -> int:",
                "    t: tuple[str, int] = re.subn('a+', '-', 'aAaA', 2)",
                "    print(t[0])",
                "    print(t[1])",
                "    t2: tuple[str, int] = PAT.subn('-', 'xXyXx')",
                "    print(t2[0])",
                "    print(t2[1])",
                "    cut: str = re.sub('a', '-', 'aaaa', count=1)",
                "    print(cut)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r27_from_import_search(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "fromre.py",
        "\n".join(
            [
                "import re",
                "from re import search",
                "",
                "def run() -> int:",
                "    m: re.Match | None = search('(a)', 'cat')",
                "    if m is not None:",
                "        g: str | None = m.group(1)",
                "        if g is not None:",
                "            print(g)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r27_fences(tmp_path: Path) -> None:
    cases = [
        ("groups.py", "        m.groups()", "Match.groups is not lowered"),
        ("gname.py", "        m.group('n')", "Match.group names are not lowered"),
        ("ctor.py", "        re.Match()", "re.Match is not in the lowered subset of re"),
    ]
    for name, line, text in cases:
        source = "\n".join(
            [
                "import re",
                "",
                "def probe() -> int:",
                "    m: re.Match | None = re.search('(a)', 'a')",
                "    if m is not None:",
                line,
                "    return 0",
                "",
            ]
        )
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
