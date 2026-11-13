"""Tests for scripts/lower_split_report.py.

The fixture is a synthetic module with the shape the script targets: one
class built from mixin-style methods, shared attributes, a hub attribute,
a long method, and sibling modules for the package analyses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import lower_split_report as lsr  # noqa: E402

FIXTURE = '''\
"""A synthetic lowerer-like module."""

import ast
from typing import Any

from . import _fixture_helpers as fh

HUB_LIMIT = 4


class Lowerer:
    def __init__(self) -> None:
        self.temp_n = 0
        self.sigs: dict[str, Any] = {}
        self.locals: dict[str, Any] = {}
        self.slots: list[str] = []
        self.globals_inits: list[str] = []
        self.only_init = 0

    def _temp(self) -> str:
        self.temp_n += 1
        return f"t{self.temp_n}"

    def _signature(self, fn: ast.FunctionDef) -> dict[str, Any]:
        """Signature lowering: two methods that only touch each other."""
        body = self._pin_signature(fn)
        self.sigs[fn.name] = body
        return body

    def _pin_signature(self, fn: ast.FunctionDef) -> dict[str, Any]:
        if fh.needs_pin(fn):
            return {"pinned": True, "temp": self._temp()}
        return {"pinned": False}

    def _call_site_types(self, fn: ast.FunctionDef) -> dict[str, Any]:
        """A long method (over the 150-line threshold once padded)."""
        out = {}
        for arg in fn.args.args:
            self.locals[arg.arg] = self._infer_type_of(arg)
            out[arg.arg] = fh.wrap(self.locals[arg.arg])
        return out

    def _infer_type_of(self, node: ast.expr) -> dict[str, Any] | None:
        if isinstance(node, ast.Name):
            return self.locals.get(node.id)
        if isinstance(node, ast.Constant):
            return {"kind": "const"}
        return None

    def _bind_global_slot(self, name: str) -> str:
        """Global-slot family: slots, globals_inits, locals."""
        slot = fh.slot_name(name)
        self.slots.append(slot)
        self.globals_inits.append(slot)
        self.locals[slot] = None
        return slot

    def _global_get(self, name: str) -> ast.expr:
        slot = self._bind_global_slot(name)
        return ast.Name(id=slot, ctx=ast.Load())

    def module(self, tree: ast.Module) -> list[str]:
        """Entry point: calls the other families, writes nothing itself."""
        emitted = []
        if len(tree.body) > HUB_LIMIT:
            emitted.append(self._signature(tree.body[0]))
            emitted.append(self._call_site_types(tree.body[0]))
        for stmt in tree.body:
            emitted.append(self._global_get("x"))
        return emitted
'''


@pytest.fixture()
def pkg(tmp_path: Path) -> Path:
    """A fake package: the fixture module plus two siblings."""
    pkg = tmp_path / "fixturepkg"
    pkg.mkdir()
    helpers = (
        "def needs_pin(fn):\n"
        "    return True\n\n\n"
        "def wrap(t):\n"
        "    return t\n\n\n"
        "def slot_name(name):\n"
        "    return f'slot_{name}'\n"
    )
    (pkg / "_fixture_helpers.py").write_text(helpers, encoding="utf-8")
    (pkg / "__init__.py").write_text(FIXTURE, encoding="utf-8")
    sibling = "from . import _fixture_helpers as fh\n\n\ndef helper_user() -> int:\n    return fh.wrap(1)\n"
    (pkg / "_sibling.py").write_text(sibling, encoding="utf-8")
    # _sibling imports __init__ and __init__ imports nothing from _sibling:
    # no cycle. A second sibling pair forms a cycle.
    (pkg / "_cyc_a.py").write_text("from ._cyc_b import b\n\n\ndef a() -> int:\n    return b()\n", encoding="utf-8")
    (pkg / "_cyc_b.py").write_text("from ._cyc_a import a\n\n\ndef b() -> int:\n    return a()\n", encoding="utf-8")
    return pkg


def _methods(pkg: Path):
    report = lsr.analyse_module(pkg / "__init__.py")
    return report


def test_method_lines_and_attrs(pkg: Path) -> None:
    report = _methods(pkg)
    by_name = {m.name: m for m in report.methods}
    assert by_name["_signature"].lines > 0
    assert by_name["__init__"].attrs_written == {
        "temp_n",
        "sigs",
        "locals",
        "slots",
        "globals_inits",
        "only_init",
    }
    assert by_name["_signature"].calls_self == {"_pin_signature"}
    assert by_name["module"].calls_self == {"_signature", "_call_site_types", "_global_get"}
    assert "HUB_LIMIT" in by_name["module"].module_names
    assert "fh" in by_name["_pin_signature"].module_names
    assert "needs_pin" not in by_name["_pin_signature"].module_names
    assert "fn" not in by_name["_pin_signature"].module_names


def test_call_site_locals_do_not_leak(pkg: Path) -> None:
    report = _methods(pkg)
    by_name = {m.name: m for m in report.methods}
    assert "out" not in by_name["_call_site_types"].module_names
    assert "arg" not in by_name["_call_site_types"].module_names


def test_clustering_families(pkg: Path) -> None:
    report = _methods(pkg)
    clusters = lsr.cluster_methods(report.methods, attr_link=False)
    names = [{m.name for m in c} for c in clusters]
    assert any({"_signature", "_pin_signature"} <= n for n in names)
    assert any({"_bind_global_slot", "_global_get"} <= n for n in names)
    assert {"module"} in names


def test_hub_attribute_does_not_fuse(pkg: Path) -> None:
    report = _methods(pkg)
    clusters = lsr.cluster_methods(report.methods)
    names = [{m.name for m in c} for c in clusters]
    # temp_n is read/written by several families but must not fuse them all.
    assert names and {"module"} in names


def test_edges_and_entry_points(pkg: Path) -> None:
    report = _methods(pkg)
    clusters = lsr.cluster_methods(report.methods, attr_link=False)
    edges = lsr.cluster_edges(clusters)
    assert edges, "module calls into the other clusters"
    entries = lsr.cluster_entry_points(clusters, edges)
    called = {caller for callers in entries.values() for caller in callers}
    assert any("module" in caller for caller in called)


def test_state_and_init_only(pkg: Path) -> None:
    report = _methods(pkg)
    owners = lsr.state_owners(report.methods)
    assert "temp_n" in owners
    assert "slots" in owners
    assert set(owners["sigs"]) == {"Lowerer.__init__", "Lowerer._signature"}
    init_only = lsr.init_only_state(report.methods)
    assert "sigs" not in init_only
    assert "only_init" in init_only
    assert init_only  # only_init is written by __init__ alone


def test_suggest_cuts_reaches_target(pkg: Path) -> None:
    report = _methods(pkg)
    clusters = lsr.cluster_methods(report.methods, attr_link=False)
    edges = lsr.cluster_edges(clusters)
    total = sum(m.lines for m in report.methods)
    cuts = lsr.suggest_cuts(clusters, edges, target_lines=max(total - 30, 1))
    assert cuts
    best = lsr.best_cut(cuts)
    assert best is not None
    assert best["under_target"]


def test_unused_module_names(pkg: Path) -> None:
    report = _methods(pkg)
    unused = lsr.unused_module_names(report)
    # lower_file-style: defined, never referenced inside the module.
    assert "ast" not in unused
    assert "fh" not in unused


def test_import_cycles(pkg: Path) -> None:
    cycles = lsr.import_cycles(pkg)
    joined = {" ".join(c) for c in cycles}
    assert any("_cyc_a" in c and "_cyc_b" in c for c in joined)
    assert not any("__init__" in c and "_sibling" in c for c in joined)


def test_text_and_json_output(pkg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = _methods(pkg)
    text = lsr.format_text(report, pkg, target=1500, long_method=10)
    assert "== methods ==" in text
    assert "== clusters" in text
    assert "== extraction seeds" in text
    data = lsr.to_json(report, pkg, target=1500, long_method=10)
    blob = json.dumps(data)
    assert "_call_site_types" in blob
    assert data["total_lines"] == source_lines(pkg)


def source_lines(pkg: Path) -> int:
    return len((pkg / "__init__.py").read_text(encoding="utf-8").splitlines())


def test_main_cli(pkg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = lsr.main([str(pkg / "__init__.py"), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["module_name"] == "__init__" or data["module_name"] == "fixturepkg"
    rc = lsr.main([str(pkg / "__init__.py")])
    assert rc == 0
    assert "== methods ==" in capsys.readouterr().out


def test_complexity(pkg: Path) -> None:
    report = _methods(pkg)
    by_name = {m.name: m for m in report.methods}
    assert by_name["_infer_type_of"].complexity == 3  # 1 + two ifs
    assert by_name["__init__"].complexity == 1
