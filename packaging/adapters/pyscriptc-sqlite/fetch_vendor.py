"""Fetch the pinned SQLite amalgamation into vendor/ and verify every hash.

Usage: python -I fetch_vendor.py [--force]
"""

from __future__ import annotations

import hashlib
import io
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
FILES = ("sqlite3.c", "sqlite3.h")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_fetched(pin: dict[str, str]) -> bool:
    """True when both files exist and match the pinned hashes."""
    for name in FILES:
        path = VENDOR / name
        if not path.is_file() or _sha256(path.read_bytes()) != pin[f"{name}_sha256"]:
            return False
    return True


def fetch(force: bool = False) -> Path:
    pin = tomllib.loads((VENDOR / "VENDOR.toml").read_text(encoding="utf-8"))
    if not force and is_fetched(pin):
        return VENDOR
    with urllib.request.urlopen(pin["url"], timeout=60) as response:  # noqa: S310 - pinned https URL
        blob = response.read()
    if _sha256(blob) != pin["zip_sha256"]:
        raise SystemExit("sqlite zip sha256 mismatch")
    if hashlib.sha3_256(blob).hexdigest() != pin["zip_sha3_256"]:
        raise SystemExit("sqlite zip sha3-256 does not match the value sqlite.org publishes")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for name in FILES:
            member = next(m for m in archive.namelist() if m.endswith("/" + name))
            data = archive.read(member)
            if _sha256(data) != pin[f"{name}_sha256"]:
                raise SystemExit(f"{name} sha256 mismatch")
            (VENDOR / name).write_bytes(data)
    return VENDOR


if __name__ == "__main__":
    print(fetch(force="--force" in sys.argv[1:]))
