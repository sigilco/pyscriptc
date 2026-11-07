"""Unit tests for .github/scripts/probe_guard.py (pure classifier, no git)."""

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "probe_guard", Path(__file__).resolve().parent.parent / ".github" / "scripts" / "probe_guard.py"
)
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["probe_guard"] = pg
_SPEC.loader.exec_module(pg)


def test_no_lowering_change_skips():
    v = pg.classify(["docs/guide/typing.md", "tests/test_assert.py"])
    assert v.lowering == []
    assert not v.gap


def test_lowering_without_probe_is_a_gap():
    v = pg.classify(["src/pyscriptc/lower/_calls.py", "tests/test_calls.py"])
    assert v.lowering == ["src/pyscriptc/lower/_calls.py"]
    assert v.gap


def test_stdlib_pack_counts_as_lowering():
    v = pg.classify(["src/pyscriptc/restlower/_argparse.py"])
    assert v.gap


def test_runtime_helper_counts_as_lowering():
    v = pg.classify(["src/pyscriptc/runtime/ps_help.c"])
    assert v.gap


def test_mir_slice_counts_as_lowering():
    for path in (
        "src/pyscriptc/mir.py",
        "src/pyscriptc/mirlower.py",
        "src/pyscriptc/miremit.py",
    ):
        assert pg.classify([path]).gap, path


def test_a_probe_satisfies_the_rule():
    v = pg.classify(["src/pyscriptc/lower/_calls.py", "probes/differential/call_kw_default.py"])
    assert not v.gap
    assert v.probes == ["probes/differential/call_kw_default.py"]


def test_a_changed_probe_satisfies_the_rule():
    v = pg.classify(["src/pyscriptc/restlower/_json.py", "probes/divergences/d14.py"])
    assert not v.gap


def test_the_ledger_is_the_escape_hatch():
    v = pg.classify(["src/pyscriptc/lower/_classes.py", "docs/reference/divergence-ledger.json"])
    assert not v.gap
    assert v.ledger


def test_the_numbered_list_stands_in_for_the_ledger():
    v = pg.classify(["src/pyscriptc/lower/_classes.py", "docs/reference/divergences.md"])
    assert not v.gap


def test_tests_do_not_count_as_probes():
    v = pg.classify(["src/pyscriptc/lower/_stmt.py", "tests/test_stmt.py"])
    assert v.gap


def test_agent_probes_do_not_count():
    v = pg.classify(["src/pyscriptc/lower/_stmt.py", ".agents/probes/x.py"])
    assert v.gap


def test_emit_only_change_is_not_a_gap():
    v = pg.classify(["src/pyscriptc/emit/llvm_calls.py"])
    assert not v.gap


def test_block_mode_exit_code(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert pg.main(["--files", "src/pyscriptc/lower/_calls.py"]) == 0
    assert pg.main(["--block", "--files", "src/pyscriptc/lower/_calls.py"]) == 1
    assert (
        pg.main(
            [
                "--block",
                "--files",
                "src/pyscriptc/lower/_calls.py",
                "probes/differential/c.py",
            ]
        )
        == 0
    )
