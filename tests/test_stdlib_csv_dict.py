"""csv.DictReader and csv.DictWriter, diffed against the CPython oracle.

The two lower over the ps_csv core: the reader maps the header row onto
every record as a dict[str, str] and the writer names the fields once and
writes the header row and the rows of a dict. The probes diff stdout,
stderr and the exit code against the CPython 3.14 oracle.
"""

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


def test_dict_reader_over_lines_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "reader_lines.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                "    lines: list[str] = ['name,age,city', 'ada,36,london', 'alan,41,cork']",
                "    for row in csv.DictReader(lines):",
                "        name: str | None = row['name']",
                "        age: str | None = row['age']",
                "        city: str | None = row['city']",
                "        if name is None or age is None or city is None:",
                "            print('missing')",
                "        else:",
                "            print(name, age, city)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_reader_over_a_file_matches_cpython(tmp_path: Path) -> None:
    people = tmp_path / "people.csv"
    _oracle(
        tmp_path,
        "reader_file.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                f"    with open(r'{people}', 'w') as f:",
                "        f.write('name,age\\nada,36\\nalan,41\\n')",
                f"    with open(r'{people}', 'r') as f:",
                "        for row in csv.DictReader(f):",
                "            name: str | None = row['name']",
                "            if name is None:",
                "                print('missing')",
                "            else:",
                "                print(name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_reader_short_row_takes_the_restval(tmp_path: Path) -> None:
    # A row with fewer fields than the header takes the restval, and
    # CPython's reader restval default is None.
    _oracle(
        tmp_path,
        "reader_short.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                "    lines: list[str] = ['a,b', '1']",
                "    for row in csv.DictReader(lines):",
                "        present: str | None = row['a']",
                "        if present is None:",
                "            print('missing')",
                "        else:",
                "            print(present)",
                "        missing: str | None = row['b']",
                "        if missing is None:",
                "            print('missing')",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_reader_long_row_raises_value_error(tmp_path: Path) -> None:
    # A row with more fields than the header raises the ValueError CPython
    # raises, with the same message.
    path = write_case(
        tmp_path,
        "reader_long.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                "    lines: list[str] = ['a,b', '1,2,3']",
                "    for row in csv.DictReader(lines):",
                "        bad: str | None = row['a']",
                "        if bad is None:",
                "            print('missing')",
                "        else:",
                "            print(bad)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    assert "dict reader has more fields than fieldnames" in got.stderr, got.stderr


def test_dict_reader_keeps_the_quoted_fields(tmp_path: Path) -> None:
    # The reader parses with the same state machine csv.reader uses: a
    # quoted field keeps its comma and its newline.
    _oracle(
        tmp_path,
        "reader_quoted.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                "    lines: list[str] = ['a,b', '\"x,y\",z']",
                "    for row in csv.DictReader(lines):",
                "        a: str | None = row['a']",
                "        b: str | None = row['b']",
                "        if a is None or b is None:",
                "            print('missing')",
                "        else:",
                "            print(a, b)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_reader_delimiter_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "reader_delim.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                "    lines: list[str] = ['a\\tb', '1\\t2']",
                "    for row in csv.DictReader(lines, delimiter='\\t'):",
                "        a: str | None = row['a']",
                "        b: str | None = row['b']",
                "        if a is None or b is None:",
                "            print('missing')",
                "        else:",
                "            print(a, b)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_writer_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "writer.py",
        "\n".join(
            [
                "import csv",
                "import io",
                "",
                "def run() -> int:",
                "    buf: io.StringIO = io.StringIO()",
                "    wr: csv.DictWriter = csv.DictWriter(buf, ['name', 'age'])",
                "    wr.writeheader()",
                "    wr.writerow({'name': 'ada', 'age': '36'})",
                "    wr.writerow({'name': 'alan', 'age': '41'})",
                "    print(buf.getvalue())",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_writer_over_a_file_matches_cpython(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    _oracle(
        tmp_path,
        "writer_file.py",
        "\n".join(
            [
                "import csv",
                "",
                "def run() -> int:",
                f"    with open(r'{out}', 'w') as f:",
                "        wr: csv.DictWriter = csv.DictWriter(f, ['a', 'b'])",
                "        wr.writeheader()",
                "        wr.writerow({'a': '1', 'b': '2'})",
                f"    with open(r'{out}', 'r') as f:",
                "        text: str = f.read()",
                "    print(text)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_writer_missing_field_takes_the_restval(tmp_path: Path) -> None:
    # A field the row dict does not carry takes the CPython restval
    # default, the empty text.
    _oracle(
        tmp_path,
        "writer_missing.py",
        "\n".join(
            [
                "import csv",
                "import io",
                "",
                "def run() -> int:",
                "    buf: io.StringIO = io.StringIO()",
                "    wr: csv.DictWriter = csv.DictWriter(buf, ['a', 'b'])",
                "    wr.writerow({'a': '1'})",
                "    print(buf.getvalue())",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_writer_quotes_the_fields_that_need_it(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "writer_quoted.py",
        "\n".join(
            [
                "import csv",
                "import io",
                "",
                "def run() -> int:",
                "    buf: io.StringIO = io.StringIO()",
                "    wr: csv.DictWriter = csv.DictWriter(buf, ['a', 'b'])",
                "    wr.writerow({'a': 'x,y', 'b': 'q\"z'})",
                "    print(buf.getvalue())",
                "    return 0",
                "",
            ]
        ),
    )


def test_dict_reader_restkey_and_restval_fence_by_name() -> None:
    _fence(
        "import csv\nrows = csv.DictReader(['a'], restkey='x')\n",
        "PS2003",
        "csv.DictReader keyword restkey is not lowered",
    )
    _fence(
        "import csv\nrows = csv.DictReader(['a'], restval='x')\n",
        "PS2003",
        "csv.DictReader keyword restval is not lowered",
    )


def test_dict_writer_keywords_fence_by_name() -> None:
    _fence(
        "import csv\nw = csv.DictWriter(open('f'), ['a'], restval='x')\n",
        "PS2003",
        "csv.DictWriter keyword restval is not lowered",
    )
    _fence(
        "import csv\nw = csv.DictWriter(open('f'), ['a'], extrasaction='ignore')\n",
        "PS2003",
        "csv.DictWriter keyword extrasaction is not lowered",
    )


def test_csv_names_outside_the_subset_fence_naming_csv() -> None:
    _fence("import csv\ns = csv.Sniffer()\n", "PS2001", "csv.Sniffer is not in the lowered subset of csv")
    _fence("import csv\ns = csv.Dialect()\n", "PS2001", "csv.Dialect is not in the lowered subset of csv")
