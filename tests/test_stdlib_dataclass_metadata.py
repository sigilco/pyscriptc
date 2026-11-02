"""The compile-time class metadata tables.

`dataclasses.fields`, `asdict`, `astuple`, `replace` and `vars` read the
field table the compiler already builds from the checked annotations, so
no runtime schema exists in the binary. Each probe diffs stdout against
the CPython oracle; the fences keep their named diagnostic.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_fields_names_and_flags(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "fields.py",
        "\n".join(
            [
                "from dataclasses import dataclass, fields",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int = 5",
                "",
                "def run() -> int:",
                "    for f in fields(Point):",
                "        print(f.name, f.kw_only, f.init, f.repr)",
                "    return 0",
                "",
            ]
        ),
    )


def test_asdict_reads_the_field_table(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "asdict.py",
        "\n".join(
            [
                "from dataclasses import dataclass, asdict",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int",
                "",
                "def run() -> int:",
                "    d = asdict(Point(1, 2))",
                "    print(d['x'], d['y'])",
                "    return 0",
                "",
            ]
        ),
    )


def test_astuple_reads_the_field_table(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "astuple.py",
        "\n".join(
            [
                "from dataclasses import dataclass, astuple",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int",
                "",
                "def run() -> int:",
                "    t = astuple(Point(1, 2))",
                "    print(t[0], t[1])",
                "    return 0",
                "",
            ]
        ),
    )


def test_replace_reuses_the_constructor(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "replace.py",
        "\n".join(
            [
                "from dataclasses import dataclass, replace",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int = 5",
                "",
                "def run() -> int:",
                "    p = Point(1)",
                "    q = replace(p, y=9)",
                "    print(q.x, q.y)",
                "    r = replace(p)",
                "    print(r.x, r.y)",
                "    return 0",
                "",
            ]
        ),
    )


def test_vars_reads_the_field_table(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "vars.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int",
                "",
                "def run() -> int:",
                "    d = vars(Point(1, 2))",
                "    print(d['x'], d['y'])",
                "    return 0",
                "",
            ]
        ),
    )


def test_field_options_reach_the_table(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "fieldopts.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field, fields",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: int = field(default=2, kw_only=True)",
                "",
                "def run() -> int:",
                "    for f in fields(Point):",
                "        print(f.name, f.kw_only)",
                "    p = Point(1, y=3)",
                "    print(p.x, p.y)",
                "    return 0",
                "",
            ]
        ),
    )


def test_init_false_field_is_not_a_constructor_argument(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "initfalse.py",
        "\n".join(
            [
                "from dataclasses import dataclass, field, fields",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    tag: int = field(default=7, init=False)",
                "",
                "def run() -> int:",
                "    p = Point(1)",
                "    print(p.x, p.tag)",
                "    for f in fields(Point):",
                "        print(f.name, f.init)",
                "    return 0",
                "",
            ]
        ),
    )


def test_heterogeneous_fields_keep_the_dict_fence(tmp_path: Path) -> None:
    """A dict result carries one value type, so mixed fields fence."""

    path = write_case(
        tmp_path,
        "mixed.py",
        "\n".join(
            [
                "from dataclasses import dataclass, asdict",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "    y: str",
                "",
                "def run() -> int:",
                "    d = asdict(Point(1, 'a'))",
                "    print(d)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "mixed.ll"))
    assert got.returncode == 3, got.stderr
    assert "needs one field type" in got.stderr


def test_wrong_field_name_in_replace_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "replace_bad.py",
        "\n".join(
            [
                "from dataclasses import dataclass, replace",
                "",
                "@dataclass",
                "class Point:",
                "    x: int",
                "",
                "def run() -> int:",
                "    p = replace(Point(1), z=2)",
                "    print(p)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2" in got.stderr
