"""Optional widening (Stage U): nested `None` arms, container payloads, PS1102.

`int | None | None` and `None | int` collapse to the same tagged optional as
`int | None`. `list[int | None]` and the boolop joins of two optionals were
already lowered; they are pinned here with emitted-IR and run tests. An
optional of a multi-arm union (`int | str | None`) fences as PS1102, a plain
multi-arm union stays PS1101.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _param_type(source: str) -> dict:
    mod = lower_source(source, "case.py")
    assert mod["fences"] == []
    fn = next(f for f in mod["functions"] if f["name"] == "f")
    return fn["params"][0]["type"]


@pytest.mark.parametrize("annotation", ["int | None", "None | int", "int | None | None", "None | int | None"])
def test_pipe_forms_share_one_optional_type(annotation: str) -> None:
    typ = _param_type(f"def f(x: {annotation}) -> int:\n    return 1\n")
    assert typ["optional"] is True
    assert typ["name"] == "opt_int"
    assert typ["elem"] == {"kind": "int"}


def test_optional_inside_list_annotation() -> None:
    typ = _param_type("def f(xs: list[int | None]) -> int:\n    return len(xs)\n")
    assert typ["kind"] == "list"
    assert typ["elem"]["optional"] is True and typ["elem"]["elem"] == {"kind": "int"}


def test_boolop_join_of_two_optionals_is_optional() -> None:
    mod = lower_source(
        "def f(a: int | None, b: int | None) -> int | None:\n    return a or b\n",
        "case.py",
    )
    assert mod["fences"] == []


@pytest.mark.parametrize(
    ("annotation", "code"),
    [
        ("int | str | None", "PS1102"),
        ("None | int | str", "PS1102"),
        ("Union[int, str, None]", "PS1102"),
        ("int | str", "PS1101"),
        ("Union[int, str]", "PS1101"),
        ("int | str | bytes", "PS1101"),
    ],
)
def test_multi_arm_fences_pick_the_right_code(annotation: str, code: str) -> None:
    source = f"from typing import Union\n\ndef f(x: {annotation}) -> int:\n    return 1\n"
    mod = lower_source(source, "case.py")
    assert {fence["function"]: fence["code"] for fence in mod["fences"]} == {"f": code}


def test_nested_none_arms_run_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "opt_nested.py",
        "\n".join(
            [
                "def a(x: int | None | None) -> int:",
                "    if x is None:",
                "        return -1",
                "    return x",
                "",
                "def b(x: None | int) -> int:",
                "    if x is not None:",
                "        return x + 1",
                "    return 0",
                "",
                "def probe() -> int:",
                "    return a(None) * 1000 + a(7) * 100 + b(None) * 10 + b(4)",
                "",
            ]
        ),
    )
    out = str(tmp_path / "opt_nested")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "-295"
