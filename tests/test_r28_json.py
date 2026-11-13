"""Real-Python backlog R28: JSON floats and null.

``json.loads`` of float text fills a double slot (``NaN``/``Infinity`` are
constants CPython accepts), and ``json.loads`` of ``null`` into an
``Optional[T]`` annotation returns the R26 tagged cell with its tag unset.
``json.dumps`` round-trips both: floats spell the CPython repr and a tagless
cell emits ``null``. Each probe diffs stdout against the CPython oracle
element-for-element.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_r28_loads_float_scalars(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "floats.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    for text in ['1.5', '-0.25', '2.5e-4', '1e300', 'NaN', '-Infinity']:",
                "        value: float = json.loads(text)",
                "        print(json.dumps(value))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r28_loads_null_to_optional(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nullopt.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    x: int | None = json.loads('null')",
                "    if x is None:",
                "        print('none')",
                "    y: int | None = json.loads('7')",
                "    if y is not None:",
                "        print(y + 1)",
                "    s: str | None = json.loads('null')",
                "    print(s is None)",
                "    t: str | None = json.loads('\"hi\"')",
                "    if t is not None:",
                "        print(t)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r28_json_decode_error_matches_cpython(tmp_path: Path) -> None:
    """A malformed document raises the JSONDecodeError class: catchable by
    name, by its ValueError parent and by Exception, all like CPython."""
    path = write_case(
        tmp_path,
        "jdecode.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    caught = 0",
                "    try:",
                "        a: dict[str, int] = json.loads('{\"a\": ')",
                "    except json.JSONDecodeError:",
                "        caught = caught + 1",
                "    try:",
                "        b: dict[str, int] = json.loads('{\"b\": ')",
                "    except ValueError:",
                "        caught = caught + 2",
                "    try:",
                "        c: dict[str, int] = json.loads('not json')",
                "    except Exception:",
                "        caught = caught + 4",
                "    print(caught)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r28_optional_bool_cell(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "optbool.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    yes: bool | None = json.loads('true')",
                "    if yes is not None:",
                "        print(yes)",
                "    no: bool | None = json.loads('null')",
                "    print(no is None)",
                "    print(json.dumps(yes))",
                "    print(json.dumps(no))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r28_nested_containers(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nested.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    xs: list[float] = json.loads('[1.5, -2.25, 1e3]')",
                "    print(json.dumps(xs))",
                '    table: dict[str, float] = json.loads(\'{"a": 0.1, "b": 1e-4}\')',
                "    print(json.dumps(table))",
                "    opts: list[int | None] = json.loads('[1, null, 3]')",
                "    print(json.dumps(opts))",
                '    grid: dict[str, int | None] = json.loads(\'{"k": null, "v": 2}\')',
                "    print(json.dumps(grid))",
                "    mixed: dict[str, list[float | None]] = json.loads('{\"r\": [1.5, null]}')",
                "    print(json.dumps(mixed))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r28_dumps_round_trip(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "roundtrip.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    print(json.dumps(1.5))",
                "    print(json.dumps(1000.0))",
                "    print(json.dumps(0.00025))",
                "    print(json.dumps(1e300))",
                "    print(json.dumps(-0.0))",
                "    x: int | None = None",
                "    print(json.dumps(x))",
                "    x = 9",
                "    print(json.dumps(x))",
                "    payload: str = json.dumps({'n': [1.5, 2.5]})",
                "    print(payload)",
                "    back: dict[str, list[float]] = json.loads(payload)",
                "    print(json.dumps(back))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r28_unsupported_json_type_still_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "clsjson.py",
        "import json\n\nclass P:\n    x: int\n\ndef f() -> str:\n    return json.dumps(P())\n",
    )
    got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / "clsjson.ll"))
    assert got.returncode == 3
    assert "PS2001" in got.stderr
    assert "json.dumps does not lower" in got.stderr


def test_r28_null_into_plain_int_fences_at_runtime(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "intnull.py",
        "import json\n\ndef run() -> int:\n    x: int = json.loads('null')\n    return x\n",
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "JSON value does not match the annotation" in got.stderr


def test_json_dumps_indent_and_sort_keys(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "dumps_opts.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                "    data: dict[str, int] = {'b': 1, 'a': 2}",
                "    print(json.dumps(data, indent=2, sort_keys=True))",
                "    print(json.dumps(data, sort_keys=True))",
                "    xs: list[int] = [1, 2, 3]",
                "    print(json.dumps(xs, indent=2))",
                "    nested: dict[str, list[int]] = {'k': [1, 2]}",
                "    print(json.dumps(nested, indent=2))",
                "    empty: list[int] = []",
                "    print(json.dumps(empty, indent=2))",
                "    none: dict[str, int] = {}",
                "    print(json.dumps(none, indent=2, sort_keys=True))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_json_dumps_unknown_keyword_fences() -> None:
    from pyscriptc.lower import lower_source

    mod = lower_source("import json\njson.dumps({'a': 1}, separators=(',', ':'))\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "json.dumps keyword separators is not lowered" in mod["fences"][0]["message"]
