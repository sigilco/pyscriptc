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

# The island is never automatic: only a per-import policy entry can select it,
# and its binary always links the host interpreter. This is the user-facing
# label coverage and binary metadata print for that lane.
ISLAND_LABEL = "requires libpython"

_KEYS: dict[str, tuple[type, ...]] = {
    "entry": (str,),
    "src": (str, list),
    "checker": (str,),
    "driver": (str,),
    "target": (str,),
    "fail-on": (str,),
    "stub-path": (str, list),
    "ffi": (str, list),
    "imports": (dict,),
    # boxed:begin
    "fallback": (dict, str, bool),
    # boxed:end
}
_CHOICES = {
    "checker": ("pyrefly", "mypy", "pyright"),
    "driver": ("clang", "zig"),
    "fail-on": ("blockers", "any"),
}


class ConfigError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__(CONFIG_CODE, message)


def entry_points(pyproject: Path) -> dict[str, str]:
    """`[project.scripts]` of a pyproject.toml: console script name -> module:attr.

    An absent `[project]` or `[project.scripts]` table is an empty dict; a
    malformed table is a PS3102. Values are checked for the `module:attr`
    shape the compiler needs (PEP 621 allows any string, the lowered subset
    does not).
    """

    try:
        data = _toml(pyproject)
    except ConfigError:
        raise
    project = data.get("project")
    if project is None:
        return {}
    if not isinstance(project, dict):
        raise ConfigError(f"{pyproject}: [project] must be a table")
    scripts = project.get("scripts")
    if scripts is None:
        return {}
    if not isinstance(scripts, dict):
        raise ConfigError(f"{pyproject}: [project.scripts] must be a table")
    out: dict[str, str] = {}
    for name, target in scripts.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ConfigError(f"{pyproject}: [project.scripts].{name!r} is not a valid console script name")
        if not isinstance(target, str) or target.count(":") != 1:
            raise ConfigError(
                f"{pyproject}: [project.scripts].{name} must be 'module:attribute' (a dotted module, a function name)"
            )
        module, attr = target.split(":")
        if not module or not all(part.isidentifier() for part in module.split(".")):
            raise ConfigError(f"{pyproject}: [project.scripts].{name}: {module!r} is not a module path")
        if not attr.isidentifier():
            raise ConfigError(f"{pyproject}: [project.scripts].{name}: {attr!r} is not an attribute name")
        out[name] = target
    return out


def project_roots(pyproject: Path) -> tuple[str, ...]:
    """The module roots of a pyproject project: [tool.pyscriptc] src, default 'src'.

    A configured root that exists wins. When none of them exists the project
    root itself is the root, so a flat layout (packages beside the
    pyproject) still resolves.
    """

    base = pyproject.parent
    data = _toml(pyproject)
    table = _project_table(data, pyproject)
    configured = table.get("src") if table else None
    if configured is None:
        configured = "src"
    roots = (configured,) if isinstance(configured, str) else tuple(configured)
    present = tuple(root for root in roots if (base / root).is_dir())
    return present or (".",)


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
        line[2:] if line.startswith("# ") else line[1:] for line in blocks[0].group("content").splitlines(keepends=True)
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
    stub_path: tuple[str, ...] = ()
    ffi: tuple[str, ...] = ()
    imports: dict[str, str] = field(default_factory=dict)
    # boxed:begin
    fallback: Any = field(default_factory=dict)
    # boxed:end
    requires_python: str | None = None
    dependencies: tuple[str, ...] = ()

    # boxed:begin
    def fallback_definitions(self) -> bool:
        """True when [tool.pyscriptc.fallback] enables per-definition island routing."""
        if isinstance(self.fallback, dict):
            return bool(self.fallback.get("definitions", False)) or self.fallback.get("tier") == "cpython"
        if isinstance(self.fallback, str):
            return self.fallback in ("definitions", "cpython")
        if isinstance(self.fallback, bool):
            return self.fallback
        return False

    # boxed:end

    def lane_for(self, name: str) -> tuple[str, str]:
        """(lane, matched key) for an imported top-level name."""
        return lane_for(name, self.imports)

    def _resolve(self, paths: tuple[str, ...]) -> tuple[Path, ...]:
        base = self.origin.parent if self.origin is not None else Path.cwd()
        return tuple((base / p).resolve() for p in paths)

    @property
    def stub_paths(self) -> tuple[Path, ...]:
        """`stub-path` entries resolved against the config file's directory."""
        return self._resolve(self.stub_path)

    @property
    def ffi_manifests(self) -> tuple[Path, ...]:
        """`ffi` manifest paths resolved against the config file's directory."""
        return self._resolve(self.ffi)

    def dependency_lanes(self) -> dict[str, str]:
        """Lane of each PEP 723 dependency, keyed by requirement string."""
        return dependency_lanes(self.dependencies, self.imports)

    def island_imports(self) -> tuple[str, ...]:
        """Import names the policy table routes to the island, sorted."""
        names: set[str] = set()
        for name, lane in self.imports.items():
            if lane == "island" and name != "*":
                names.add(name)
                names.add(name.replace("-", "_"))
        return tuple(sorted(names))

    def wildcard_island(self) -> bool:
        """True when `* = "island"` makes every unlisted import island-bound."""
        return self.imports.get("*") == "island"


