"""Verify the vendored Ryu sources against the pinned hashes.

The Ryu files are committed (they are small), so this script does not download
anything by default: it re-derives the tarball hashes from the pinned release
and checks every committed file against `VENDOR.toml`. With `--fetch` it
downloads the pinned tarball into a scratch directory and re-verifies the
members before they are copied over the committed tree.

Usage: python -I fetch_vendor.py [--fetch]
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent
PIN = VENDOR / "VENDOR.toml"

# The committed file -> the path it has inside the upstream tarball.
MEMBERS = {
    "ryu.h": "ryu-2.0/ryu/ryu.h",
    "common.h": "ryu-2.0/ryu/common.h",
    "d2s.h": "ryu-2.0/ryu/d2s.h",
    "d2s.c": "ryu-2.0/ryu/d2s.c",
    "d2s_full_table.h": "ryu-2.0/ryu/d2s_full_table.h",
    "d2s_intrinsics.h": "ryu-2.0/ryu/d2s_intrinsics.h",
    "digit_table.h": "ryu-2.0/ryu/digit_table.h",
    "LICENSE-Apache2": "ryu-2.0/LICENSE-Apache2",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pin() -> dict[str, str]:
    return tomllib.loads(PIN.read_text(encoding="utf-8"))


def _expected(pin: dict[str, str], name: str) -> str:
    key = f"{name}_sha256" if name == "LICENSE-Apache2" else f"ryu/{name}_sha256"
    try:
        return pin[key]
    except KeyError:
        raise SystemExit(f"no pinned hash for {name} in {PIN.name}") from None


def verify() -> list[str]:
    """Check every committed file against its pin. Returns the checked names."""
    pin = _pin()
    checked = []
    for name in MEMBERS:
        path = VENDOR / name
        if not path.is_file():
            raise SystemExit(f"missing vendored file: {path}")
        digest = _sha256(path.read_bytes())
        if digest != _expected(pin, name):
            raise SystemExit(f"{name} sha256 mismatch: {digest}")
        checked.append(name)
    return checked


def fetch() -> None:
    """Download the pinned tarball and re-verify every member before writing."""
    pin = _pin()
    with urllib.request.urlopen(pin["url"], timeout=60) as response:  # noqa: S310 - pinned https URL
        blob = response.read()
    if _sha256(blob) != pin["tarball_sha256"]:
        raise SystemExit("ryu tarball sha256 mismatch")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for name, member in MEMBERS.items():
            data = archive.extractfile(member).read()  # type: ignore[union-attr]
            if _sha256(data) != _expected(pin, name):
                raise SystemExit(f"{name} sha256 mismatch in the tarball")
            (VENDOR / name).write_bytes(data)
    verify()


if __name__ == "__main__":
    if "--fetch" in sys.argv[1:]:
        with tempfile.TemporaryDirectory() as scratch:
            fetch()
    else:
        for checked in verify():
            print(f"ok {checked}")
