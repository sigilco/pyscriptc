# SPDX-License-Identifier: Apache-2.0
"""OrderedDict and defaultdict in the value form (issue #491).

The annotation carries the key and value types; `OrderedDict[K, V]` marks
the dict `ordered` and `defaultdict[K, V]` marks it `defaultdict`, and the
marks ride into the repr spec so `print` spells CPython's
`OrderedDict({...})` and `defaultdict(<class 'list'>, {...})`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc._irnode import t_dict, t_leaf, t_list
from pyscriptc.reprspec import type_spec
from pyscriptc.tirserde import node_to_dict

_ROOT = Path(__file__).resolve().parents[1]
_PROBE = _ROOT / "probes" / "differential" / "collections_valueform.py"


def _spec(t: object) -> str | None:
    return type_spec(node_to_dict(t))


def test_repr_spec_marks() -> None:
    s, i = t_leaf("str"), t_leaf("int")
    assert _spec(t_dict(s, i)) == "dsi"
    assert _spec(t_dict(s, i, ordered=True)) == "odsi"
    assert _spec(t_dict(s, t_list(i), defaultdict=True)) == "Dldsli"
    assert _spec(t_dict(i, t_list(s, True), defaultdict=True)) == "Dtdits"
    assert _spec(t_dict(s, i, defaultdict=True)) == "Didsi"
    assert _spec(t_dict(s, t_leaf("float"), defaultdict=True)) == "Dfdsf"
    assert _spec(t_dict(s, s, defaultdict=True)) == "Dsdss"
    assert _spec(t_dict(s, t_dict(s, i), defaultdict=True)) == "Dddsdsi"
    # A nested mark rides the value spec.
    assert _spec(t_dict(s, t_dict(s, i, defaultdict=True))) == "dsDidsi"
    # A defaultdict whose value has no factory spelling has no spec.
    assert _spec(t_dict(s, t_leaf("bool"), defaultdict=True)) is None


def _ir_specs(tmp_path: Path, source: str) -> list[str]:
    import json

    path = write_case(tmp_path, "case.py", source)
    out = tmp_path / "dbg"
    got = run_cli(
        "compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "case.ll"), "--debug-dir", str(out)
    )
    assert got.returncode == 0, got.stderr
    specs: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("symbol") == "ps_map_repr":
                specs.append(node["args"][1]["value"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads((out / "ir.json").read_text()))
    return specs


def test_print_lowers_marked_specs(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from collections import OrderedDict, defaultdict",
            "",
            "def main() -> None:",
            "    od: OrderedDict[str, int] = OrderedDict()",
            "    print(od)",
            "    plain: dict[str, int] = {}",
            "    print(plain)",
            "    dl: defaultdict[str, list[int]] = defaultdict(list)",
            "    print(dl)",
            "",
            "main()",
            "",
        ]
    )
    assert _ir_specs(tmp_path, source) == ["odsi", "dsi", "Dldsli"]


def test_ordereddict_annotation_equality_fences(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from collections import OrderedDict",
            "",
            "def probe() -> bool:",
            "    od: OrderedDict[str, int] = OrderedDict()",
            "    od2: OrderedDict[str, int] = OrderedDict()",
            "    return od == od2",
            "",
        ]
    )
    path = write_case(tmp_path, "eq.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "eq.ll"))
    assert got.returncode == 3, got.stderr
    assert "OrderedDict equality is not lowered" in got.stderr


@pytest.mark.parametrize(
    ("source", "text"),
    [
        ("from collections import ChainMap\n", "collections.ChainMap is not in the lowered subset of collections"),
        ("from collections import UserDict\n", "collections.UserDict is not in the lowered subset of collections"),
    ],
)
def test_names_outside_subset_fence(tmp_path: Path, source: str, text: str) -> None:
    path = write_case(tmp_path, "out.py", source + "\ndef probe() -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2001" in got.stderr
    assert text in got.stderr


@pytest.mark.linux_container
def test_valueform_probe_matches_cpython() -> None:
    # `verify` runs the pinned CPython 3.14 oracle next to the native build.
    got = run_cli("verify", str(_PROBE))
    assert got.returncode == 0, got.stdout + got.stderr
    assert ": match" in got.stdout, got.stdout
