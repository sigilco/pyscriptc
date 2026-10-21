"""One config surface: PEP 723 inline script metadata and [tool.pyscriptc].

Sources, nearest-directory first:

- ``pyscriptc.toml`` in a directory at or above the source (whole-file
  ``[pyscriptc]`` table, or the root table itself).
- ``pyproject.toml`` with a ``[tool.pyscriptc]`` table at or above the source.
- The script's own PEP 723 block (``# /// script``), read for
  ``requires-python`` and ``dependencies``.

Project config wins over PEP 723 for the keys it sets; PEP 723 is the only
source for script dependency metadata. The per-import policy table maps an
import name to a lane: ``compile`` (default), ``replace``, ``extern``,
``island``, ``refuse``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyscriptc.errors import SpikeError

CONFIG_CODE = "PS3102"

PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")

LANES = ("compile", "replace", "extern", "island", "refuse")
DEFAULT_LANE = "compile"

_KEYS: dict[str, tuple[type, ...]] = {
    "entry": (str,),
    "src": (str, list),
    "checker": (str,),
    "driver": (str,),
    "target": (str,),
    "fail-on": (str,),
    "imports": (dict,),
}
_CHOICES = {
    "checker": ("pyrefly", "mypy", "pyright"),
    "driver": ("clang", "zig"),
    "fail-on": ("blockers", "any"),
}


class ConfigError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__(CONFIG_CODE, message)


def read_pep723(text: str) -> dict[str, Any] | None:
    """The parsed `# /// script` metadata block, or None when absent.

    Shared parser: PR #138 carries an identical copy in deps.py; when that
    branch lands it should import this function instead.
    """
    blocks = [m for m in PEP723_RE.finditer(text) if m.group("type") == "script"]
    if not blocks:
        return None
    if len(blocks) > 1:
        raise ConfigError("more than one `# /// script` block (PEP 723 allows one)")
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0].group("content").splitlines(keepends=True)
    )
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in PEP 723 metadata: {exc}") from exc


@dataclass(frozen=True)
class Config:
    """The resolved config for one compile, with its origin for messages."""

    origin: Path | None
    source: str  # "pyscriptc.toml" | "pyproject.toml" | "pep723" | "default"
    entry: str | None = None
    src: tuple[str, ...] = ()
    checker: str | None = None
    driver: str | None = None
    target: str | None = None
    fail_on: str | None = None
    imports: dict[str, str] = field(default_factory=dict)
    requires_python: str | None = None
    dependencies: tuple[str, ...] = ()

    def lane_for(self, name: str) -> tuple[str, str]:
        """(lane, matched key) for an imported top-level name."""
        return lane_for(name, self.imports)


def lane_for(name: str, imports: dict[str, str]) -> tuple[str, str]:
    """(lane, matched key) for an imported top-level name.

    An exact module name wins over the `*` default; without either, the
    default lane is `compile`.
    """
    if name in imports:
        return imports[name], name
    if "*" in imports:
        return imports["*"], "*"
    return DEFAULT_LANE, "default"


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _where(table: str, key: str) -> str:
    return f"[{table}].{key}" if table else key


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _validate(table: str, data: dict[str, Any]) -> dict[str, Any]:
    for key, value in data.items():
        if key not in _KEYS:
            known = ", ".join(sorted(_KEYS))
            raise ConfigError(f"{_where(table, key)}: unknown key (known: {known})")
        types = _KEYS[key]
        if not isinstance(value, types):
            want = "/".join(t.__name__ for t in types)
            raise ConfigError(f"{_where(table, key)} must be {want}, got {type(value).__name__}")
        if key in _CHOICES and value not in _CHOICES[key]:
            raise ConfigError(f"{_where(table, key)} must be one of {'|'.join(_CHOICES[key])}, got {value!r}")
    if "imports" in data:
        imports = data["imports"]
        for name, lane in imports.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*|\*", name):
                raise ConfigError(f"{_where(table, 'imports')}: invalid module name {name!r}")
            if not isinstance(lane, str) or lane not in LANES:
                raise ConfigError(f"{_where(table, 'imports')}.{name} must be one of {'|'.join(LANES)}, got {lane!r}")
    return data


def _from_table(table: dict[str, Any], origin: Path, source: str) -> Config:
    _validate("tool.pyscriptc" if source == "pyproject.toml" else "pyscriptc", table)
    src = table.get("src", ())
    if isinstance(src, str):
        src = (src,)
    return Config(
        origin=origin,
        source=source,
        entry=table.get("entry"),
        src=tuple(src),
        checker=table.get("checker"),
        driver=table.get("driver"),
        target=table.get("target"),
        fail_on=table.get("fail-on"),
        imports=dict(table.get("imports", {})),
    )


def _project_table(data: dict[str, Any], origin: Path) -> dict[str, Any] | None:
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ConfigError(f"{origin}: [tool] must be a table")
    table = tool.get("pyscriptc")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ConfigError(f"{origin}: [tool.pyscriptc] must be a table")
    return table


def _deps_of(meta: dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    requires = meta.get("requires-python")
    if requires is not None and not isinstance(requires, str):
        raise ConfigError("PEP 723 `requires-python` must be a string")
    deps = meta.get("dependencies")
    if deps is None:
        deps = ()
    if not isinstance(deps, list) or not all(isinstance(item, str) for item in deps):
        raise ConfigError("PEP 723 `dependencies` must be a list of requirement strings")
    return requires, tuple(deps)


def _read_script_meta(script: Path) -> dict[str, Any]:
    try:
        return read_pep723(script.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def load_config(source: Path) -> Config:
    """Resolve config for a source file or directory; never raises when absent."""
    start = source.resolve()
    start_dir = start if start.is_dir() else start.parent
    project: Config | None = None
    for directory in (start_dir, *start_dir.parents):
        candidate = directory / "pyscriptc.toml"
        if candidate.is_file():
            data = _toml(candidate)
            table = data.get("pyscriptc", data)
            if not isinstance(table, dict):
                raise ConfigError(f"{candidate}: [pyscriptc] must be a table")
            project = _from_table(table, candidate, "pyscriptc.toml")
            break
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            table = _project_table(_toml(candidate), candidate)
            if table is not None:
                project = _from_table(table, candidate, "pyproject.toml")
                break
    meta: dict[str, Any] = {}
    meta_source = "default"
    if start.is_file() and start.suffix == ".py":
        meta = _read_script_meta(start)
        if meta:
            meta_source = "pep723"
    if project is None and not meta:
        return Config(origin=None, source="default")
    requires, deps = _deps_of(meta) if meta else (None, ())
    if project is None:
        return Config(origin=start, source=meta_source, requires_python=requires, dependencies=deps)
    if meta:
        return Config(
            origin=project.origin,
            source=project.source,
            entry=project.entry,
            src=project.src,
            checker=project.checker,
            driver=project.driver,
            target=project.target,
            fail_on=project.fail_on,
            imports=project.imports,
            requires_python=requires,
            dependencies=deps,
        )
    return project


def apply_to_args(config: Config, args: Any) -> list[str]:
    """Fill unset CLI flags from config; explicit flags win.

    Returns note lines for the compile log. Mutates `args` in place.
    """
    notes: list[str] = []
    for attr, value in (
        ("checker", config.checker),
        ("driver", config.driver),
        ("target", config.target),
        ("fail_on", config.fail_on),
    ):
        if value is None or not hasattr(args, attr):
            continue
        if getattr(args, attr, None) is None:
            setattr(args, attr, value)
    if config.source != "default":
        notes.append(f"note: config from {config.origin} ({config.source})")
    if config.requires_python is not None:
        notes.append(f"note: requires-python {config.requires_python}")
    return notes
