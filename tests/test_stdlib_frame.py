"""The pyscriptc_frame slice-1 surface (issue #497).

The compile-run cases diff the compiled program against the pinned
CPython. The oracle for `pyscriptc_frame` is not real pandas (the surface
mirrors pandas' semantics on a closed subset, but the module is
pyscriptc-native), so the oracle programs rewrite the calls onto plain
dicts and lists: the values the compiled run prints must equal what the
same operations produce on CPython dicts of lists. The column order and
the int/float spelling of the printed values ride the same comparison.

Every fence case checks the PS2001 naming of the module, or the
PS100x/PS200x of the arguments outside the slice-1 subset.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, write_case

from pyscriptc.lower import lower_source


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    # `--call run` echoes the return value on its own line; the cases all
    # return 0, so the echo is a constant suffix.
    assert got.stdout.endswith("\n0\n"), got.stdout
    return got.stdout[: -len("\n0\n")] + "\n"


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


# ---------------------------------------------------------------------
# construction and the metadata
# ---------------------------------------------------------------------


def test_construction_matches_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_construction.py",
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2, 3], 'b': [4.5, 5.5, 6.5]})",
                "    print(df.columns)",
                "    print(len(df))",
                "    print(df.shape)",
                "    return 0",
            ]
        ),
    )
    # CPython: a dict of lists keeps its insertion order.
    data = {"a": [1, 2, 3], "b": [4.5, 5.5, 6.5]}
    assert got == (f"{list(data)}\n{len(data['a'])}\n{(len(data['a']), len(data))}\n")


def test_column_read_matches_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_getcol.py",
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2, 3], 'b': [4.5, 5.5, 6.5]})",
                "    print(df['a'].tolist())",
                "    print(df['b'].tolist())",
                "    print(df['b'].sum())",
                "    return 0",
            ]
        ),
    )
    b = [4.5, 5.5, 6.5]
    assert got == (f"[1, 2, 3]\n[4.5, 5.5, 6.5]\n{sum(b)}\n")


def test_setitem_replaces_the_column(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_setcol.py",
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2, 3]})",
                "    df['a'] = pd.DataFrame({'x': [7, 8, 9]})['x']",
                "    print(df['a'].tolist())",
                "    print(df.columns)",
                "    return 0",
            ]
        ),
    )
    assert got == "[7, 8, 9]\n['a']\n"


def test_head_tail_match_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_headtail.py",
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6]})",
                "    print(df.head(2)['a'].tolist())",
                "    print(df.tail(2)['a'].tolist())",
                "    print(df.head()['a'].tolist())",
                "    print(df.tail(99)['a'].tolist())",
                "    print(df.head(0)['a'].tolist())",
                "    return 0",
            ]
        ),
    )
    a = [1, 2, 3, 4, 5, 6]
    assert got == (f"{a[:2]}\n{a[-2:]}\n{a[:5]}\n{a}\n[]\n")


def test_to_dict_matches_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_todict.py",
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})",
                "    d = df.to_dict()",
                "    print(len(d))",
                "    print(d['a'])",
                "    print(d['b'])",
                "    return 0",
            ]
        ),
    )
    data = {"a": [1, 2, 3], "b": [4, 5, 6]}
    assert got == (f"{len(data)}\n{data['a']}\n{data['b']}\n")


def test_pandas_alias_lowers_identically(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "fr_pandas_alias.py",
        "\n".join(
            [
                "import pandas as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2]})",
                "    print(len(df))",
                "    return 0",
            ]
        ),
    )
    assert got == "2\n"


# ---------------------------------------------------------------------
# the fences
# ---------------------------------------------------------------------


def test_fences_name_the_module() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1]})",
                "    df = pd.read_csv('f.csv')",
                "    return 0",
            ]
        ),
        "PS2001",
        "pyscriptc_frame.read_csv is not in the lowered subset of pyscriptc_frame",
    )


def test_fences_method_outside_subset() -> None:
    for method, spelling in (
        ("merge", "df.merge(df)"),
        ("join", "df.join(df)"),
        ("concat", "df.concat(df)"),
        ("pivot", "df.pivot()"),
        ("melt", "df.melt()"),
        ("apply", "df.apply(None)"),
        ("describe", "df.describe()"),
    ):
        _fence(
            "\n".join(
                [
                    "import pyscriptc_frame as pd",
                    "",
                    "def run() -> int:",
                    "    df = pd.DataFrame({'a': [1]})",
                    f"    {spelling}",
                    "    return 0",
                ]
            ),
            "PS2001",
            f"pyscriptc_frame.{method} is not in the lowered subset of pyscriptc_frame",
        )


def test_fences_groupby_and_sort_values() -> None:
    for spelling in ('df.groupby("a").sum()', 'df.sort_values("a")', 'df.sort_values("a", ascending=False)'):
        _fence(
            "\n".join(
                [
                    "import pyscriptc_frame as pd",
                    "",
                    "def run() -> int:",
                    "    df = pd.DataFrame({'a': [1]})",
                    f"    {spelling}",
                    "    return 0",
                ]
            ),
            "PS2001",
            "is not in the lowered subset of pyscriptc_frame",
        )


def test_fences_indexers_and_attributes() -> None:
    for spelling in ("df.loc[0]", "df.iloc[0]", "x: int = df.dtypes"):
        _fence(
            "\n".join(
                [
                    "import pyscriptc_frame as pd",
                    "",
                    "def run() -> int:",
                    "    df = pd.DataFrame({'a': [1]})",
                    f"    {spelling}",
                    "    return 0",
                ]
            ),
            "PS2001",
            "pyscriptc_frame",
        )


def test_fences_column_outside_schema() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2]})",
                "    n: int = len(df['b'])",
                "    return 0",
            ]
        ),
        "PS2001",
        "the frame has no column b",
    )


def test_fences_new_column_setitem() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2]})",
                "    df['b'] = df['a']",
                "    return 0",
            ]
        ),
        "PS2001",
        "the frame has no column b",
    )


def test_fences_dtype_mismatch_setitem() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2]})",
                "    df['a'] = pd.DataFrame({'x': [1.5]})['x']",
                "    return 0",
            ]
        ),
        "PS1003",
        "takes an ndarray[int64]",
    )


def test_fences_to_dict_mixed_frame() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2], 'b': [1.5]})",
                "    d = df.to_dict()",
                "    return 0",
            ]
        ),
        "PS2001",
        "homogeneous frame",
    )


def test_fences_to_dict_records_orient() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': [1, 2]})",
                '    d = df.to_dict(orient="records")',
                "    return 0",
            ]
        ),
        "PS2001",
        "orient='records'",
    )


def test_fences_non_display_constructor() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run(xs: dict[str, list[int]]) -> int:",
                "    df = pd.DataFrame(xs)",
                "    return 0",
            ]
        ),
        "PS2003",
        "takes a dict display of columns",
    )


def test_fences_str_column() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({'a': ['x']})",
                "    return 0",
            ]
        ),
        "PS1003",
        "is a list of numbers",
    )


def test_fences_non_string_column_name() -> None:
    _fence(
        "\n".join(
            [
                "import pyscriptc_frame as pd",
                "",
                "def run() -> int:",
                "    df = pd.DataFrame({1: [1, 2]})",
                "    return 0",
            ]
        ),
        "PS1003",
        "column name is a string",
    )


def test_demo_program_has_no_fences() -> None:
    from pyscriptc.lower import lower_source

    src = "\n".join(
        [
            "import pyscriptc_frame as pd",
            "",
            "def run() -> int:",
            "    df = pd.DataFrame({'status': [1, 2, 2], 'bytes': [10, 20, 30]})",
            "    print(df.columns)",
            "    print(len(df))",
            "    print(df.shape)",
            "    print(df['bytes'].tolist())",
            "    print(df.head(1)['bytes'].tolist())",
            "    print(df.tail(1)['bytes'].sum())",
            "    return 0",
        ]
    )
    mod = lower_source(src)
    assert not mod["fences"], mod["fences"]
