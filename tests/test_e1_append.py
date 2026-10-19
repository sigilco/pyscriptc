"""E1 item A2: `list.append` on a name-bound list lowers in place.

The compiler routes `xs.append(v)` through the same IR the set surface and
list comprehensions already use: a `builtins`/`list.append` module call with
the `ps_list_push` symbol, emitted as the existing `ps_seq_push`. The list
value mutates in place, so the statement produces no result and returns the
module-level `None` constant, exactly like `set.add`. The receiver must be a
local Name of list type; element type must match; one positional argument.

Everything else keeps fencing: another method name is PS2001, argument-count
and keyword forms are PS2003, element type mismatch is PS1003, a frozen
(set-list) receiver is PS2002, and a non-Name receiver is PS2002.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.canonical import hash_module
from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def _pushes(stmts: list[dict]) -> list[dict]:
    found: list[dict] = []

    def walk(body: list[dict]) -> None:
        for stmt in body:
            expr = stmt.get("expr")
            if isinstance(expr, dict) and expr.get("symbol") == "ps_list_push":
                found.append(expr)
            for key in ("body", "then", "else"):
                if isinstance(stmt.get(key), list):
                    walk(stmt[key])

    walk(stmts)
    return found


def test_append_on_local_list_lowers_to_seq_push() -> None:
    mod = lower_source(
        """
def probe(p0: int) -> int:
    xs: list[int] = [1, 2]
    xs.append(p0)
    return len(xs)
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    push = _pushes(entry["body"])
    assert len(push) == 1
    call = push[0]
    assert call["callee"] == {"kind": "module", "module": "builtins", "name": "list.append"}
    assert call["symbol"] == "ps_list_push"
    assert call["type"] == {"kind": "none"}
    target, value = call["args"]
    assert target["kind"] == "name" and target["id"] == "xs"
    assert target["type"] == {"kind": "list", "elem": {"kind": "int"}}
    assert value["type"] == {"kind": "int"}


def test_append_statement_binds_none_like_set_add() -> None:
    mod = lower_source(
        """
def probe() -> int:
    xs: list[str] = ["a"]
    xs.append("b")
    return len(xs)
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    stmt = entry["body"][1]
    assert stmt["kind"] == "expr"
    assert stmt["expr"]["type"] == {"kind": "none"}


def test_append_across_call_boundary_matches_cpython(tmp_path: Path) -> None:
    # The mutation is in place on the heap value, so a callee's append is
    # visible to the caller after the call returns - the list does not
    # need to come back by value.
    path = write_case(
        tmp_path,
        "entry_append.py",
        "\n".join(
            [
                "def push(xs: list[int], v: int) -> int:",
                "    xs.append(v)",
                "    return 0",
                "",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1]",
                "    push(xs, 2)",
                "    xs.append(xs[0] + xs[1])",
                "    return len(xs) * 1000003 + xs[2]",
                "",
            ]
        ),
    )
    out = str(tmp_path / "entry_append")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "3000012"


def test_append_in_function_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "func_append.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    xs: list[int] = [1]",
                "    xs.append(2)",
                "    xs.append(xs[0] + xs[1])",
                "    return len(xs) * 1000003 + xs[2]",
                "",
            ]
        ),
    )
    out = str(tmp_path / "func_append")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "3000012"


def test_defonly_module_without_append_stays_byte_identical() -> None:
    # No append in the module: the module shape is untouched by the dispatch.
    # The hash pins the pre-existing shape of this exact module.
    mod = lower_source(
        """
def probe(xs: list[int]) -> int:
    return len(xs)
"""
    )
    assert mod["fences"] == []
    assert mod["entry"] == "probe"
    assert "main" not in {fn["name"] for fn in mod["functions"]}
    assert hash_module(mod) == "3b10e996f3e550c38c1711a9a89dbe44af0c0670cbc08a20bd23f4770c2df888"


def test_append_in_defonly_module_synthesizes_no_fence() -> None:
    # append only runs inside a function body here, so the module keeps the
    # pre-E1 shape: first function is the entry, no fences anywhere.
    mod = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    xs.append(2)
    return len(xs)
"""
    )
    assert mod["fences"] == []
    assert mod["entry"] == "probe"
    assert "main" not in {fn["name"] for fn in mod["functions"]}


