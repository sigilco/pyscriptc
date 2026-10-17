"""E1 item: for loops over a tuple literal iterable.

`_for_dispatch` rewrites a homogeneous `ast.Tuple` iterable into the
`ast.List` the loop already lowers, so iteration order and element binding
match CPython. An empty tuple emits no loop. A mixed tuple still fences,
because the loop target could not take a single type.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def _while_loops(stmts: list[dict]) -> list[dict]:
    return [stmt for stmt in stmts if stmt.get("kind") == "while"]


def test_tuple_literal_iterable_lowers_as_indexed_list_loop() -> None:
    mod = lower_source(
        """
def probe() -> int:
    total: int = 0
    for n in (10, 20, 27):
        total = total + n
    return total
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    loops = _while_loops(entry["body"])
    assert len(loops) == 1
    # The iterable is pinned into a seq temp typed list[int] before the loop.
    seq_assigns = [
        stmt for stmt in entry["body"] if stmt.get("kind") == "assign" and stmt.get("value", {}).get("kind") == "list"
    ]
    assert len(seq_assigns) == 1
    value = seq_assigns[0]["value"]
    assert value["type"] == {"kind": "list", "elem": {"kind": "int"}}
    assert [elt["value"] for elt in value["elems"]] == ["10", "20", "27"]


def test_empty_tuple_iterable_emits_no_loop() -> None:
    mod = lower_source(
        """
def probe() -> int:
    total: int = 0
    for n in ():
        total = total + 1
    return total
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    assert _while_loops(entry["body"]) == []


def test_mixed_tuple_iterable_still_fences() -> None:
    mod = lower_source(
        """
def probe() -> int:
    total: int = 0
    for n in (1, "x"):
        total = total + 1
    return total
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS1003"


def test_single_element_tuple_iterates_once() -> None:
    mod = lower_source(
        """
def probe() -> int:
    total: int = 0
    for n in (7,):
        total = total + n
    return total
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    assert len(_while_loops(entry["body"])) == 1


def test_tuple_literal_values_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "fortuple_vals.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    total: int = 0",
                "    for n in (10, 20, 27):",
                "        total = total * 1000 + n",
                "    for s in (3, 1, 4, 1, 5, 9):",
                "        total = total + s",
                "    return total",
                "",
            ]
        ),
    )
    out = str(tmp_path / "fortuple_vals")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
