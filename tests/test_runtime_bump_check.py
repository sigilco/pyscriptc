"""The runtime-bump check: the pinned micro against the PBS release list.

Issue #664. The scheduled workflow (.github/workflows/runtime-bump.yml) runs
scripts/check_runtime_bump.py; these tests pin its decision logic so a bump
PR opens exactly when a newer 3.14 micro exists, and never for a lock that
does not exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_runtime_bump import (  # noqa: E402
    newest_micro,
    pinned_release,
    pinned_version,
    release_micros,
    report,
)


def test_newest_micro_picks_the_newer_micro() -> None:
    tags = ["3.14.9+20261021", "3.14.8+20261014", "3.14.7+20261001"]
    assert newest_micro(tags, "3.14.9") is None
    assert newest_micro(tags, "3.14.8") == "3.14.9"
    assert newest_micro(tags, "3.14.0") == "3.14.9"


def test_newest_micro_ignores_other_minors() -> None:
    assert newest_micro(["3.14.9"], "3.14.9") is None
    assert newest_micro(["3.13.10"], "3.14.9") is None


def test_newest_micro_handles_duplicate_and_unsorted() -> None:
    assert newest_micro(["3.14.7", "3.14.9", "3.14.7"], "3.14.8") == "3.14.9"


def test_pinned_fields_from_a_lock(tmp_path: Path) -> None:
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        '[release]\ntag = "20261021"\n[cpython]\nversion = "3.14.9"\n',
        encoding="utf-8",
    )
    assert pinned_version(lock) == "3.14.9"
    assert pinned_release(lock) == "20261021"


def test_report_without_a_lock_is_a_clean_no_op(tmp_path: Path) -> None:
    data = report(tmp_path / "absent.toml")
    assert data["status"] == "no-lock"
    assert data["newest"] is None


def test_report_current_when_the_list_has_nothing_newer(monkeypatch, tmp_path: Path) -> None:
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        '[release]\ntag = "20261021"\n[cpython]\nversion = "3.14.9"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys.modules["scripts.check_runtime_bump"],
        "fetch_pbs_releases",
        lambda: [{"tag_name": "20261021"}, {"tag_name": "20261014"}],
    )
    data = report(lock)
    assert data == {"status": "current", "pinned": "3.14.9", "newest": None, "tag": "20261021"}


def test_report_bump_when_a_newer_micro_exists(monkeypatch, tmp_path: Path) -> None:
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        '[release]\ntag = "20261014"\n[cpython]\nversion = "3.14.8"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys.modules["scripts.check_runtime_bump"],
        "fetch_pbs_releases",
        lambda: [
            {
                "tag_name": "20261021",
                "assets": [{"name": "cpython-3.14.9+20261021-x86_64-unknown-linux-gnu.tar.gz"}],
            },
            {"tag_name": "20261014", "assets": []},
        ],
    )
    data = report(lock)
    assert data["status"] == "bump"
    assert data["newest"] == "3.14.9"
    assert data["tag"] == "20261021"


def test_report_unreachable_is_not_a_bump(monkeypatch, tmp_path: Path) -> None:
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        '[release]\ntag = "20261021"\n[cpython]\nversion = "3.14.9"\n',
        encoding="utf-8",
    )

    def boom() -> list[dict]:
        raise OSError("rate limited")

    monkeypatch.setattr(sys.modules["scripts.check_runtime_bump"], "fetch_pbs_releases", boom)
    data = report(lock)
    assert data["status"] == "unreachable"
    assert data["newest"] is None


def test_release_micros_read_asset_names() -> None:
    release = {
        "tag_name": "20261021",
        "assets": [
            {"name": "cpython-3.14.9+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz"},
            {"name": "cpython-3.14.9+20261021-aarch64-apple-darwin-install_only.tar.gz"},
            {"name": "SHA256SUMS"},
        ],
    }
    assert release_micros(release) == ["3.14.9", "3.14.9"]
