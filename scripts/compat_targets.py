"""Load and validate `compat/data/targets.json`, the per-target status table.

The file is hand-maintained: what works per target is proven by CI lanes and
smoke scripts, not by a measurement this tree can redo, so it does not belong
in the generated document. The builder reads it, validates it against the
schema and embeds it as the `targets` section, so the site has one file and
the provenance stays explicit.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "compat" / "data" / "targets.json"


def load(path: Path | None = None) -> dict[str, Any]:
    """The targets document. Raises ValueError when missing or malformed."""
    source = path or TARGETS
    if not source.exists():
        raise ValueError(f"targets file not found: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("targets"), list):
        raise ValueError(f"{source} must hold an object with a targets list")
    return document
