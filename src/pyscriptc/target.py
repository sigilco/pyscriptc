"""Declared CPython target configuration.

The tool host may be CPython 3.11 through 3.15 (the floor lives in
`pyproject.toml` and CI proves it), but the emitted program always claims
the target declared here. Everything that reads a version to decide what
the program does reads it from this module, not from the running
interpreter, so a 3.11 host emits what a 3.14 host emits.
"""

from __future__ import annotations

import json
from pathlib import Path

TARGET_FILE = Path(__file__).resolve().parent / "target.json"


def _read_target() -> dict[str, str]:
    return json.loads(TARGET_FILE.read_text(encoding="utf-8"))


_TARGET = _read_target()
TARGET_CPYTHON: str = _TARGET["cpython"]
TARGET_MINOR: str = _TARGET["minor"]
TARGET_VERSION_INFO: tuple[int, int, int] = tuple(int(x) for x in TARGET_CPYTHON.split("."))  # type: ignore[assignment]
TARGET_MINOR_INFO: tuple[int, int] = (TARGET_VERSION_INFO[0], TARGET_VERSION_INFO[1])
