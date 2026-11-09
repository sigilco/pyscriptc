"""The P6 inventory script ranks the named modules by raw dict-IR reads."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("dict_ir_consumers", ROOT / "scripts" / "dict_ir_consumers.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_covers_the_p6_set() -> None:
    mod = _load()
    names = set(mod.MODULES)
    assert "cli/_report.py" in names
    assert "coverage.py" in names
    assert "package.py" in names
    assert "boxed.py" in names
    rows = {row["module"] for row in mod.inventory()}
    assert rows == names


def test_measure_counts_subscript_and_get(tmp_path: Path) -> None:
    mod = _load()
    path = tmp_path / "probe.py"
    path.write_text(
        'module["functions"]\n'
        'module.get("classes", [])\n'
        'node["kind"]\n'
        'node.get("kind")\n'
        'fence["span"]\n'
        'stmt["body"]\n'
        '{"kind": "const"}\n',
        encoding="utf-8",
    )
    row = mod.measure_path(path)
    assert row["reads"] == 6
    assert row["by_key"] == {
        "kind": 2,
        "functions": 1,
        "classes": 1,
        "body": 1,
        "span": 1,
    }
    assert row["dict_build"] == 1


def test_raw_read_count_matches_measure() -> None:
    mod = _load()
    path = ROOT / "src" / "pyscriptc" / "emitlib.py"
    assert mod.raw_read_count(path) == 0


def test_ranking_puts_nonzero_reads_first() -> None:
    mod = _load()
    rows = mod.inventory()
    reads = [int(row["reads"]) for row in rows]
    assert reads == sorted(reads, reverse=True)
    text = mod.ranking_markdown(rows)
    assert "P6 dict-IR consumer inventory" in text
    assert "`package.py`" in text
