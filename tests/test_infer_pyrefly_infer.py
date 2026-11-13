"""`pyrefly infer` supplies the facts the single-pass pins cannot chain (issue #731).

The call-site pins are a single pass over the checker's first answer: an
argument the checker types `Any` (an unannotated parameter of the caller)
refuses the pin, and the pinned re-check never recomputes pins. `pyrefly
infer` iterates the same proof to a fixpoint on the mirror copy, so a
parameter proven from one call site feeds the next call site's argument.

The case below is a two-module program whose `add_one` parameter only
becomes provable after `call_it`'s parameter is pinned: the pin proves
`v: int` from the `call_it(41)` call site, and `pyrefly infer` carries
that fact to `add_one(x)` and both return types. Without the infer pass
the chained definition keeps its PS1001 fences.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.diagnostics import DIAGNOSTICS

_HELPERS = "def add_one(x):\n    return x + 1\n\n\ndef call_it(v):\n    return add_one(v)\n"
_MAIN = (
    "from helpers import call_it\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(call_it(41))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_case(root, "helpers.py", _HELPERS)
    write_case(root, "__main__.py", _MAIN)


def _lower_with_infer(root: Path):
    """Lower the tree the way `compile --infer` wires the facts."""

    from pyscriptc.factsbridge import BridgeProvider, CheckerBridge
    from pyscriptc.inferrunner import build_facts, facts_as_pins, pinned_facts
    from pyscriptc.package import lower_directory

    facts = build_facts(root)
    pin_map = facts_as_pins(facts)
    facts_by_file = pinned_facts(root, facts)

    class _Provider(BridgeProvider):
        def for_file(self, path: Path):
            found = facts_by_file.get(path.resolve())
            bridge = CheckerBridge(found, self.records) if found is not None else super().for_file(path)
            bridge.call_site_facts = pin_map
            return bridge

    provider = _Provider("pyrefly", strict=False)
    provider.open()
    try:
        module, _source = lower_directory(root, program_mode=True, bridge=provider, config=None)
        return module, facts_by_file
    finally:
        provider.close()


def test_the_infer_pass_rechecks_the_files_it_annotated(tmp_path: Path) -> None:
    """The chained facts reach the bridge: `x: int` and `-> int` on `add_one`."""

    root = tmp_path / "inferred"
    _tree(root)

    _module, facts_by_file = _lower_with_infer(root)
    helpers = next(path for path in facts_by_file if path.name == "helpers.py")
    defs = facts_by_file[helpers].defs
    add_one = next(facts for facts in defs.values() if facts.name == "add_one")
    assert add_one.params["x"].kind == "int"
    assert add_one.return_type is not None
    assert add_one.return_type.kind == "int"


def test_the_chained_parameter_and_returns_lower_natively(tmp_path: Path) -> None:
    """`add_one` signs and lowers; the PS1001 fences are gone."""

    root = tmp_path / "inferred"
    _tree(root)

    module, _facts_by_file = _lower_with_infer(root)
    names = [fn["name"] for fn in module["functions"]]
    assert "helpers.add_one" in names
    assert "helpers.call_it" in names
    fences = [fence for fence in module["fences"] if fence["code"] == "PS1001"]
    assert not [fence for fence in fences if "needs an annotation" in fence["message"]]


def test_the_infer_report_breaks_the_fence_delta_down_by_family_and_code(tmp_path: Path) -> None:
    """`infer --json` says which fence family inference helped (issue #1171).

    The totals alone cannot answer "how much does inference help
    `param-inference` fences specifically": the delta is a single number
    over every family at once. The report carries the per-family and
    per-code rows so one run answers it.
    """

    root = tmp_path / "inferred"
    _tree(root)

    proc = run_cli("infer", str(root), "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)

    by_family = report["fences_by_family"]
    by_code = report["fences_by_code"]

    # The baseline fences the unannotated parameters, so the report has a
    # `param-inference` row to move.
    assert by_family["param-inference"]["baseline"] > 0
    assert by_family["param-inference"]["inferred"] == 0
    assert by_family["param-inference"]["delta"] == -by_family["param-inference"]["baseline"]

    # Every row is baseline, inferred and the delta between them, and the
    # rows add up to the totals the report already carried.
    for rows in (by_family, by_code):
        for row in rows.values():
            assert row["delta"] == row["inferred"] - row["baseline"]
        assert sum(row["baseline"] for row in rows.values()) == report["fences_baseline"]
        assert sum(row["inferred"] for row in rows.values()) == report["fences_inferred"]
        assert sum(row["delta"] for row in rows.values()) == report["fences_delta"]

    # The rows are the report's own vocabulary: coverage families and
    # diagnostic codes.
    assert "PS1001" in by_code
    assert all(code in DIAGNOSTICS for code in by_code)
    assert all(isinstance(family, str) and family for family in by_family)


def test_the_inferred_chain_runs_native_and_matches_cpython(tmp_path: Path) -> None:
    """`compile --infer` lowers the chain and the binary agrees with CPython."""

    root = tmp_path / "inferred"
    _tree(root)

    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "bin",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
    )
    assert proc.returncode == 0, proc.stderr
    binary = next((tmp_path / "out").rglob("inferred"))
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0
    assert run.stdout == "42\n"

    ll = (tmp_path / "out" / "inferred.ll").read_text(encoding="utf-8")
    assert "define i64 @helpers.add_one" in ll
