"""Declared CPython target configuration."""

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