def test_list_core_methods_lower_without_fences() -> None:
    # R2 batch: the core mutators and lookups joined append on the lowered
    # side. An outside-table call keeps the generic PS2001 wording.
    for call in (
        "pop(0)",
        "sort()",
        "extend([2])",
        "insert(0, 2)",
        "clear()",
        "index(1)",
        "count(1)",
        "remove(1)",
        "copy()",
    ):
        mod = lower_source(
            f"""
def probe() -> int:
    xs: list[int] = [1]
    xs.{call}
    return len(xs)
"""
        )
        assert mod["fences"] == [], call
    mod = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    xs.shuffle()
    return len(xs)
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2001"
    assert "call of attribute is not lowered (xs.shuffle)" in fence["message"]


def test_non_name_receivers_fence_ps2002() -> None:
    mod = lower_source(
        "def probe() -> int:\n    xs: list[list[int]] = [[1]]\n    xs[0].append(2)\n    return len(xs[0])\n"
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2002"
    assert "list.append needs a name" in fence["message"]


def test_append_on_attribute_receiver_keeps_field_fence() -> None:
    # self.xs without a declared field fences in the field-lookup surface
    # before the method dispatch runs; the wording stays there, pinned.
    mod = lower_source(
        """
class Host:
    def run(self) -> int:
        self.xs.append(1)
        return 0


def probe() -> int:
    return Host().run()
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2001"
    assert "Host.xs is not a field" in fence["message"]


def test_append_argument_arity_and_keywords_fence() -> None:
    none_args = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    xs.append()
    return len(xs)
"""
    )
    assert none_args["fences"][0]["code"] == "PS2003"
    assert "list.append expects 1 argument(s)" in none_args["fences"][0]["message"]
    two_args = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    xs.append(2, 3)
    return len(xs)
"""
    )
    assert two_args["fences"][0]["code"] == "PS2003"
    assert "list.append expects 1 argument(s)" in two_args["fences"][0]["message"]
    kwarg = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    xs.append(v=2)
    return len(xs)
"""
    )
    assert kwarg["fences"][0]["code"] == "PS2003"
    assert "keyword arguments are not lowered" in kwarg["fences"][0]["message"]


def test_append_starred_arg_fences_ps2002() -> None:
    mod = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    extra: list[int] = [2]
    xs.append(*extra)
    return len(xs)
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2002"
    assert "Starred is not lowered" in fence["message"]


def test_append_element_type_mismatch_fences_ps1003() -> None:
    mod = lower_source(
        """
def probe() -> int:
    xs: list[int] = [1]
    ys: list[str] = ["a"]
    xs.append(ys[0])
    return len(xs)
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS1003"
    assert "list.append takes an element of that list" in fence["message"]


def test_append_str_and_float_elements_run_like_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "elems.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    words: list[str] = ["a"]',
                '    words.append("b")',
                "    nums: list[float] = [1.5]",
                "    nums.append(2.5)",
                "    return words[0] + words[1] + str(nums[0] + nums[1])",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "elems"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "ab4.0"


def test_append_rejects_cpython_type_errors_up_front(tmp_path: Path) -> None:
    # The typed front end still rejects the programs CPython rejects; the
    # lowering only owns the well-typed residue.
    bad = write_case(
        tmp_path,
        "bad.py",
        "def probe() -> int:\n    xs: list[int] = [1]\n    xs.append([2])\n    return 0\n",
    )
    got = run_cli("compile", str(bad), "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 2, got.stderr
    assert "is not assignable to parameter" in got.stderr


if __name__ == "__main__":
    sys.exit(0)
