"""Bare-container `isinstance` must fence, never crash.

`isinstance(x, dict)` / `isinstance(x, list)` used to lower the bare
container to an incomplete type dict (`{"kind": "dict", "bare": True}`),
and a mismatch against a concrete type then crashed `ir.type_key` with a
`KeyError`. Bare `dict`/`list` now fence `PS2001` like bare `set`/`tuple`.
The bare branches in `_isinstance_holds`/`ir.type_key` stay as belt-and-
braces for any future incomplete container type dict.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.ir import type_key, types_equal
from pyscriptc.lower import lower_source


def test_bare_container_isinstance_fences_without_crashing(tmp_path: Path) -> None:
    """A bare container type against a concrete one fences PS2001, never a KeyError."""
    cases = [
        ("dict", "def f(x: str) -> bool:\n    return isinstance(x, dict)\n"),
        ("list", "def f(x: str) -> bool:\n    return isinstance(x, list)\n"),
        ("set", "def f(x: str) -> bool:\n    return isinstance(x, set)\n"),
        ("tuple", "def f(x: str) -> bool:\n    return isinstance(x, tuple)\n"),
        # A bare container against a matching container parameter must also
        # fence, not fold to True from an incomplete type dict.
        ("dict_match", "def f(x: dict[str, int]) -> bool:\n    return isinstance(x, dict)\n"),
        ("list_match", "def f(x: list[int]) -> bool:\n    return isinstance(x, list)\n"),
    ]
    for name, source in cases:
        path = write_case(tmp_path, f"bare_{name}.py", source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS2001" in got.stderr, f"{name}\n{got.stderr}"
        assert "isinstance type is not in the lowered subset" in got.stderr, f"{name}\n{got.stderr}"


def test_isinstance_scalar_mismatch_is_constant_false(tmp_path: Path) -> None:
    """A complete scalar mismatch still folds to a constant false, not a fence."""
    path = write_case(
        tmp_path,
        "scalar_mismatch.py",
        "\n".join(
            [
                "def f(x: str) -> int:",
                "    if isinstance(x, int):",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )
    module = lower_source(path.read_text(encoding="utf-8"), str(path))
    fn = next(item for item in module["functions"] if item["name"] == "f")
    conditions = [stmt["cond"] for stmt in fn["body"] if stmt.get("kind") == "if"]
    assert [cond["value"] for cond in conditions] == [False], fn["body"]


def test_type_key_tolerates_incomplete_bare_containers() -> None:
    """A bare container type dict must not crash type_key/types_equal."""
    assert type_key({"kind": "dict", "bare": True}) == "dict"
    assert type_key({"kind": "list", "bare": True}) == "list"
    assert not types_equal({"kind": "dict", "bare": True}, {"kind": "str"})
    assert types_equal({"kind": "list", "bare": True}, {"kind": "list", "bare": True})
