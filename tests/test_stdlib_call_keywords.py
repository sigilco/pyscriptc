"""Call-table keyword and arity rows over the builtin and stdlib surface.

Each row lowers keywords by reordering them into the positional call the
runtime helper takes; an unknown form fences with a named PS code. Probes
diff stdout, stderr and the exit code against the CPython 3.14 oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def _fence(tmp_path: Path, source: str, code: str, message: str) -> None:
    path = write_case(tmp_path, "fenced.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    combined = got.stderr + got.stdout
    assert code in combined, combined
    assert message in combined, combined


def test_int_base_keyword_and_positional_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "bases.py",
        "\n".join(
            [
                "def run() -> int:",
                "    a: int = int('ff', base=16)",
                "    b: int = int('777', 8)",
                "    c: int = int('-0xff', base=16)",
                "    d: int = int('102', 3)",
                "    e: int = int('Zz', base=36)",
                "    f: int = int(b'caFe', 16)",
                "    print(a, b, c, d, e, f)",
                "    return 0",
                "",
            ]
        ),
    )


def test_int_bad_base_raises_value_error(tmp_path: Path) -> None:
    # The ValueError message carries the base the caller gave, like CPython.
    for line in ("int('qq', 8)", "int('qq', base=8)"):
        path = write_case(tmp_path, "bad.py", f"def run() -> int:\n    print({line})\n    return 0\n")
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0, line
        assert "ValueError: invalid literal for int() with base 8" in got.stderr, line


def test_int_out_of_i64_range_stays_a_value_error(tmp_path: Path) -> None:
    # Base 16 over the i64 ceiling raise ValueError, the int-str subset, not
    # CPython's bignum.
    path = write_case(
        tmp_path,
        "big.py",
        "def run() -> int:\n    print(int('fffffffffffffffffffffffffffffffffffff', 16))\n    return 0\n",
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "ValueError" in got.stderr


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_int_unknown_forms_fence_with_named_codes() -> None:
    # base 0 and out-of-range constants fence with a named PS code; an
    # unknown keyword names itself.
    _fence("x: int = int('18', base=0)\n", "PS2003", "int base 0 is not lowered")
    _fence("x: int = int('18', base=99)\n", "PS2003", "int lowers base 2 to 36 only")
    _fence("x: int = int('ff', radix=16)\n", "PS2003", "unexpected keyword argument radix")
    _fence("x: int = int(255, base=16)\n", "PS2003", "takes a str or bytes value")
    _fence("x: int = int('ff', 16, base=10)\n", "PS2003", "base given more than once")


def test_open_encoding_read_write_match_cpython(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "enc.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w', encoding='utf-8') as handle:",
                "        handle.write('alpha bravo\\ncharlie')",
                f"    with open({data}, 'r', encoding='utf-8') as handle:",
                "        s: str = handle.read()",
                "    print(s)",
                f"    with open({data}, encoding='ascii') as handle:",
                "        print(len(handle.read()) > 0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_open_unknown_forms_fence_with_named_codes() -> None:
    # Unknown keyword names itself; a non-listed encoding names the set.
    _fence("with open('a', errors='ignore') as h:\n    s: str = h.read()\n", "PS2003", "keyword errors is not lowered")
    _fence(
        "with open('a', 'r', encoding='utf-16') as h:\n    s: str = h.read()\n", "PS2001", "utf-8, ascii, or latin-1"
    )
    _fence("with open('a', 'r', -1) as h:\n    s: str = h.read()\n", "PS2003", "buffering is not lowered")


def test_csv_reader_delimiter_matches_cpython(tmp_path: Path) -> None:
    try:
        _oracle(
            tmp_path,
            "csv_delim.py",
            "\n".join(
                [
                    "import csv",
                    "",
                    "def run() -> int:",
                    "    lines: list[str] = ['col1\\tcol2\\tcol3', 'a\\tb\\tc', 'x\\ty\\tz']",
                    "    rows = csv.reader(lines, delimiter='\\t')",
                    "    for row in rows:",
                    "        print(row[0], row[1], row[2])",
                    "    lines2: list[str] = ['1;2;3', '4;5;6']",
                    "    for r in csv.reader(lines2, delimiter=';'):",
                    "        print(r[0], r[1], r[2])",
                    "    with open('data.tsv', 'w') as f:",
                    "        f.write('h1\\th2\\nval1\\tval2\\n')",
                    "    with open('data.tsv', 'r') as f:",
                    "        for r in csv.reader(f, delimiter='\\t'):",
                    "            print(r[0], r[1])",
                    "    return 0",
                    "",
                ]
            ),
        )
    finally:
        target = Path("data.tsv")
        if target.exists():
            target.unlink()


def test_csv_reader_unknown_forms_fence_with_named_codes() -> None:
    _fence(
        "import csv\nrows = csv.reader(['a'], quotechar='\"')\n",
        "PS2003",
        "csv.reader keyword quotechar is not lowered",
    )
    _fence(
        "import csv\nrows = csv.reader(['a'], delimiter='tab')\n",
        "PS2003",
        '"delimiter" must be a 1-character string',
    )
    _fence("import csv\nrows = csv.reader(['a'], delimiter=123)\n", "PS2003", "csv.reader delimiter must be a str")


def test_enumerate_start_keyword_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "enum_start.py",
        "\n".join(
            [
                "def run() -> int:",
                "    items: list[str] = ['alpha', 'bravo', 'charlie']",
                "    for idx, item in enumerate(items, start=1):",
                "        print(idx, item)",
                "    for idx, item in enumerate(items, start=-3):",
                "        print(idx, item)",
                "    e = enumerate(items, start=10)",
                "    pair: tuple[int, str] = next(e)",
                "    print(pair[0], pair[1])",
                "    return 0",
                "",
            ]
        ),
    )


def test_enumerate_unknown_forms_fence_with_named_codes() -> None:
    _fence("for i, x in enumerate(['a'], 1, start=2):\n    pass\n", "PS2003", "enumerate start given more than once")
    _fence("for i, x in enumerate(['a'], step=1):\n    pass\n", "PS2003", "enumerate keyword step is not lowered")
    _fence("for i, x in enumerate(['a'], start='bad'):\n    pass\n", "PS1003", "enumerate start is int")
