"""E1 item C19: negative slice steps walk backwards.

`_slice_step` no longer fences a negative step. The same `builtins`/`slice`
call reaches `ps_seq_slice` (list) or `ps_str_slice` (str and bytes), where a
step below zero flips the bounds defaults like CPython: missing start is
n - 1, missing stop walks through index 0, and out-of-range bounds clamp to
[-1, n - 1]. Step 0 stays the runtime's ValueError.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source
from pyscriptc.tirserde import dict_to_node


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def _slices(stmts: list[object]) -> list[object]:
    found: list[object] = []

    def walk(body: list[object]) -> None:
        for stmt in body:
            node = dict_to_node(stmt)
            expr = node.expr if node.expr is not None else node.value
            expr = dict_to_node(expr) if expr is not None else None
            callee_src = None
            if expr is not None:
                callee_src = expr["callee"] if isinstance(expr, dict) else expr.callee
            callee = dict_to_node(callee_src) if callee_src is not None else None
            if (
                callee is not None
                and getattr(callee, "kind", None) == "module"
                and callee.module == "builtins"
                and callee.name == "slice"
            ):
                found.append(expr)
            for child in (node.body, node.then, node.else_):
                if isinstance(child, list):
                    walk(child)

    walk(stmts)
    return found


def test_reverse_step_lowers_with_const_negative_step() -> None:
    mod = lower_source(
        """
def probe(xs: list[int]) -> list[int]:
    return xs[::-1]
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    slices = _slices(entry["body"])
    assert len(slices) == 1
    call = dict_to_node(slices[0])
    assert call.symbol == "ps_seq_slice"
    value, start, stop, has_start, has_stop, step = call.args
    assert value.type.kind == "list"
    assert has_start.kind == "const" and has_start.svalue == "0"
    assert has_stop.kind == "const" and has_stop.svalue == "0"
    # The unary -1 is not a const or name, so it pins into a temp.
    assert step.kind == "name" and step.type.kind == "int"
    prelude = dict_to_node(entry["body"][0])
    assert prelude.kind == "assign" and prelude.target == step.id
    assert prelude.value.kind == "unary" and prelude.value.op == "-"
    assert prelude.value.operand.svalue == "1"


def test_str_reverse_uses_str_slice() -> None:
    mod = lower_source(
        """
def probe(s: str) -> str:
    return s[::-1]
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    slices = _slices(entry["body"])
    assert len(slices) == 1
    call = dict_to_node(slices[0])
    assert call.symbol == "ps_str_slice"
    assert call.type.kind == "str"
    step = call.args[-1]
    assert step.kind == "name" and step.type.kind == "int"


def test_variable_negative_step_lowers_once() -> None:
    # A non-const step is pinned into a temp so its expression runs once.
    mod = lower_source(
        """
def probe(xs: list[int], k: int) -> list[int]:
    return xs[::0 - k]
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    slices = _slices(entry["body"])
    assert len(slices) == 1
    step = dict_to_node(slices[0]).args[-1]
    assert step.kind == "name"
    assert step.type.kind == "int"


def test_str_non_step_operands_keep_fence_wordings() -> None:
    mod = lower_source(
        """
def probe(s: str) -> str:
    k: str = "x"
    return s[::k]
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS1003"
    assert "a slice step is int" in fence["message"]


def test_negative_step_values_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "negstep_vals.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    xs: list[int] = [0, 1, 2, 3, 4]",
                "    a: list[int] = xs[::-1]",
                "    b: list[int] = xs[3:0:-2]",
                "    c: list[int] = xs[:1:-1]",
                "    d: list[int] = xs[-2::-2]",
                "    e: list[int] = xs[100:-100:-3]",
                "    f: list[int] = xs[1:4:-1]",
                "    total: int = a[0] + 10 * a[4] + 100 * len(b) + 1000 * b[1]",
                "    return total + 10000 * len(c) + 100000 * d[0] + 1000000 * len(e) + 10000000 * len(f)",
                "",
            ]
        ),
    )
    out = str(tmp_path / "negstep_vals")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_negative_step_str_and_empty_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "negstep_str.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    s: str = "abcdef"',
                "    xs: list[int] = []",
                "    empty: list[int] = xs[::-1]",
                "    return s[::-1] + s[4:1:-2] + s[::-3] + str(len(empty))",
                "",
            ]
        ),
    )
    out = str(tmp_path / "negstep_str")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "fedcbaecfc0"


def test_negative_step_variable_step_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "negstep_var.py",
        "\n".join(
            [
                "def probe(k: int) -> int:",
                "    xs: list[int] = [0, 1, 2, 3, 4]",
                "    ys: list[int] = xs[::k]",
                "    return len(ys) * 1000003 + ys[0]",
                "",
            ]
        ),
    )
    out = str(tmp_path / "negstep_var")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "--args", "[-2]", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe", "[-2]")


def test_zero_step_still_raises_at_runtime(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "zerostep.py",
        "\n".join(
            [
                "def probe(k: int) -> int:",
                "    xs: list[int] = [1, 2]",
                "    return len(xs[::k])",
                "",
            ]
        ),
    )
    out = str(tmp_path / "zerostep")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "--args", "[0]", "-o", out)
    assert got.returncode != 0
    assert "slice step cannot be zero" in got.stderr or "slice step cannot be zero" in got.stdout


if __name__ == "__main__":
    sys.exit(0)
