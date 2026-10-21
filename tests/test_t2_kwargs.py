"""T2-4: `**kwargs` normalization.

A `**kwargs: T` parameter lowers as a trailing `dict[str, T]` param on the
signature (mirroring `*args: T` -> `list[T]`); call sites bind leftover named
keywords into a dict literal in declaration order, so `kwargs` behaves as a
plain dict in the body (`in`, `[]`, `.get`, iteration over keys).

The dynamic `f(**d)` call-site unpack stays fenced (PS2002): a `dict[str, T]`
carries no static key set, so the lowering cannot prove which declared params
its members bind to nor whether the remainder fits the kwargs dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def test_kwargs_param_appends_a_dict_param() -> None:
    mod = lower_source(
        """
def probe(a: int, **kwargs: int) -> int:
    return a
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    params = entry["params"]
    assert params[-1] == {
        "name": "kwargs",
        "type": {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "int"}},
        "kwarg": True,
    }


def test_call_site_binds_leftover_keywords_in_declaration_order() -> None:
    mod = lower_source(
        """
def probe(a: int, **kwargs: int) -> int:
    return a
def run() -> int:
    return probe(1, x=2, y=3)
"""
    )
    assert mod["fences"] == []
    run = next(fn for fn in mod["functions"] if fn["name"] == "run")
    call = run["body"][-1]["value"]
    packed = call["args"][-1]
    assert packed["kind"] == "dict"
    assert [pair["key"]["value"] for pair in packed["pairs"]] == ["x", "y"]
    assert [pair["value"]["value"] for pair in packed["pairs"]] == ["2", "3"]


def test_keyword_matching_a_declared_param_does_not_pack() -> None:
    mod = lower_source(
        """
def probe(a: int, b: int, **kwargs: int) -> int:
    return a + b
def run() -> int:
    return probe(b=2, a=1, c=3)
"""
    )
    assert mod["fences"] == []
    run = next(fn for fn in mod["functions"] if fn["name"] == "run")
    call = run["body"][-1]["value"]
    assert [arg["value"] for arg in call["args"][:2]] == ["1", "2"]
    packed = call["args"][-1]
    assert [pair["key"]["value"] for pair in packed["pairs"]] == ["c"]


def test_empty_kwargs_packs_an_empty_dict() -> None:
    mod = lower_source(
        """
def probe(a: int, **kwargs: int) -> int:
    return a
def run() -> int:
    return probe(1)
"""
    )
    assert mod["fences"] == []
    run = next(fn for fn in mod["functions"] if fn["name"] == "run")
    call = run["body"][-1]["value"]
    assert call["args"][-1] == {
        "kind": "dict",
        "pairs": [],
        "type": {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "int"}},
        "span": call["args"][-1]["span"],
    }


def test_kwargs_and_varargs_pack_together() -> None:
    mod = lower_source(
        """
def probe(a: int, *xs: int, **kwargs: int) -> int:
    return a
def run() -> int:
    return probe(1, 2, 3, x=4)
"""
    )
    assert mod["fences"] == []
    run = next(fn for fn in mod["functions"] if fn["name"] == "run")
    call = run["body"][-1]["value"]
    assert call["args"][1]["kind"] == "list"
    assert call["args"][2]["kind"] == "dict"


def test_starstar_unpack_stays_fenced() -> None:
    mod = lower_source(
        """
def probe(**kwargs: int) -> int:
    return len(kwargs)
def run() -> int:
    d: dict[str, int] = {"x": 1}
    return probe(**d)
"""
    )
    fences = [fence for fn in mod["functions"] for fence in fn.get("fences", [])]
    assert mod["fences"] or fences
    codes = {fence["code"] for fence in mod["fences"] + fences}
    assert "PS2002" in codes


def test_unannotated_kwargs_keeps_the_parameter_form_fence() -> None:
    mod = lower_source(
        """
def probe(**kwargs) -> int:
    return 0
"""
    )
    assert any(fence["code"] == "PS1202" for fence in mod["fences"])


def test_kwargs_bool_values_fence() -> None:
    mod = lower_source(
        """
def probe(**kwargs: bool) -> int:
    return 0
"""
    )
    assert any(fence["code"] == "PS2002" for fence in mod["fences"])


def test_extra_positional_without_vararg_fences() -> None:
    mod = lower_source(
        """
def probe(a: int, **kwargs: int) -> int:
    return a
def run() -> int:
    return probe(1, 2)
"""
    )
    run = next((fn for fn in mod["functions"] if fn["name"] == "run"), None)
    fences = (run or {}).get("fences", []) + mod["fences"]
    assert any(fence["code"] == "PS1003" for fence in fences)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_kwargs_capture_iteration_membership_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kwargs.py",
        "\n".join(
            [
                "def total(a: int, **kwargs: int) -> int:",
                "    acc: int = a",
                "    for key in kwargs:",
                "        acc = acc + kwargs[key]",
                '    if "x" in kwargs:',
                "        acc = acc + 100",
                '    if "nope" in kwargs:',
                "        return 0",
                '    return acc + kwargs.get("missing", 7)',
                "def run() -> int:",
                "    return total(1, x=2, y=3) + total(5)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_kwargs_with_varargs_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kwvar.py",
        "\n".join(
            [
                "def gather(a: int, *xs: int, **kwargs: int) -> int:",
                "    acc: int = a",
                "    for v in xs:",
                "        acc = acc + v",
                "    for key in kwargs:",
                "        acc = acc + kwargs[key]",
                "    return acc",
                "def run() -> int:",
                "    return gather(1, 2, 3, x=4, y=5) + gather(9)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
