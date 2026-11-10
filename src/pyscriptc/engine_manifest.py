"""Engine capability manifest v1 (issue #669).

A manifest is a machine-readable declaration of what an island engine (or
runtime pack) provides: its identity, the Python language level, which
stdlib modules resolve, which language features compile, and the platform
it runs on. The probe that produces one lives in
`scripts/engine_manifest.py` and runs inside the engine being described;
this module owns the schema, the loader and the consumers.

`pyscriptc imports` and `pyscriptc assess` consume a manifest through the
selected engine (`--island-engine` descriptor, `manifest` key) so they can
report against the engine lane and not only against the native lane. With
no engine selected the consumers behave exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


class ManifestError(Exception):
    """A user-facing failure of the manifest path (no traceback)."""


@dataclass
class EngineManifest:
    """A validated engine capability manifest (schema v1)."""

    manifest_version: int
    name: str
    version: str
    language_level: str
    platform: str
    stdlib_available: list[str]
    stdlib_missing: list[str]
    features: dict[str, bool]
    free_threaded: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        """The JSON-report form."""
        data: dict[str, Any] = {
            "manifestVersion": self.manifest_version,
            "name": self.name,
            "version": self.version,
            "languageLevel": self.language_level,
            "platform": self.platform,
            "stdlib": {"available": list(self.stdlib_available), "missing": list(self.stdlib_missing)},
            "features": dict(self.features),
            "freeThreaded": self.free_threaded,
        }
        if self.extra:
            data["extra"] = self.extra
        return data

    def stdlib_status(self, root: str) -> str:
        """`available`, `missing` or `unknown` for a top-level import name."""
        if root in self.stdlib_missing:
            return "missing"
        if root in self.stdlib_available:
            return "available"
        return "unknown"


def _require_str(table: str, key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"engine manifest: [{table}] {key} must be a non-empty string, got {value!r}")
    return value


def parse_manifest(data: object, *, source: Path | None = None) -> EngineManifest:
    """Validate an already-parsed manifest document against schema v1."""
    if not isinstance(data, dict):
        raise ManifestError("engine manifest: top level must be a JSON object")
    version = data.get("schema")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ManifestError(f"engine manifest: schema must be an integer, got {version!r}")
    if version > MANIFEST_VERSION:
        raise ManifestError(
            f"engine manifest: schema {version} is newer than the supported "
            f"version {MANIFEST_VERSION}; upgrade pyscriptc to read this manifest"
        )
    engine = data.get("engine")
    if not isinstance(engine, dict):
        raise ManifestError("engine manifest: missing required [engine] object")
    name = _require_str("engine", "name", engine.get("name"))
    engine_version = _require_str("engine", "version", engine.get("version"))
    language_level = _require_str("engine", "language_level", engine.get("language_level"))
    platform_text = _require_str("engine", "platform", engine.get("platform"))
    free_threaded = engine.get("free_threaded", False)
    if not isinstance(free_threaded, bool):
        raise ManifestError(f"engine manifest: [engine] free_threaded must be a boolean, got {free_threaded!r}")
    stdlib = data.get("stdlib")
    if not isinstance(stdlib, dict):
        raise ManifestError("engine manifest: missing required [stdlib] object")
    available = stdlib.get("available")
    missing = stdlib.get("missing")
    for key, value in (("available", available), ("missing", missing)):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ManifestError(f"engine manifest: [stdlib] {key} must be a list of strings, got {value!r}")
    overlap = sorted(set(available) & set(missing))
    if overlap:
        raise ManifestError(f"engine manifest: module(s) listed in both stdlib lists: {', '.join(overlap)}")
    features = data.get("features")
    if not isinstance(features, dict) or not all(isinstance(item, bool) for item in features.values()):
        raise ManifestError("engine manifest: [features] must be an object of booleans")
    known = {"schema", "engine", "stdlib", "features"}
    extra = {key: value for key, value in data.items() if key not in known}
    return EngineManifest(
        manifest_version=version,
        name=name,
        version=engine_version,
        language_level=language_level,
        platform=platform_text,
        free_threaded=free_threaded,
        stdlib_available=list(available),
        stdlib_missing=list(missing),
        features=dict(features),
        extra=extra,
        path=source,
    )


def parse_manifest_text(text: str, *, source: Path | None = None) -> EngineManifest:
    """Parse and validate a manifest document."""
    import json

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ManifestError(f"engine manifest: invalid JSON ({exc})") from exc
    return parse_manifest(data, source=source)


def load_manifest_file(path: Path) -> EngineManifest:
    """Load and validate a manifest from a file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"engine manifest: cannot read {path} ({exc})") from exc
    return parse_manifest_text(text, source=path)
