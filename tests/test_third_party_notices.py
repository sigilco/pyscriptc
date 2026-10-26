"""The NOTICE generator: deterministic rollup of the bundled-runtime notices.

Issue #663: the compiler ships an Apache-2.0 NOTICE that names the third-party
components compiled into the bundled CPython runtime. The output is generated
from packaging/third-party.toml (and the runtime lock when one is pinned), so
a runtime bump or an inventory change is a one-input change, never an edit of
the generated file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_third_party_notices import (  # noqa: E402
    INVENTORY,
    NOTICE,
    cpython_line,
    main,
    render,
)


def test_inventory_is_wellformed() -> None:
    """Every component entry has the fields the NOTICE rollup quotes."""
    import tomllib

    data = tomllib.loads(INVENTORY.read_text(encoding="utf-8"))
    components = data["components"]
    assert len(components) >= 5
    for component in components:
        assert component["name"], component
        assert component["license"], component


def test_render_lists_components_without_a_lock() -> None:
    """No pinned pack: NOTICE still lists the inventory with a provenance note."""
    text = render(None, INVENTORY)
    assert "No runtime pack is pinned yet" in text
    assert "- expat" in text
    assert "- OpenSSL" in text
    assert "Apache License, Version 2.0" in text


def test_render_with_a_lock(tmp_path: Path) -> None:
    """A pinned lock names the CPython version and the PBS release tag."""
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        '[release]\ntag = "20261021"\n[cpython]\nversion = "3.14.9"\n',
        encoding="utf-8",
    )
    text = render(lock, INVENTORY)
    assert "CPython 3.14.9 (python-build-standalone release 20261021)" in text
    assert "No runtime pack is pinned" not in text


def test_cpython_line_from_a_full_lock() -> None:
    assert (
        cpython_line({"release": {"tag": "20261021"}, "cpython": {"version": "3.14.9"}})
        == "CPython 3.14.9 (python-build-standalone release 20261021)"
    )
    assert (
        cpython_line({"release": {"pbs": "20261001"}, "cpython": {"version": "3.14.8"}})
        == "CPython 3.14.8 (python-build-standalone release 20261001)"
    )
    assert "unknown" in cpython_line({})


def test_committed_notice_is_current() -> None:
    """The committed NOTICE matches the inputs (--check discipline)."""
    assert main(["--check"]) == 0, f"{NOTICE} is stale; regenerate it"


def test_check_reports_drift(tmp_path: Path, monkeypatch) -> None:
    """A stale NOTICE fails --check instead of being rewritten silently."""
    monkeypatch.setattr(sys, "argv", ["gen_third_party_notices.py", "--check"])
    real_notice = Path(NOTICE)

    class FakeNotice:
        @staticmethod
        def exists() -> bool:
            return True

        @staticmethod
        def read_text(encoding: str) -> str:
            return "stale"

    monkeypatch.setattr(sys.modules["scripts.gen_third_party_notices"], "NOTICE", FakeNotice)
    try:
        assert main(["--check"]) == 1
    finally:
        assert real_notice.exists()
