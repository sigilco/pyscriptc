"""The share/licenses tree of a bundle output (issue #709).

The staged tree must be writable from an installed package: the vendored
texts and the NOTICE rollup are package data, the CPython text comes from the
pack, and the dist-info licenses come from whatever #660 put into the
bundle's site-packages.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pyscriptc import licenses

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "src" / "pyscriptc" / "pbs_licenses"
PINS = ROOT / "packaging" / "pbs_licenses.toml"


def test_vendored_texts_match_the_pins() -> None:
    """Every vendored text hashes to its packaging/pbs_licenses.toml pin."""
    import hashlib

    pins = tomllib.loads(PINS.read_text(encoding="utf-8"))["license"]
    assert len(pins) >= 10
    for name, pin in pins.items():
        path = VENDORED / name
        assert path.is_file(), name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == pin["sha256"], name
    for path in VENDORED.glob("LICENSE.*"):
        assert path.name in pins, f"{path.name} is vendored but not pinned"


def test_vendored_cpython_text_is_the_psf_license() -> None:
    text = (VENDORED / "LICENSE.cpython.txt").read_text(encoding="utf-8")
    assert "1. This LICENSE AGREEMENT is between the Python Software Foundation" in text
    assert "PSF License Version 2" in text


def test_packaged_notice_matches_the_repo_notice() -> None:
    """The packaged copy is regenerated together with the root NOTICE."""
    assert (VENDORED / "NOTICE").read_text(encoding="utf-8") == (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_stage_pack_licenses_skips_the_notice(tmp_path: Path) -> None:
    dest = tmp_path / "licenses"
    dest.mkdir()
    staged = licenses.stage_pack_licenses(dest)
    assert staged and "NOTICE" not in staged
    assert (dest / "LICENSE.expat.txt").is_file()
    assert not (dest / "NOTICE").exists()


def test_stage_notice_is_a_no_op_without_package_data(tmp_path: Path) -> None:
    dest = tmp_path / "licenses"
    dest.mkdir()
    assert licenses.stage_notice(dest) is True
    assert (dest / "NOTICE").is_file()


def test_stage_cpython_license_copies_from_the_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    (pack / "lib" / "python3.14").mkdir(parents=True)
    (pack / "lib" / "python3.14" / "LICENSE.txt").write_text("pack text", encoding="utf-8")
    dest = tmp_path / "licenses"
    dest.mkdir()
    assert licenses.stage_cpython_license(dest, pack, "3.14") is True
    assert (dest / "LICENSE.cpython.txt").read_text(encoding="utf-8") == "pack text"


def test_stage_cpython_license_tolerates_a_textless_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    (pack / "lib" / "python3.14").mkdir(parents=True)
    dest = tmp_path / "licenses"
    dest.mkdir()
    assert licenses.stage_cpython_license(dest, pack, "3.14") is False
    assert not (dest / "LICENSE.cpython.txt").exists()


def test_stage_site_licenses_copies_dist_info_trees(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    dist_info = site / "example-1.0.dist-info"
    (dist_info / "licenses" / "vendor").mkdir(parents=True)
    (dist_info / "licenses" / "LICENSE.txt").write_text("example", encoding="utf-8")
    (dist_info / "licenses" / "vendor" / "NOTICE.txt").write_text("vendored", encoding="utf-8")
    bare = site / "bare-1.0.dist-info"
    bare.mkdir(parents=True)
    dest = tmp_path / "licenses"
    dest.mkdir()
    staged = licenses.stage_site_licenses(site, dest)
    assert staged == ["example-1.0"]
    assert (dest / "example-1.0" / "LICENSE.txt").read_text(encoding="utf-8") == "example"
    assert (dest / "example-1.0" / "vendor" / "NOTICE.txt").read_text(encoding="utf-8") == "vendored"
    assert not (dest / "bare-1.0").exists()


def test_stage_site_licenses_tolerates_a_missing_tree(tmp_path: Path) -> None:
    staged = licenses.stage_site_licenses(tmp_path / "absent", tmp_path / "licenses")
    assert staged == []


def test_stage_share_licenses_writes_the_whole_tree(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    (pack / "lib" / "python3.14").mkdir(parents=True)
    (pack / "lib" / "python3.14" / "LICENSE.txt").write_text("pack text", encoding="utf-8")
    site = tmp_path / "site-packages"
    dist_info = site / "example-1.0.dist-info"
    (dist_info / "licenses").mkdir(parents=True)
    (dist_info / "licenses" / "LICENSE.txt").write_text("example", encoding="utf-8")
    output = tmp_path / "prog"
    output.mkdir()
    dest = licenses.stage_share_licenses(output, pack_directory=pack, version="3.14", site_packages=site)
    assert dest == output / "share" / "licenses"
    assert (dest / "LICENSE.cpython.txt").read_text(encoding="utf-8") == "pack text"
    assert (dest / "LICENSE.expat.txt").is_file()
    assert (dest / "NOTICE").is_file()
    assert (dest / "example-1.0" / "LICENSE.txt").is_file()
