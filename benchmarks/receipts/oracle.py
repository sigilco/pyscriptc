# SPDX-License-Identifier: Apache-2.0
"""The pinned CPython 3.14 oracle the receipt harness compares against.

The receipt is a run-and-match measurement, so it needs a reference: the
same program run under a pinned CPython 3.14, byte for byte. The
resolver is the one `benchmarks/probe_lane/lane.py` already uses, so
the two lanes cannot drift on what "the oracle" means.

The resolver lives here rather than being imported from
`benchmarks/compat2/score.py` for the same reason the lane keeps its
own: that file is a frozen compat2 gate, and it carries 3.14-only
syntax that an older host cannot import, while the receipt harness has
to run on every host the compiler supports.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The pin the repo holds everywhere else: the oracle is CPython 3.14.
ORACLE_PIN = (3, 14)


def oracle_version(executable: str) -> tuple[tuple[int, int], str]:
    """The version of an interpreter, and its full version string."""

    out = subprocess.run(
        [executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.version)"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    lines = out.stdout.strip().splitlines()
    major, minor = (int(part) for part in lines[0].split("."))
    return (major, minor), lines[1]


def resolve_oracle() -> str | None:
    """The pinned CPython 3.14 oracle executable, or None when unavailable.

    The harness never silently compares against the host interpreter: the
    oracle has to be CPython 3.14, the pin the repo holds everywhere
    else. The resolution order is the one the repo already uses
    (`PYSC_ORACLE`, else `PYSC_ORACLE_DIR`, else `uv python find 3.14`).
    """

    candidates: list[str] = []
    explicit = os.environ.get("PYSC_ORACLE")
    if explicit:
        if not os.path.isfile(explicit) or not os.access(explicit, os.X_OK):
            print(
                f"PYSC_ORACLE={explicit!r} is not an executable path; the oracle must be CPython 3.14",
                file=sys.stderr,
            )
            return None
        candidates.append(explicit)
    oracle_dir = os.environ.get("PYSC_ORACLE_DIR")
    if oracle_dir:
        candidates += [str(path) for path in sorted(Path(oracle_dir).glob("cpython-3.14.*/bin/python3"))[-1:]]
    if not candidates:
        try:
            found = subprocess.run(["uv", "python", "find", "3.14"], capture_output=True, text=True, check=True)
            candidates.append(found.stdout.strip())
        except (OSError, subprocess.CalledProcessError):
            pass
    if not candidates and sys.version_info[:2] == ORACLE_PIN:
        candidates.append(sys.executable)
    for candidate in candidates:
        try:
            version, _full = oracle_version(candidate)
        except (OSError, subprocess.SubprocessError):
            continue
        if version == ORACLE_PIN:
            return candidate
    return None
