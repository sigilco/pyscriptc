"""Fetch the pinned mbedTLS release into vendor/mbedtls and apply the config trim.

Usage: python -I fetch_vendor.py [--force]

The pin is the upstream release tarball (URL plus every hash) plus the SHA-256
of the five files the adapter pins after the trim; the trim itself is the
client-only `mbedtls_config_client.h` next to this script, applied as a
deterministic `scripts/config.py unset` run per option, so the fetched tree
byte-matches the trimmed tree every host builds. Only the pin and the trim
are committed; the fetched tree is gitignored (the sqlite adapter's pattern).

The trim is the client-only config (issue #235): no server, no DTLS, TLS 1.2
and TLS 1.3 only, and the cipher and curve set a public HTTPS endpoint
negotiates. The commit that introduced it records the measured effect on the
linked binary (section GC plus this trim take the TLS hello binary from
1.46 MB to 373 KB).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
ROOT = VENDOR / "mbedtls"
# The trimmed config the adapter builds against: the client-only one.
CLIENT_CONFIG = Path(__file__).resolve().parent / "mbedtls_config_client.h"
# Files that must byte-match after the fetch and the trim.
PIN_FILES = (
    "include/mbedtls/mbedtls_config.h",
    "library/ssl_tls.c",
    "library/ssl_tls12_client.c",
    "library/ssl_tls13_client.c",
    "library/x509_crt.c",
)
# The config lines the client transport unsets. These are the DTLS (datagram
# TLS) options and the server-side session options the client seam never
# reaches; the trimmed config builds the same client handshake as the default.
UNSET = (
    "MBEDTLS_SSL_PROTO_DTLS",
    "MBEDTLS_SSL_DTLS_ANTI_REPLAY",
    "MBEDTLS_SSL_DTLS_HELLO_VERIFY",
    "MBEDTLS_SSL_COOKIE_C",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_fetched(pin: dict[str, str]) -> bool:
    if not (ROOT / "library" / "ssl_tls.c").is_file():
        return False
    return all(_sha256(ROOT / name) == pin[f"{name}_sha256"] for name in PIN_FILES)


def client_unset() -> list[str]:
    """The options the client-only trim switches off, read from the trim.

    The trim is a committed `mbedtls_config.h`: the upstream tree with the
    options the client transport never reaches commented out. The list is
    read from that file (the difference against the pinned upstream tree),
    so the committed trim is the single source of truth and the fetch
    reproduces it exactly.
    """
    import re

    text = CLIENT_CONFIG.read_text(encoding="utf-8")
    off = set(re.findall(r"^//#define\s+(MBEDTLS_[A-Z0-9_]+)", text, re.M))
    return sorted(off)


def fetch(force: bool = False) -> Path:
    pin = tomllib.loads((VENDOR / "VENDOR.toml").read_text(encoding="utf-8"))
    if not force and is_fetched(pin):
        return ROOT
    if ROOT.exists():
        shutil.rmtree(ROOT)
    VENDOR.mkdir(parents=True, exist_ok=True)
    tarball = VENDOR / "mbedtls.tar.bz2"
    with urllib.request.urlopen(pin["url"], timeout=120) as response:  # noqa: S310 - pinned https URL
        tarball.write_bytes(response.read())
    if _sha256(tarball) != pin["tarball_sha256"]:
        raise SystemExit("mbedtls tarball sha256 mismatch")
    with tarfile.open(tarball, "r:bz2") as archive:
        for member in archive.getmembers():
            if member.name.startswith(f"{pin['strip']}/") and not member.name.endswith("/"):
                target = ROOT / member.name[len(pin["strip"]) + 1 :]
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isfile():
                    target.write_bytes(archive.extractfile(member).read())  # noqa: S202 - pinned members only
                elif member.issym() or member.isdir():
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
    tarball.unlink()
    # The config trim, deterministically: the client-only options go off, in
    # the order the trim file lists them, one `config.py unset` per option.
    for name in (*UNSET, *client_unset()):
        proc = subprocess.run(
            [sys.executable, "scripts/config.py", "unset", name], cwd=ROOT, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise SystemExit(f"config unset {name} failed: {proc.stderr}")
    for name in PIN_FILES:
        got = _sha256(ROOT / name)
        want = pin[f"{name}_sha256"]
        if got != want:
            raise SystemExit(f"{name} sha256 mismatch after trim: {got} != {want}")
    return ROOT


if __name__ == "__main__":
    print(fetch(force="--force" in sys.argv[1:]))
