#!/usr/bin/env python3
"""Vendor the third-party license texts of the bundled runtime into the wheel.

The pack is cut from a python-build-standalone (PBS) `install_only_stripped`
asset (packaging/runtime.lock.toml), which ships no `licenses/` directory; the
`full` archive of the same release does. This script vendors the texts of that
`full` archive into `src/pyscriptc/pbs_licenses/` as package data, so an
installed compiler can stage them into every `--embed-python=bundle` output
(issue #709) without a repository checkout.

`packaging/pbs_licenses.toml` pins the sha256 of every vendored text; the
default mode verifies the vendored copy against it and exits 1 on drift.

Usage:
    uv run python scripts/sync_pbs_licenses.py                # verify only
    uv run python scripts/sync_pbs_licenses.py --from-dir DIR # vendor DIR, refresh the pins
    uv run python scripts/sync_pbs_licenses.py --fetch        # download the full archives, vendor them
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging" / "runtime.lock.toml"
HASHES = ROOT / "packaging" / "pbs_licenses.toml"
VENDORED = ROOT / "src" / "pyscriptc" / "pbs_licenses"
ARCHIVE_PREFIX = "python/licenses/"


def _read_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_archive_url(release: str, asset: str) -> str:
    """The `full` archive URL matching a pinned `install_only_stripped` asset."""
    full_asset = asset.replace("install_only_stripped", "pgo+lto-full").replace(".tar.gz", ".tar.zst")
    return f"https://github.com/astral-sh/python-build-standalone/releases/download/{release}/{full_asset}"


def fetch_licenses(url: str, dest: Path) -> Path:
    """Download a PBS archive and extract its `licenses/` directory into `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pbs-licenses-") as tmp:
        archive = Path(tmp) / "full.tar.zst"
        request = urllib.request.Request(  # noqa: S310 - fixed https URL
            url, headers={"User-Agent": "pyscriptc-license-sync"}
        )
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            with archive.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        with tarfile.open(archive, "r:*") as tar:
            members = [m for m in tar.getmembers() if m.name.startswith(ARCHIVE_PREFIX)]
            tar.extractall(tmp, members=members)  # noqa: S202 - fixed prefix, members filtered above
        source = Path(tmp).joinpath(*ARCHIVE_PREFIX.split("/"))
        for item in sorted(source.iterdir()):
            if item.is_file():
                shutil.copy2(item, dest / item.name)
    return dest


def write_pins(sources: dict[str, Path], hashes_path: Path = HASHES) -> None:
    """Write the pin file for the vendored texts."""
    lines = [
        "# sha256 of the license texts vendored in src/pyscriptc/pbs_licenses/.",
        "#",
        "# The texts come from the `licenses/` directory of the python-build-",
        "# standalone `full` archive of the pinned release (the `install_only`",
        "# asset the runtime pack is cut from ships no such directory). Refresh",
        "# them with `uv run python scripts/sync_pbs_licenses.py --fetch`.",
        "",
        "schema = 1",
        "",
    ]
    for name in sorted(sources):
        lines.append(f'[license."{name}"]')
        lines.append(f'sha256 = "{_sha256(sources[name])}"')
        lines.append("")
    hashes_path.write_text("\n".join(lines), encoding="utf-8")


def verify(vendored: Path = VENDORED, hashes_path: Path = HASHES) -> int:
    """Check the vendored texts against the pins; return the number of problems."""
    if not hashes_path.exists():
        print(f"{hashes_path} is absent: the license pins are missing", file=sys.stderr)
        return 1
    pins = _read_toml(hashes_path).get("license", {})
    problems = 0
    for name in sorted(pins):
        expected = pins[name]["sha256"]
        path = vendored / name
        if not path.is_file():
            print(f"{name}: missing from {vendored}", file=sys.stderr)
            problems += 1
            continue
        actual = _sha256(path)
        if actual != expected:
            print(f"{name}: sha256 {actual} != pinned {expected}", file=sys.stderr)
            problems += 1
    for path in sorted(vendored.glob("LICENSE.*")):
        if path.name not in pins:
            print(f"{path.name}: vendored but not pinned", file=sys.stderr)
            problems += 1
    if problems:
        print(
            "the vendored license texts are stale; refresh them with "
            "`uv run python scripts/sync_pbs_licenses.py --fetch`",
            file=sys.stderr,
        )
    else:
        print(f"{len(pins)} license texts verified")
    return problems


def vendor(source: Path, vendored: Path = VENDORED, hashes_path: Path = HASHES) -> int:
    """Copy the license texts of `source` into the package data and re-pin them."""
    texts = {path.name: path for path in sorted(source.glob("LICENSE.*")) if path.is_file()}
    if not texts:
        print(f"{source} carries no LICENSE.* text", file=sys.stderr)
        return 1
    vendored.mkdir(parents=True, exist_ok=True)
    for stale in vendored.glob("LICENSE.*"):
        stale.unlink()
    for name, path in texts.items():
        shutil.copy2(path, vendored / name)
    write_pins(texts, hashes_path)
    print(f"vendored {len(texts)} license texts into {vendored}")
    return 0


def fetch(lock_path: Path = LOCK, vendored: Path = VENDORED, hashes_path: Path = HASHES) -> int:
    """Download the pinned `full` archives and vendor their license texts."""
    if not lock_path.exists():
        print("packaging/runtime.lock.toml is absent: nothing to fetch", file=sys.stderr)
        return 1
    lock = _read_toml(lock_path)
    release = str(lock["release"])
    cpython = str(lock["cpython"])
    with tempfile.TemporaryDirectory(prefix="pbs-licenses-") as tmp:
        root = Path(tmp)
        for platform, entry in sorted(lock["runtime"][cpython].items()):
            url = full_archive_url(release, str(entry["pbs_asset"]))
            print(f"fetching {platform}: {url}")
            fetch_licenses(url, root / platform)
        sources = {}
        for path in sorted(root.glob("*/*")):
            if path.is_file():
                sources[path.name] = path
        if not sources:
            print("the full archives carry no license text", file=sys.stderr)
            return 1
        vendored.mkdir(parents=True, exist_ok=True)
        for stale in vendored.glob("LICENSE.*"):
            stale.unlink()
        for name, path in sources.items():
            shutil.copy2(path, vendored / name)
        write_pins(sources, hashes_path)
        print(f"vendored {len(sources)} license texts into {vendored}")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify only (the default)")
    parser.add_argument("--from-dir", type=Path, default=None, help="vendor the texts of this directory")
    parser.add_argument("--fetch", action="store_true", help="download the pinned full archives")
    args = parser.parse_args(argv)
    if args.from_dir is not None:
        return vendor(args.from_dir)
    if args.fetch:
        return fetch()
    return 1 if verify() else 0


if __name__ == "__main__":
    sys.exit(main())
