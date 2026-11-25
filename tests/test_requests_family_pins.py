"""The #1186 requests-family pin contract and its measured graduation gates.

The pins themselves landed in #1301; these tests keep the contract the issue
asked for honest: the three packages are pinned in both pin files (the v1
top100 pin file and the compat2 corpus pins), the corpus carries their rows,
and the two graduation gates the issue names stay closed — PS1101 no longer
exists in the diagnostics registry and no `circular import` fence fires on
any pinned family tree (the decision record:
`.agents/plans/decisions/2026-11-25-requests-family-gates.md`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyscriptc.coverage import build_report
from pyscriptc.diagnostics import DIAGNOSTICS

ROOT = Path(__file__).resolve().parents[1]
TOP100_PINS = ROOT / "benchmarks" / "compat" / "top100" / "pins.json"
CORPUS_PINS = ROOT / "benchmarks" / "compat2" / "corpus" / "pins.json"
PACKAGE_SPEC = ROOT / "benchmarks" / "compat2" / "corpus" / "package_spec.json"

FAMILY = {
    "requests": "requests",
    "urllib3": "urllib3",
    "charset-normalizer": "charset_normalizer",
}
# certifi graduated in v0.7.0; idna was already pinned before #1301.
GRADUATED = {"certifi": "certifi"}


def _pins(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["packages"]


def _tree(pkg: str) -> Path:
    pin = _pins(TOP100_PINS)[pkg]
    return ROOT / "benchmarks" / "compat" / "top100" / str(pin["tree"])


@pytest.mark.parametrize("project", sorted([*FAMILY, *GRADUATED]))
def test_family_is_pinned_in_both_pin_files(project: str) -> None:
    v1 = _pins(TOP100_PINS)[project]
    corpus = _pins(CORPUS_PINS)[project]
    assert v1["sha256"] == corpus["sha256"], project
    assert v1["version"] == corpus["version"], project
    assert _tree(project).is_dir(), project


@pytest.mark.parametrize("project", sorted(FAMILY))
def test_corpus_spec_carries_the_family_row(project: str) -> None:
    spec = json.loads(PACKAGE_SPEC.read_text(encoding="utf-8"))["packages"][project]
    pin = _pins(TOP100_PINS)[project]
    assert spec["import_name"] == pin["import_name"]
    assert spec["version"] == pin["version"]


@pytest.mark.parametrize("project", sorted(FAMILY))
def test_corpus_carries_a_probe_row_per_family_package(project: str) -> None:
    # The issue asked for the family to be measurable in the compat2 corpus:
    # one program probe per pinned package plus the closed-signature
    # function probes the spec builder found.
    from benchmarks.compat2.corpus import load as corpus

    ids = {probe.id for probe in corpus.load()}
    assert f"top100.{project}" in ids, project


def test_ps1101_is_retired_from_the_registry() -> None:
    # #413 retired the code: a multi-arm union erases to its first arm and the
    # later arms fence at the use site with PS1003. If a gate ever reintroduces
    # PS1101, the #1186 decision record is stale and must be revisited.
    assert "PS1101" not in DIAGNOSTICS


@pytest.mark.parametrize("pkg", sorted(FAMILY), ids=lambda p: p)
def test_no_circular_import_or_ps1101_fence_on_the_family(pkg: str) -> None:
    """Both #1186 graduation gates stay closed on the pinned trees.

    Measured 2026-11-25 (the decision record): PS1101 fires 0 times on the
    family (the code is retired) and the `circular import` cycle refusal
    (`package.py`) fires 0 times — #598 fixed the submodule false positive
    and #414's pruning keeps unreached modules out of the root set.
    """

    report = build_report(_tree(pkg))
    for fence in report["fences"]:
        assert fence["code"] != "PS1101", (pkg, fence.get("file"), fence["message"])
        assert "circular import" not in fence["message"], (pkg, fence.get("file"))
