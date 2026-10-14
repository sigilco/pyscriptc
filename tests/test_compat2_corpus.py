from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.compat2.corpus import load as corpus

ROOT = Path(corpus.HERE)
SCRIPT_DIRS = sorted(p for base in (ROOT, ROOT / "holdout") for p in (base / "scripts").glob("*/"))


def test_default_load_never_includes_holdout():
    visible = {p.id for p in corpus.load()}
    held = {p.id for p in corpus.load(holdout=True)}
    assert visible and held
    assert not visible & held


def test_v1_pins_are_extended_not_edited():
    pins = corpus.package_pins()
    v1 = json.loads((ROOT.parent.parent / "compat" / "top100" / "pins.json").read_text())["packages"]
    for name, pin in v1.items():
        assert pins[name]["sha256"] == pin["sha256"]
        assert corpus.package_root(pins[name]).exists()


def test_every_new_pin_has_checksum_license_and_tree():
    for holdout in (False, True):
        for name, pin in corpus.package_pins(holdout).items():
            assert len(str(pin["sha256"])) == 64, name
            assert corpus.package_root(pin, holdout).exists(), name
            if pin.get("origin") == "v2":
                lic = ROOT / ("holdout/" if holdout else "") / "licenses" / str(pin["license_file"])
                assert lic.is_file(), name


def test_holdout_is_marked_and_disjoint():
    assert (ROOT / "holdout" / "HOLDOUT").is_file()
    assert not set(corpus.package_pins()) & set(corpus.package_pins(True))


@pytest.mark.parametrize("folder", SCRIPT_DIRS, ids=lambda p: p.name)
def test_script_expected_matches_cpython(folder: Path):
    assert (folder / "meta.json").is_file()
    run = subprocess.run([sys.executable, "-I", str(folder / "main.py")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout == (folder / "expected.txt").read_text(encoding="utf-8")


def test_probe_shapes():
    for probe in corpus.load():
        assert probe.kind == "value"
        if probe.category == "program":
            assert probe.call == "" and len(probe.args) == 1
        else:
            assert probe.call == "probe" and probe.source.rstrip().endswith(")")
            compile(probe.source, probe.id, "exec")


def test_probe_ids_are_unique():
    ids = [p.id for p in corpus.load()] + [p.id for p in corpus.load(holdout=True)]
    assert len(ids) == len(set(ids))
