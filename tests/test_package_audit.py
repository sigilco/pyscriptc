# SPDX-License-Identifier: Apache-2.0
"""Audit events for the package paths: package.discover, package.catalog, package.plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pyscriptc.log import log_path
from pyscriptc.package import lower_directory


def _records(event: str) -> list[dict[str, Any]]:
    lines = log_path().read_text(encoding="utf-8").splitlines()
    return [r for r in (json.loads(line) for line in lines) if r.get("event") == event]


def test_directory_compile_emits_package_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_LOG_DIR", str(tmp_path / "audit"))
    root = tmp_path / "prog"
    root.mkdir()
    (root / "__main__.py").write_text("import helper\n\nprint(helper.twice(2))\n", encoding="utf-8")
    (root / "helper.py").write_text("X: int = 1\n\n\ndef twice(n: int) -> int:\n    return n * 2\n", encoding="utf-8")
    (root / "not-a-module.py").write_text("x = 1\n", encoding="utf-8")

    module, fences = lower_directory(root)
    assert fences == ""
    assert module is not None

    (discover,) = _records("package.discover")
    assert discover["root"] == str(root.resolve())
    assert Path(discover["root"]).is_dir()
    assert discover["module_count"] == 2
    assert discover["skipped"]["count"] == 1
    assert discover["skipped"]["paths"] == ["not-a-module.py"]
    assert all((root / p).exists() for p in discover["skipped"]["paths"])

    (catalog,) = _records("package.catalog")
    assert catalog["root"] == str(root.resolve())
    assert catalog["export_count"] >= 1
    assert catalog["failed_count"] == 0

    (plan,) = _records("package.plan")
    assert plan["root"] == str(root.resolve())
    assert plan["reached_count"] == 2
    assert plan["init_owners"]["count"] == len(plan["init_owners"]["names"])


def test_package_mode_emits_discover_and_catalog_but_no_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_LOG_DIR", str(tmp_path / "audit"))
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "core.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")

    module, _fences = lower_directory(root)
    assert module is not None
    assert len(_records("package.discover")) == 1
    assert len(_records("package.catalog")) == 1
    assert _records("package.plan") == []
