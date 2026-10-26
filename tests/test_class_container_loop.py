"""A `for` loop over a container of user class instances lowers (issue #481).

`rows: list[Row]` with a user class `Row` iterates natively: the loop
target binds the element, field reads and method calls on it lower, and
the compiled output matches CPython byte for byte. The paired forms
(`enumerate(rows)`, `zip(rows, other)`) bind the element the same way.

The compiled entry takes scalars (a compiled binary cannot take a user
class instance as an argument), so each helper builds its own rows and
the CPython oracle runs the same source.

A shape that has no lowered element type stays refused with a named PS
code: a synthetic runtime class element (`list[Path]`), an optional
element and a set of instances.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

_PROBE = """
class Row:
    name: str
    n: int

    def __init__(self, name: str, n: int) -> None:
        self.name = name
        self.n = n

    def scaled(self, k: int) -> int:
        return self.n * k


def make_rows() -> list[Row]:
    return [Row("a", 2), Row("b", 3), Row("c", 5)]


def field_sum() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for row in rows:
        total = total + row.n
    return total


def method_sum() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for row in rows:
        total = total + row.scaled(2)
    return total


def name_join() -> str:
    rows: list[Row] = make_rows()
    out = ""
    for row in rows:
        out = out + row.name
    return out


def with_else() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for row in rows:
        total = total + row.n
    else:
        total = total + 100
    return total


def with_break() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for row in rows:
        if row.n > 4:
            break
        total = total + row.n
    return total


def with_continue() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for row in rows:
        if row.n == 2:
            continue
        total = total + row.n
    return total


def nested() -> int:
    rows: list[Row] = make_rows()
    tags: list[str] = ["ab", "c"]
    total = 0
    for row in rows:
        for tag in tags:
            total = total + len(tag)
        total = total + row.n
    return total


def enumerated() -> int:
    rows: list[Row] = make_rows()
    total = 0
    for index, row in enumerate(rows):
        total = total + index * row.n
    return total


def zipped() -> int:
    rows: list[Row] = make_rows()
    weights: list[int] = [10, 20, 30]
    total = 0
    for row, weight in zip(rows, weights):
        total = total + row.n * weight
    return total


def mutating() -> int:
    rows: list[Row] = make_rows()
    for row in rows:
        row.n = row.n + 10
    return rows[0].n * 100 + rows[1].n


def probe() -> int:
    total = field_sum() * 1000000
    total = total + method_sum() * 100000
    total = total + len(name_join()) * 10000
    total = total + with_else() * 1000
    total = total + with_break() * 100
    total = total + with_continue() * 10
    total = total + nested()
    total = total + enumerated()
    total = total + zipped()
    total = total + mutating()
    return total
"""

_FUNCS = [
    "field_sum",
    "method_sum",
    "name_join",
    "with_else",
    "with_break",
    "with_continue",
    "nested",
    "enumerated",
    "zipped",
    "mutating",
]


def _case(tmp_path: Path, name: str, source: str) -> Path:
    return write_case(tmp_path, name, source)


@needs_cc
def test_class_container_loop_matches_cpython(tmp_path: Path) -> None:
    path = _case(tmp_path, "rows.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@needs_cc
@pytest.mark.parametrize("func", _FUNCS)
def test_each_loop_form_matches_cpython(tmp_path: Path, func: str) -> None:
    path = _case(tmp_path, "rows.py", _PROBE)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, f"{func}\n{got.stderr}"
    assert got.stdout == oracle(path, func)


def test_loop_over_class_list_lowers_without_fences() -> None:
    from pyscriptc.lower import lower_source

    module = lower_source(_PROBE)
    assert module["fences"] == []
    entry = next(fn for fn in module["functions"] if fn["name"] == "field_sum")
    # The loop target binds the element of the pinned sequence, so the
    # element type is the user class and not a generic pointer.
    locals_by_name = {local["name"]: local["type"] for local in entry["locals"]}
    assert locals_by_name["rows"] == {"kind": "list", "elem": {"kind": "class", "name": "Row"}}
    assert locals_by_name["row"] == {"kind": "class", "name": "Row"}


def test_optional_class_element_stays_fenced(tmp_path: Path) -> None:
    source = (
        "class Row:\n"
        "    n: int\n"
        "\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "\n"
        "def probe() -> int:\n"
        "    rows: list[Row | None] = [None]\n"
        "    total = 0\n"
        "    for row in rows:\n"
        "        if row is not None:\n"
        "            total = total + row.n\n"
        "    return total\n"
    )
    path = _case(tmp_path, "optional.py", source)
    got = run_cli("compile", str(path), "-o", str(tmp_path / "optional"))
    assert got.returncode != 0
    assert "PS2002" in got.stderr
    assert "for over list[Row | None] is not lowered" in got.stderr


def test_synthetic_class_element_stays_fenced(tmp_path: Path) -> None:
    source = (
        "from pathlib import Path\n"
        "\n"
        "def probe() -> int:\n"
        "    paths: list[Path] = [Path('/a')]\n"
        "    total = 0\n"
        "    for path in paths:\n"
        "        total = total + 1\n"
        "    return total\n"
    )
    path = _case(tmp_path, "paths.py", source)
    got = run_cli("compile", str(path), "-o", str(tmp_path / "paths"))
    assert got.returncode != 0
    assert "PS2002" in got.stderr
    assert "for over list[Path] is not lowered" in got.stderr


def test_set_of_class_instances_stays_fenced(tmp_path: Path) -> None:
    source = (
        "class Row:\n"
        "    n: int\n"
        "\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "\n"
        "def probe() -> int:\n"
        "    rows: set[Row] = set()\n"
        "    total = 0\n"
        "    for row in rows:\n"
        "        total = total + row.n\n"
        "    return total\n"
    )
    path = _case(tmp_path, "rows_set.py", source)
    got = run_cli("compile", str(path), "-o", str(tmp_path / "rows_set"))
    assert got.returncode != 0
    assert "PS2002" in got.stderr
