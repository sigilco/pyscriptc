"""The vendored third-party license texts of the bundled runtime (#709).

The runtime pack is cut from a python-build-standalone `install_only` asset,
which ships no `licenses/` directory; the texts are vendored in the wheel as
package data instead, sha256-pinned in `packaging/pbs_licenses.toml`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_pbs_licenses import (  # noqa: E402
    full_archive_url,
    vendor,
    verify,
    write_pins,
)


def test_full_archive_url_matches_the_pinned_asset() -> None:
    url = full_archive_url(
        "20261021",
        "cpython-3.14.9+20261021-aarch64-apple-darwin-install_only_stripped.tar.gz",
    )
    assert url == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20261021/cpython-3.14.9+20261021-aarch64-apple-darwin-pgo+lto-full.tar.zst"
    )


def test_the_vendored_texts_verify_against_the_pins() -> None:
    assert verify() == 0


def test_vendor_roundtrip_and_tamper_detection(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "LICENSE.demo.txt").write_text("demo license", encoding="utf-8")
    vendored = tmp_path / "vendored"
    pins = tmp_path / "pins.toml"
    assert vendor(source, vendored, pins) == 0
    assert (vendored / "LICENSE.demo.txt").read_text(encoding="utf-8") == "demo license"
    assert '[license."LICENSE.demo.txt"]' in pins.read_text(encoding="utf-8")
    assert verify(vendored, pins) == 0
    (vendored / "LICENSE.demo.txt").write_text("tampered", encoding="utf-8")
    assert verify(vendored, pins) == 1


def test_verify_reports_an_unpinned_text(tmp_path: Path) -> None:
    vendored = tmp_path / "vendored"
    vendored.mkdir()
    pins = tmp_path / "pins.toml"
    write_pins({}, pins)
    (vendored / "LICENSE.extra.txt").write_text("extra", encoding="utf-8")
    assert verify(vendored, pins) == 1
