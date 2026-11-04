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
from pathlib import Path
from typing import Any, Dict

from pyscriptc.errors import SpikeError

CONFIG_CODE = "PS3102"

PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")

LANES: list[str] = ["compile", "replace", "extern", "island", "refuse"]
DEFAULT_LANE = "compile"

# The island is never automatic: only a per-import policy entry can select it,
# and its binary always links the host interpreter. This is the user-facing
# label coverage and binary metadata print for that lane.
ISLAND_LABEL = "requires libpython"

# The accepted value type of every config key, by name. The names are the
# runtime type names of the values TOML can hold (`str`, `list`, `dict`,
# `bool`), so the validator can compare a value's type name against them
# without reading a type object at run time.
_KEYS: Dict[str, list[str]] = {
    "entry": ["str"],
    "src": ["str", "list"],
    "checker": ["str"],
    "driver": ["str"],
    "target": ["str"],
    "fail-on": ["str"],
    "stub-path": ["str", "list"],
    "ffi": ["str", "list"],
    "imports": ["dict"],
    # boxed:begin
    "fallback": ["dict", "str", "bool"],
    "island": ["dict"],
    # boxed:end
}
_CHOICES: Dict[str, list[str]] = {
    "checker": ["pyrefly", "mypy", "pyright"],
    "driver": ["clang", "zig"],
    "fail-on": ["blockers", "any"],
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
        data: Dict[str, Any] = _toml(pyproject)
    except ConfigError:
        raise
    if "project" not in data:
        return {}
    project: Dict[str, Any] = data["project"]
    if not isinstance(project, dict):
        raise ConfigError(f"{pyproject}: [project] must be a table")
    if "scripts" not in project:
        return {}
    scripts: Dict[str, str] = project["scripts"]
    if not isinstance(scripts, dict):
        raise ConfigError(f"{pyproject}: [project.scripts] must be a table")
    out: dict[str, str] = {}
    for name, target in scripts.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ConfigError(f"{pyproject}: [project.scripts].{name!r} is not a valid console script name")
        if not isinstance(target, str):
            raise ConfigError(
                f"{pyproject}: [project.scripts].{name} must be 'module:attribute' (a dotted module, a function name)"
            )
        parts: list[str] = target.split(":")
        if len(parts) != 2:
            raise ConfigError(
                f"{pyproject}: [project.scripts].{name} must be 'module:attribute' (a dotted module, a function name)"
            )
        module: str = parts[0]
        attr: str = parts[1]
        if not module or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", module):
            raise ConfigError(f"{pyproject}: [project.scripts].{name}: {module!r} is not a module path")
        if not re.fullmatch(r"[A-Za-z_]\w*", attr):
            raise ConfigError(f"{pyproject}: [project.scripts].{name}: {attr!r} is not an attribute name")
        out[name] = target
    return out


def project_roots(pyproject: Path) -> tuple[str, ...]:
    """The module roots of a pyproject project: [tool.pyscriptc] src, default 'src'.

    A configured root that exists wins. When none of them exists the project
    root itself is the root, so a flat layout (packages beside the
    pyproject) still resolves.
    """

    base: Path = pyproject.parent
    data: Dict[str, Any] = _toml(pyproject)
    table: Dict[str, Any] | None = _project_table(data, pyproject)
    roots: list[str] = []
    if table is not None and "src" in table:
        if isinstance(table["src"], str):
            src_str: str = table["src"]
            roots.append(src_str)
        else:
            src_items: list[str] = table["src"]
            for r in src_items:
                roots.append(r)
    if not roots:
        roots.append("src")
    present: list[str] = []
    for root in roots:
        if (base / root).is_dir():
            present.append(root)
    if not present:
        present.append(".")
    return _as_tuple(present)


def read_pep723(text: str) -> dict[str, Any] | None:
    """The parsed `# /// script` metadata block, or None when absent.

    Shared parser: PR #138 carries an identical copy in deps.py; when that
    branch lands it should import this function instead.
    """
    blocks: list[re.Match] = []
    for m in PEP723_RE.finditer(text):
        if m.group(1) == "script":
            blocks.append(m)
    if not blocks:
        return None
    if len(blocks) > 1:
        raise ConfigError("more than one `# /// script` block (PEP 723 allows one)")
    target_match: re.Match = blocks[0]
    raw = target_match.group(2)
    raw_content: str = "" if raw is None else raw
    lines: list[str] = raw_content.splitlines(True)
    content_parts: list[str] = []
    for line in lines:
        if line.startswith("# "):
            content_parts.append(line[2:])
        else:
            content_parts.append(line[1:])
    content: str = "".join(content_parts)
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in PEP 723 metadata: {exc}") from exc


class Config:
    """The resolved config for one compile, with its origin for messages."""

    origin: Path | None
    source: str  # "pyscriptc.toml" | "pyproject.toml" | "pep723" | "default"
    entry: str | None
    src: tuple[str, ...]
    checker: str | None
    driver: str | None
    target: str | None
    fail_on: str | None
    stub_path: tuple[str, ...]
    ffi: tuple[str, ...]
    imports: Dict[str, str]
    # boxed:begin
    fallback: Dict[str, Any]
    island: Dict[str, Any]
    # boxed:end
    requires_python: str | None
    dependencies: tuple[str, ...]

    def __init__(
        self,
        origin: Path | None = None,
        source: str = "default",
        entry: str | None = None,
        src: tuple[str, ...] | None = None,
        checker: str | None = None,
        driver: str | None = None,
        target: str | None = None,
        fail_on: str | None = None,
        stub_path: tuple[str, ...] | None = None,
        ffi: tuple[str, ...] | None = None,
        imports: Dict[str, str] | None = None,
        # boxed:begin
        fallback: Dict[str, Any] | None = None,
        island: Dict[str, Any] | None = None,
        # boxed:end
        requires_python: str | None = None,
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        empty_src: tuple[str, ...] = ()
        self.origin = origin
        self.source = source
        self.entry = entry
        self.src = empty_src if src is None else src
        self.checker = checker
        self.driver = driver
        self.target = target
        self.fail_on = fail_on
        self.stub_path = empty_src if stub_path is None else stub_path
        self.ffi = empty_src if ffi is None else ffi
        empty_imports: Dict[str, str] = {}
        self.imports = empty_imports if imports is None else imports
        # boxed:begin
        empty_fallback: Dict[str, Any] = {}
        self.fallback = empty_fallback if fallback is None else fallback
        empty_island: Dict[str, Any] = {}
        self.island = empty_island if island is None else island
        # boxed:end
        self.requires_python = requires_python
        self.dependencies = empty_src if dependencies is None else dependencies

    # boxed:begin
    def fallback_definitions(self) -> bool:
        """True when [tool.pyscriptc.fallback] enables per-definition island routing."""
        if isinstance(self.fallback, dict):
            enabled = False
            if "definitions" in self.fallback:
                definitions: bool = self.fallback["definitions"]
                if definitions:
                    enabled = True
            if "tier" in self.fallback:
                tier: str = self.fallback["tier"]
                if tier == "cpython":
                    enabled = True
            return enabled
        if isinstance(self.fallback, str):
            return self.fallback == "definitions" or self.fallback == "cpython"
        if isinstance(self.fallback, bool):
            return self.fallback
        return False

    def island_embed(self) -> str | None:
        """The `[tool.pyscriptc.island] embed` mode, or None when unset."""

        if isinstance(self.island, dict):
            if "embed" in self.island:
                value: str = self.island["embed"]
                from pyscriptc.embed import MODES

                table = "pyscriptc.island" if self.source == "pyscriptc.toml" else "tool.pyscriptc.island"
                if value not in MODES:
                    modes_str: str = "|".join(MODES)
                    raise ConfigError(f"{table}.embed must be one of {modes_str}, got {value!r}")
                return str(value)
        return None

    def island_stdlib(self) -> str | None:
        """The `[tool.pyscriptc.island] stdlib` trim, or None when unset."""

        if isinstance(self.island, dict):
            if "stdlib" in self.island:
                value: str = self.island["stdlib"]
                from pyscriptc.stdlibzip import STDLIB_MODES

                table = "pyscriptc.island" if self.source == "pyscriptc.toml" else "tool.pyscriptc.island"
                if value not in STDLIB_MODES:
                    stdlib_modes_str: str = "|".join(STDLIB_MODES)
                    raise ConfigError(f"{table}.stdlib must be one of {stdlib_modes_str}, got {value!r}")
                return str(value)
        return None

    # boxed:end

    def lane_for(self, name: str) -> tuple[str, str]:
        """(lane, matched key) for an imported top-level name."""
        return lane_for(name, self.imports)

    def _resolve(self, paths: tuple[str, ...]) -> tuple[Path, ...]:
        base: Path = self.origin.parent if self.origin is not None else Path.cwd()
        out: list[Path] = []
        for p in paths:
            out.append((base / p).resolve())
        return tuple(out)

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
        out: list[str] = sorted(names)
        return tuple(out)

    def wildcard_island(self) -> bool:
        """True when `* = "island"` makes every unlisted import island-bound."""
        return self.imports.get("*") == "island"


def lane_for(name: str, imports: Dict[str, str]) -> tuple[str, str]:
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
POLICY_TO_LANE: Dict[str, str] = {
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
    name: str | None = match.group(1)
    if name is None:
        raise ConfigError(f"PEP 723 dependency {requirement!r} is not a valid requirement")
    return name


def dependency_lanes(dependencies: tuple[str, ...], imports: dict[str, str] | None = None) -> dict[str, str]:
    """Map each dependency to `native`, `pack`, `extern`, `island` or `refused`.

    The `imports` policy table wins (by distribution name or by the import
    name it most likely provides, `-` read as `_`); a `*` entry applies only
    when it is not the `compile` default. Otherwise the offline import
    classifier decides from the known-native data and installed metadata.
    """
    from pyscriptc.imports import classify

    if imports is not None:
        policy: Dict[str, str] = imports
    else:
        empty_policy: Dict[str, str] = {}
        policy = empty_policy
    star: str | None = policy.get("*")
    lanes: dict[str, str] = {}
    for requirement in dependencies:
        dist: str = requirement_name(requirement)
        module: str = dist.lower().replace("-", "_").replace(".", "_")
        chosen: str | None = None
        for k in (dist, dist.lower(), module):
            if k in policy:
                chosen = policy[k]
                break
        if chosen is None and star is not None and star != "compile":
            chosen = star
        if chosen is not None:
            lanes[requirement] = POLICY_TO_LANE[chosen]
            continue
        verdict = classify(module, distribution=dist)
        lane: str = verdict.lane
        lanes[requirement] = "native" if lane == "absent" else lane
    return lanes


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _where(table: str, key: str) -> str:
    return f"[{table}].{key}" if table else key


def _as_tuple(items: list[str]) -> tuple[str, ...]:
    """The runtime tuple of a name list. The IR models it as a list."""
    return tuple(items)


def _paths(table: Dict[str, Any], key: str) -> tuple[str, ...]:
    """The `stub-path` or `ffi` entries of a config table, as a tuple."""
    out: list[str] = []
    if key in table:
        probe: str = table[key]
        if isinstance(probe, str):
            one: str = table[key]
            out.append(one)
        else:
            entries: list[str] = table[key]
            for item in entries:
                out.append(item)
    return _as_tuple(out)


def _validate(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in data.items():
        if key not in _KEYS:
            key_names: list[str] = []
            for known in _KEYS:
                key_names.append(known)
            joined = ", ".join(sorted(key_names))
            raise ConfigError(f"{_where(table, key)}: unknown key (known: {joined})")
        names = _KEYS[key]
        actual: str = type(value).__name__
        if actual not in names:
            want = "/".join(names)
            raise ConfigError(f"{_where(table, key)} must be {want}, got {actual}")
        if key in _CHOICES and actual == "str":
            choice_str: str = data[key]
            if choice_str not in _CHOICES[key]:
                choice_names: list[str] = []
                for choice in _CHOICES[key]:
                    choice_names.append(choice)
                choice_want = "|".join(choice_names)
                raise ConfigError(f"{_where(table, key)} must be one of {choice_want}, got {choice_str!r}")
    for key in ("src", "stub-path", "ffi"):
        if key in data:
            val_list: list[str] = data[key]
            for item in val_list:
                if not isinstance(item, str):
                    raise ConfigError(f"{_where(table, key)} must be a list of strings")
    if "imports" in data:
        imports: Dict[str, Any] = data["imports"]
        for name, lane in imports.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*|\*", name):
                raise ConfigError(f"{_where(table, 'imports')}: invalid module name {name!r}")
            lane_str: str = lane
            if not isinstance(lane, str) or lane_str not in LANES:
                lane_want = "|".join(LANES)
                raise ConfigError(f"{_where(table, 'imports')}.{name} must be one of {lane_want}, got {lane_str!r}")
    return data


def _src_list(table: Dict[str, Any]) -> list[str]:
    """The `src` entries of a config table: one string or a list of strings."""
    out: list[str] = []
    if "src" in table:
        probe: str = table["src"]
        if isinstance(probe, str):
            one: str = table["src"]
            out.append(one)
        else:
            src: list[str] = table["src"]
            for item in src:
                out.append(item)
    return out


def _from_table(table: dict[str, Any], origin: Path, source: str) -> Config:
    _validate("tool.pyscriptc" if source == "pyproject.toml" else "pyscriptc", table)
    entry: str | None = None
    if "entry" in table:
        entry = table["entry"]
    checker: str | None = None
    if "checker" in table:
        checker = table["checker"]
    driver: str | None = None
    if "driver" in table:
        driver = table["driver"]
    target: str | None = None
    if "target" in table:
        target = table["target"]
    fail_on: str | None = None
    if "fail-on" in table:
        fail_on = table["fail-on"]
    fallback: Dict[str, Any] = {}
    if "fallback" in table:
        fallback = table["fallback"]
    island: Dict[str, Any] = {}
    if "island" in table:
        island = table["island"]
    imports: Dict[str, str] = {}
    if "imports" in table:
        imports = table["imports"]
    return Config(
        origin=origin,
        source=source,
        entry=entry,
        src=_as_tuple(_src_list(table)),
        checker=checker,
        driver=driver,
        target=target,
        fail_on=fail_on,
        stub_path=_paths(table, "stub-path"),
        ffi=_paths(table, "ffi"),
        imports=imports,
        # boxed:begin
        fallback=fallback,
        island=island,
        # boxed:end
    )


def _project_table(data: Dict[str, Any], origin: Path) -> Dict[str, Any] | None:
    if "tool" not in data:
        return None
    tool: Dict[str, Any] = data["tool"]
    if not isinstance(tool, dict):
        raise ConfigError(f"{origin}: [tool] must be a table")
    if "pyscriptc" not in tool:
        return None
    table: Dict[str, Any] = tool["pyscriptc"]
    if not isinstance(table, dict):
        raise ConfigError(f"{origin}: [tool.pyscriptc] must be a table")
    return table


def _deps_of(meta: Dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    requires: str | None = None
    if "requires-python" in meta:
        req_val: str = meta["requires-python"]
        if not isinstance(req_val, str):
            raise ConfigError("PEP 723 `requires-python` must be a string")
        requires = req_val
    if "dependencies" not in meta:
        empty_deps: list[str] = []
        return requires, tuple(empty_deps)
    deps_val: list[str] = meta["dependencies"]
    if not isinstance(deps_val, list):
        raise ConfigError("PEP 723 `dependencies` must be a list of requirement strings")
    deps_list: list[str] = []
    for item in deps_val:
        if not isinstance(item, str):
            raise ConfigError("PEP 723 `dependencies` must be a list of requirement strings")
        deps_list.append(item)
    return requires, tuple(deps_list)


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
    # boxed:begin
    if hasattr(args, "embed_python") and getattr(args, "embed_python", None) is None:
        island_embed = config.island_embed()
        if island_embed is not None:
            args.embed_python = island_embed
    if hasattr(args, "embed_stdlib") and getattr(args, "embed_stdlib", None) is None:
        island_stdlib = config.island_stdlib()
        if island_stdlib is not None:
            args.embed_stdlib = island_stdlib
    # boxed:end
    return notes