def lane_for(name: str, imports: dict[str, str]) -> tuple[str, str]:
    """(lane, matched key) for an imported top-level name.

    An exact module name wins over the `*` default; without either, the
    default lane is `compile`.
    """
    if name in imports:
        return imports[name], name
    norm_under = name.replace("-", "_")
    if norm_under in imports:
        return imports[norm_under], norm_under
    norm_hyphen = name.replace("_", "-")
    if norm_hyphen in imports:
        return imports[norm_hyphen], norm_hyphen
    if "*" in imports:
        return imports["*"], "*"
    return DEFAULT_LANE, "default"


# Policy-table lane to the lane vocabulary of the import classifier. The
# island entry is the only way an import reaches that lane: classification
# never guesses it from the package name or its metadata.
POLICY_TO_LANE = {
    "compile": "native",
    "replace": "pack",
    "extern": "extern",
    "island": "island",
    "refuse": "refused",
}

_REQ_NAME_RE = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def requirement_name(requirement: str) -> str:
    """The distribution name of a PEP 508 requirement string."""
    match = _REQ_NAME_RE.match(requirement)
    if match is None:
        raise ConfigError(f"PEP 723 dependency {requirement!r} is not a valid requirement")
    return match.group(1)


def dependency_lanes(dependencies: tuple[str, ...], imports: dict[str, str] | None = None) -> dict[str, str]:
    """Map each dependency to `native`, `pack`, `extern`, `island` or `refused`.

    The `imports` policy table wins (by distribution name or by the import
    name it most likely provides, `-` read as `_`); a `*` entry applies only
    when it is not the `compile` default. Otherwise the offline import
    classifier decides from the known-native data and installed metadata.
    """
    from pyscriptc.imports import classify

    policy = imports or {}
    star = policy.get("*")
    lanes: dict[str, str] = {}
    for requirement in dependencies:
        dist = requirement_name(requirement)
        module = dist.lower().replace("-", "_").replace(".", "_")
        chosen = next((policy[k] for k in (dist, dist.lower(), module) if k in policy), None)
        if chosen is None and star not in (None, "compile"):
            chosen = star
        if chosen is not None:
            lanes[requirement] = POLICY_TO_LANE[chosen]
            continue
        verdict = classify(module, distribution=dist)
        lane = verdict["lane"]
        lanes[requirement] = "native" if lane == "absent" else lane
    return lanes


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
    for key in ("src", "stub-path", "ffi"):
        if isinstance(data.get(key), list) and not all(isinstance(i, str) for i in data[key]):
            raise ConfigError(f"{_where(table, key)} must be a list of strings")
    if "imports" in data:
        imports = data["imports"]
        for name, lane in imports.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*|\*", name):
                raise ConfigError(f"{_where(table, 'imports')}: invalid module name {name!r}")
            if not isinstance(lane, str) or lane not in LANES:
                raise ConfigError(f"{_where(table, 'imports')}.{name} must be one of {'|'.join(LANES)}, got {lane!r}")
    return data


def _paths(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


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
        stub_path=_paths(table.get("stub-path", ())),
        ffi=_paths(table.get("ffi", ())),
        imports=dict(table.get("imports", {})),
        # boxed:begin
        fallback=table.get("fallback", {}),
        # boxed:end
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
        return requires, ()
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
    meta_project: Config | None = None
    if start.is_file() and start.suffix == ".py":
        meta = _read_script_meta(start)
        if meta:
            meta_source = "pep723"
            meta_table = _project_table(meta, start)
            if meta_table is not None:
                meta_project = _from_table(meta_table, start, "pep723")
    if project is None and not meta:
        return Config(origin=None, source="default")
    requires, deps = _deps_of(meta) if meta else (None, ())
    if project is None and meta_project is not None:
        return Config(
            origin=meta_project.origin,
            source=meta_project.source,
            entry=meta_project.entry,
            src=meta_project.src,
            checker=meta_project.checker,
            driver=meta_project.driver,
            target=meta_project.target,
            fail_on=meta_project.fail_on,
            stub_path=meta_project.stub_path,
            ffi=meta_project.ffi,
            imports=meta_project.imports,
            requires_python=requires,
            dependencies=deps,
        )
    if project is None:
        return Config(origin=start, source=meta_source, requires_python=requires, dependencies=deps)
    if meta:
        merged_imports = dict(meta_project.imports if meta_project else {})
        merged_imports.update(project.imports)
        return Config(
            origin=project.origin,
            source=project.source,
            entry=project.entry or (meta_project.entry if meta_project else None),
            src=project.src or (meta_project.src if meta_project else ()),
            checker=project.checker or (meta_project.checker if meta_project else None),
            driver=project.driver or (meta_project.driver if meta_project else None),
            target=project.target or (meta_project.target if meta_project else None),
            fail_on=project.fail_on or (meta_project.fail_on if meta_project else None),
            stub_path=project.stub_path or (meta_project.stub_path if meta_project else ()),
            ffi=project.ffi or (meta_project.ffi if meta_project else ()),
            imports=merged_imports,
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
