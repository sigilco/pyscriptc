"""Island engine descriptors: schema v1, loader and validation.

An island engine descriptor is a small TOML document that describes a
pluggable engine behind the boxed island tier (the built-in engine is the
embedded CPython; issue #668 targets PyPy). `--island-engine
<name-or-path>` selects one; the selection is inert in this slice — it is
resolved, validated and recorded in the compile summary/debug output, and
no lowering or link behavior changes.

Schema v1 (see `docs/reference/island-engine.md`):

    schema_version = 1

    [engine]
    name = "pypy"            # required, non-empty
    version = "7.3.16"       # required, non-empty
    kind = "interpreter"     # required, non-empty free string

    capabilities = ["fallback", "reflection"]   # optional, open string list

    [abi]                    # optional, string -> string surface expectations
    libpython = "3.14"
    stable_abi = "no"

Forward compatibility: unknown top-level keys and unknown `[abi]` entries
are kept as-is (they are reachable on the returned descriptor), so an
older compiler can still load a newer descriptor. Only `schema_version`
is a hard fence: a descriptor written against a major version newer than
this one is rejected with a clear diagnostic, because its meaning may
have changed under the same keys.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
#: The built-in engine: the embedded CPython the island tier already uses.
BUILTIN_NAME = "cpython"
#: Directories `--island-engine <name>` searches, from this env var
#: (`os.pathsep`-separated).
SEARCH_PATH_ENV = "PYSC_ISLAND_ENGINES"


class EngineError(Exception):
    """A user-facing failure of the engine descriptor path (no traceback)."""


@dataclass
class EngineDescriptor:
    """A validated island engine descriptor (schema v1)."""

    name: str
    version: str
    kind: str
    capabilities: list[str] = field(default_factory=list)
    abi: dict[str, str] = field(default_factory=dict)
    #: Unknown top-level tables/keys, kept for forward compatibility.
    extra: dict[str, object] = field(default_factory=dict)
    #: The `manifest` key of `[engine]` (issue #669): a JSON path, relative
    #: to the descriptor file, naming the engine capability manifest. None
    #: when the descriptor does not carry one.
    manifest: str | None = None
    #: Where the descriptor was loaded from (None for the built-in one).
    path: Path | None = None

    def as_summary(self) -> EngineSummary:
        """The debug-summary form of the selection."""
        return EngineSummary(
            name=self.name,
            version=self.version,
            kind=self.kind,
            capabilities=list(self.capabilities),
            abi=dict(self.abi),
            source=str(self.path) if self.path is not None else "builtin",
        )


class EngineSummary:
    """The debug-summary form of an engine selection.

    Typed rather than a plain dict so `debug.py` can use attribute access
    instead of literal IR-key reads (see `tests/test_debug.py`); the JSON
    summary serializes it with `as_dict`. Plain class, not `@dataclass`:
    the self-compile of the entry module crashed `_setattr_target` with a
    KeyError on `Debug` while the dataclass form was in place (class-body
    annotation resolution, issue #732 territory), and the plain class
    self-compiles.
    """

    name: str
    version: str
    kind: str
    capabilities: list[str]
    abi: dict[str, str]
    source: str

    def __init__(
        self,
        name: str,
        version: str,
        kind: str,
        capabilities: list[str],
        abi: dict[str, str],
        source: str,
    ) -> None:
        self.name = name
        self.version = version
        self.kind = kind
        self.capabilities = capabilities
        self.abi = abi
        self.source = source

    def as_dict(self) -> dict[str, Any]:
        """The JSON-summary form (`asdict` equivalent)."""
        return {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "abi": dict(self.abi),
            "source": self.source,
        }


def builtin_descriptor() -> EngineDescriptor:
    """The built-in embedded-CPython engine, always available."""
    return EngineDescriptor(
        name=BUILTIN_NAME,
        version="builtin",
        kind="interpreter",
        capabilities=["fallback"],
        abi={"libpython": "embedded"},
    )


def _require_text(table: str, key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineError(f"engine descriptor: [{table}] {key} must be a non-empty string, got {value!r}")
    return value


def parse_descriptor(data: object, *, source: Path | None = None) -> EngineDescriptor:
    """Validate an already-parsed descriptor document against schema v1."""
    if not isinstance(data, dict):
        raise EngineError("engine descriptor: top level must be a TOML table")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise EngineError(f"engine descriptor: schema_version must be an integer, got {version!r}")
    if version > SCHEMA_VERSION:
        raise EngineError(
            f"engine descriptor: schema_version {version} is newer than the supported "
            f"version {SCHEMA_VERSION}; upgrade pyscriptc to load this descriptor"
        )
    engine = data.get("engine")
    if not isinstance(engine, dict):
        raise EngineError("engine descriptor: missing required [engine] table")
    name = _require_text("engine", "name", engine.get("name"))
    version_text = _require_text("engine", "version", engine.get("version"))
    kind = _require_text("engine", "kind", engine.get("kind"))
    capabilities_raw = data.get("capabilities", [])
    if not isinstance(capabilities_raw, list) or not all(isinstance(item, str) for item in capabilities_raw):
        raise EngineError("engine descriptor: capabilities must be a list of strings")
    abi_raw = data.get("abi", {})
    if not isinstance(abi_raw, dict):
        raise EngineError("engine descriptor: [abi] must be a table")
    abi: dict[str, str] = {}
    for key, value in abi_raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise EngineError(f"engine descriptor: [abi] {key!r} must map a name to a string, got {value!r}")
        abi[key] = value
    known = {"schema_version", "engine", "capabilities", "abi"}
    extra = {key: value for key, value in data.items() if key not in known}
    manifest_raw = engine.get("manifest")
    manifest_ref: str | None = None
    if manifest_raw is not None:
        manifest_ref = _require_text("engine", "manifest", manifest_raw)
    return EngineDescriptor(
        name=name,
        version=version_text,
        kind=kind,
        capabilities=list(capabilities_raw),
        abi=abi,
        extra=extra,
        manifest=manifest_ref,
        path=source,
    )


def parse_descriptor_text(text: str, *, source: Path | None = None) -> EngineDescriptor:
    """Parse and validate descriptor TOML text."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EngineError(f"engine descriptor: invalid TOML: {exc}") from exc
    return parse_descriptor(data, source=source)


def search_dirs() -> list[Path]:
    """The directories `--island-engine <name>` searches, in order."""
    raw = os.environ.get(SEARCH_PATH_ENV, "")
    dirs: list[Path] = []
    for part in raw.split(os.pathsep):
        if part.strip():
            dirs.append(Path(part))
    return dirs


def resolve(name_or_path: str) -> EngineDescriptor:
    """Resolve `--island-engine <name-or-path>` to a validated descriptor.

    A path (anything containing a path separator or ending in `.toml`) is
    loaded directly. A bare name is looked up as `<name>.toml` in the
    `PYSC_ISLAND_ENGINES` search dirs. The built-in `cpython` engine is
    always resolvable without any configuration.
    """
    candidate = Path(name_or_path)
    has_altsep = bool(os.altsep) and os.altsep in name_or_path
    looks_like_path = os.sep in name_or_path or has_altsep or name_or_path.endswith(".toml")
    if looks_like_path:
        if not candidate.is_file():
            raise EngineError(f"--island-engine: no descriptor file at {candidate}")
        return load_file(candidate)
    if name_or_path == BUILTIN_NAME:
        return builtin_descriptor()
    for directory in search_dirs():
        path = directory / f"{name_or_path}.toml"
        if path.is_file():
            return load_file(path)
    searched = ", ".join(str(d) for d in search_dirs()) or "(empty; set PYSC_ISLAND_ENGINES)"
    raise EngineError(
        f"--island-engine: unknown engine {name_or_path!r}; searched {searched}. "
        f"Pass a descriptor path, name the engine {BUILTIN_NAME!r}, "
        f"or point {SEARCH_PATH_ENV} at a directory of descriptors"
    )


def load_file(path: Path) -> EngineDescriptor:
    """Load and validate a descriptor file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineError(f"--island-engine: cannot read {path}: {exc}") from exc
    return parse_descriptor_text(text, source=path)
