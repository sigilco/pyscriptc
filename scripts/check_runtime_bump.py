#!/usr/bin/env python3
"""Check whether a newer CPython 3.14 micro exists for the bundled runtime.

The runtime pack is cut from a pinned python-build-standalone (PBS) release
recorded in packaging/runtime.lock.toml (docs/reference/runtime-pack.md). This
script answers one question: is there a newer 3.14 micro to bump to?

It is used by .github/workflows/runtime-bump.yml (weekly schedule plus
workflow_dispatch) and by the manual bump procedure in
.agents/docs/release/embed-runtime.md. It only reads the lock file and the
PBS release list; it never writes the lock and never publishes anything.

Usage:
    uv run python scripts/check_runtime_bump.py            # report, exit 1 when a newer micro exists
    uv run python scripts/check_runtime_bump.py --json     # machine-readable report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging" / "runtime.lock.toml"

PBS_RELEASES_URL = "https://api.github.com/repos/astral-sh/python-build-standalone/releases"
MICRO_RE = re.compile(r"3\.14\.\d+")


def pinned_version(lock_path: Path = LOCK) -> str:
    """The CPython micro the lock file pins."""
    with lock_path.open("rb") as fh:
        lock = tomllib.load(fh)
    return str(lock["cpython"]["version"])


def pinned_release(lock_path: Path = LOCK) -> str:
    """The PBS release tag the lock file pins."""
    with lock_path.open("rb") as fh:
        lock = tomllib.load(fh)
    release = lock.get("release", {})
    return str(release.get("tag") or release.get("pbs") or "")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def newest_micro(candidates: list[str], current: str) -> str | None:
    """The newest 3.14 micro in `candidates` that is newer than `current`.

    Returns None when there is nothing to bump to.
    """
    micros = sorted({m for c in candidates for m in MICRO_RE.findall(c)}, key=version_key)
    if not micros:
        return None
    newest = micros[-1]
    return newest if version_key(newest) > version_key(current) else None


def fetch_pbs_releases(url: str = PBS_RELEASES_URL, limit: int = 10) -> list[dict]:
    """The newest PBS releases, newest first.

    Uses the public GitHub API without a token; the rate limit is enough for
    a weekly schedule.
    """
    request = urllib.request.Request(  # noqa: S310 - fixed https API endpoint
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pyscriptc-runtime-bump"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, list):
        return []
    return payload[:limit]


def release_micros(release: dict) -> list[str]:
    """The CPython micros a PBS release carries, read from its asset names.

    A PBS release tag is a date ("20261021"); the micro it builds is in the
    asset names ("cpython-3.14.9+20261021-x86_64-unknown-linux-gnu.tar.gz").
    """
    names = [str(asset.get("name", "")) for asset in release.get("assets", [])]
    return [m for name in names for m in MICRO_RE.findall(name)]


def report(lock_path: Path = LOCK) -> dict:
    """The bump report: what is pinned, what is available."""
    if not lock_path.exists():
        return {"status": "no-lock", "pinned": None, "newest": None, "tag": None}
    pinned = pinned_version(lock_path)
    tag = pinned_release(lock_path)
    try:
        releases = fetch_pbs_releases()
    except (OSError, ValueError) as exc:
        return {"status": "unreachable", "pinned": pinned, "newest": None, "tag": tag, "error": str(exc)}
    tags = [str(entry.get("tag_name", "")) for entry in releases]
    candidates = list(tags)
    for entry in releases:
        candidates.extend(release_micros(entry))
    newest = newest_micro(candidates, pinned)
    if newest is None:
        return {"status": "current", "pinned": pinned, "newest": None, "tag": tag}
    release_tag = next(
        (str(entry.get("tag_name", "")) for entry in releases if newest in release_micros(entry)),
        tag,
    )
    return {"status": "bump", "pinned": pinned, "newest": newest, "tag": release_tag}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    data = report()
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        status = data["status"]
        if status == "no-lock":
            print("packaging/runtime.lock.toml is absent: the runtime pack slice has not landed")
        elif status == "unreachable":
            print(f"the python-build-standalone release list is unreachable: {data.get('error')}")
        elif status == "current":
            print(f"CPython {data['pinned']} is current against {data['tag']}")
        else:
            print(f"a newer CPython 3.14 micro is available: {data['pinned']} -> {data['newest']} ({data['tag']})")
    return 1 if data["status"] == "bump" else 0


if __name__ == "__main__":
    sys.exit(main())
