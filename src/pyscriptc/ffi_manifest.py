"""Typed extern manifest: parser and validator for the C library lane.

A manifest is a hand-written TOML file that binds a Python-visible module
surface to C ABI symbols. The schema and the design are `docs/ffi-manifest.md`.

This module is the F0 gate of the FFI epic: it parses a manifest, checks
every pin and every reference between sections, and reports failures with
stable diagnostic codes. It does not lower anything and it does not emit
code. The lowering and the linker consume the dataclasses below once the V2
track lands.

Layout of a manifest:

    [manifest]
    module = "pyscriptc_sqlite"
    ps_abi = 1
    format = 1

    [[source]]
    path = "vendor/sqlite3.c"

    [[handle]]
    name = "Connection"
    c_type = "sqlite3 *"
    frees = "sqlite3_close_v2"

    [[const]]
    name = "OK"
    type = "int"
    value = 0

    [[fn]]
    name = "connect"
    py = "(path: str) -> Connection"
    c_symbol = "sqlite3_open_v2"
    abi = "ps_ffi_sqlite_connect"

The parser is deliberately strict. A manifest is a contract reviewed like a
C header, so an unknown key, a missing pin, a dangling handle reference or
an unsupported type is an error with a code, never a warning. The compiler
refuses a manifest before lowering starts, the same fail-closed posture the
`ps_abi` pin requires (`docs/abi-strategy.md`).

Diagnostic codes, all in the `PS4xxx` block that belongs to the FFI lane:

| code | meaning |
|---|---|
| `PS4001` | the manifest file could not be read or is not valid TOML |
| `PS4002` | the `[manifest]` header is missing or malformed |
| `PS4003` | the `format` pin is unknown or unsupported |
| `PS4004` | the `ps_abi` pin is missing or does not match the compiler |
| `PS4005` | a manifest key is unknown, malformed or has the wrong type |
| `PS4006` | a signature or type in the manifest is outside the lowered vocabulary |
| `PS4007` | a name is declared twice, or two manifests claim the same module |
| `PS4008` | a reference names a symbol, handle or constant that does not exist |
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyscriptc.errors import SpikeError

# The manifest schema version this compiler understands. A manifest with a
# different `format` is refused, never guessed at: the schema is append-only
# inside an epoch (`docs/ffi-manifest.md` section 6).
SUPPORTED_FORMATS: frozenset[int] = frozenset({1})

# The `PS_ABI` epoch this compiler stamps. A manifest pins the epoch it was
# written against and the compiler refuses a mismatch before lowering
# (`docs/abi-strategy.md`, "Single PS_ABI integer").
CURRENT_PS_ABI: int = 1

# The scalar types that map onto a machine value with no runtime object.
SCALAR_TYPES: frozenset[str] = frozenset({"int", "float", "bool", "str", "bytes"})

# Container types the subset lowers. `list` and `dict` cross the boundary as
# a `ptr` to the runtime layout, so a manifest may name them only when the
# adapter's thunk builds the value itself.
CONTAINER_TYPES: frozenset[str] = frozenset({"list", "dict"})

# The type names a manifest may use in a `py` signature. Everything else is
# refused: the vocabulary is the lowered subset's, so the emitter never has
# to inspect a value at runtime.
ALLOWED_TYPE_NAMES: frozenset[str] = SCALAR_TYPES | CONTAINER_TYPES

# The C types a `[[const]]` may hold.
CONST_TYPES: frozenset[str] = frozenset({"int", "float", "str"})

# The keys each section accepts. Anything else is `PS4005`.
_MANIFEST_KEYS = frozenset({"module", "ps_abi", "format"})
_SOURCE_KEYS = frozenset({"path", "objects"})
_HANDLE_KEYS = frozenset({"name", "c_type", "frees"})
_CONST_KEYS = frozenset({"name", "type", "value"})
_FN_KEYS = frozenset({"name", "py", "c_symbol", "abi", "borrows", "consumes"})
# A Python identifier, the only name shape the manifest accepts.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A dotted module name, `pyscriptc_sqlite` or `pkg.mod`.
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

# A C identifier, the shape of a symbol or a thunk name.
_C_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A C type spelling for a handle, `sqlite3 *` or `struct foo *`.
_C_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*\*$|^[A-Za-z_][A-Za-z0-9_]*$")

# A `py` signature: `(a: int, b: str) -> int`. Optional `->` return is
# allowed and means `None`.
_SIGNATURE = re.compile(r"^\((?P<params>[^()]*)\)\s*(?:->\s*(?P<ret>[^()]+))?$")

# A parameter inside a signature: `name: type`, with an optional default.
_PARAM = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<type>[^=,]+)(?:\s*=\s*(?P<default>.+))?$")


class FfiManifestError(SpikeError):
    """A manifest that does not validate. `code` is stable and testable."""

    def __init__(self, code: str, message: str, *, path: Path | None = None, where: str = "") -> None:
        location = ""
        if path is not None:
            location = f"{path}"
            if where:
                location = f"{location}: {where}"
        elif where:
            location = where
        super().__init__(code, f"{location}: {message}" if location else message)
        self.path = path
        self.where = where


@dataclass(frozen=True)
class ManifestSource:
    """A C unit or archive that the link line gains."""

    path: str = ""
    objects: tuple[str, ...] = ()

    def resolved(self, base: Path) -> list[Path]:
        """The paths of this source, resolved relative to the manifest file."""
        if self.path:
            return [base / self.path]
        return [base / obj for obj in self.objects]


@dataclass(frozen=True)
class ManifestHandle:
    """An opaque pointer that crosses the boundary as a token."""

    name: str
    c_type: str
    frees: str = ""


@dataclass(frozen=True)
class ManifestConst:
    """A compile-time constant exposed on the module."""

    name: str
    type: str
    value: int | float | str


@dataclass(frozen=True)
class ManifestParam:
    """One parameter of a `py` signature."""

    name: str
    type: str
    default: str | None = None

    @property
    def required(self) -> bool:
        return self.default is None


@dataclass(frozen=True)
class HandleRef:
    """One ownership annotation: a parameter that carries a handle.

    `param` is the argument name in the `py` signature, `handle` is the
    handle type it carries. Ownership is per-argument, not per-call, so two
    parameters of the same handle type are distinct references.
    """

    param: str
    handle: str


@dataclass(frozen=True)
class ManifestFunction:
    """A Python-visible function bound to a C ABI thunk."""

    name: str
    py: str
    c_symbol: str
    abi: str
    params: tuple[ManifestParam, ...] = ()
    returns: str = "None"
    borrows: tuple[HandleRef, ...] = ()
    consumes: tuple[HandleRef, ...] = ()

    @property
    def arity(self) -> int:
        return len(self.params)

    @property
    def required_arity(self) -> int:
        return sum(1 for param in self.params if param.required)


@dataclass(frozen=True)
class FfiManifest:
    """A validated manifest: the module surface plus the link inputs."""

    module: str
    ps_abi: int
    format: int
    path: Path
    sources: tuple[ManifestSource, ...] = ()
    handles: tuple[ManifestHandle, ...] = ()
    consts: tuple[ManifestConst, ...] = ()
    functions: tuple[ManifestFunction, ...] = ()
    handle_names: frozenset[str] = field(default_factory=frozenset)

    def function(self, name: str) -> ManifestFunction | None:
        """The function bound to a Python-visible name, if any."""
        for fn in self.functions:
            if fn.name == name:
                return fn
        return None

    def handle(self, name: str) -> ManifestHandle | None:
        """The handle bound to a Python-visible name, if any."""
        for handle in self.handles:
            if handle.name == name:
                return handle
        return None

    def const(self, name: str) -> ManifestConst | None:
        """The constant bound to a Python-visible name, if any."""
        for const in self.consts:
            if const.name == name:
                return const
        return None

    def unit_paths(self) -> list[Path]:
        """Every C unit and archive the manifest adds to the link line."""
        base = self.path.parent
        units: list[Path] = []
        for source in self.sources:
            units.extend(source.resolved(base))
        return units


def parse_manifest_text(text: str, *, path: Path | None = None, ps_abi: int = CURRENT_PS_ABI) -> FfiManifest:
    """Parse and validate a manifest from TOML text.

    `ps_abi` is the epoch the running compiler stamps; a manifest pinning a
    different epoch is refused.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FfiManifestError("PS4001", f"the manifest is not valid TOML: {exc}", path=path) from exc
    return _build(data, path=path, ps_abi=ps_abi)


def load_manifest(path: Path | str, *, ps_abi: int = CURRENT_PS_ABI) -> FfiManifest:
    """Read, parse and validate a manifest file."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FfiManifestError(
            "PS4001", f"the manifest could not be read: {exc.strerror or exc}", path=file_path
        ) from exc
    except UnicodeDecodeError as exc:
        raise FfiManifestError("PS4001", "the manifest is not valid UTF-8 text", path=file_path) from exc
    return parse_manifest_text(text, path=file_path, ps_abi=ps_abi)


def load_manifests(paths: list[Path | str], *, ps_abi: int = CURRENT_PS_ABI) -> list[FfiManifest]:
    """Load several manifests and refuse two that claim the same module."""
    manifests = [load_manifest(path, ps_abi=ps_abi) for path in paths]
    claimed: dict[str, Path] = {}
    for manifest in manifests:
        owner = claimed.get(manifest.module)
        if owner is not None:
            raise FfiManifestError(
                "PS4007",
                f"the module {manifest.module} is already claimed by {owner}",
                path=manifest.path,
            )
        claimed[manifest.module] = manifest.path
    return manifests


def _build(data: dict[str, Any], *, path: Path | None, ps_abi: int) -> FfiManifest:
    where = "[manifest]"
    header = data.get("manifest")
    if header is None:
        raise FfiManifestError("PS4002", "the manifest has no [manifest] section", path=path)
    if not isinstance(header, dict):
        raise FfiManifestError("PS4002", "[manifest] is not a table", path=path, where=where)

    module = _require_str(header, "module", path=path, where=where)
    if not _MODULE.match(module):
        raise FfiManifestError(
            "PS4002", f"the module name {module!r} is not a dotted identifier", path=path, where=where
        )

    fmt = _require_pin_int(header, "format", code="PS4003", path=path, where=where)
    if fmt not in SUPPORTED_FORMATS:
        raise FfiManifestError(
            "PS4003",
            f"the manifest format {fmt} is not supported; this compiler reads format {sorted(SUPPORTED_FORMATS)}",
            path=path,
            where=where,
        )

    pinned = _require_pin_int(header, "ps_abi", code="PS4004", path=path, where=where)
    if pinned != ps_abi:
        raise FfiManifestError(
            "PS4004",
            f"the manifest pins ps_abi {pinned} but this compiler targets ps_abi {ps_abi}",
            path=path,
            where=where,
        )

    _reject_unknown_keys(header, _MANIFEST_KEYS, path=path, where=where)

    sources = _build_sources(data, path=path)
    handles = _build_handles(data, path=path)
    consts = _build_consts(data, path=path)
    handle_names = {handle.name for handle in handles}
    functions = _build_functions(
        data,
        path=path,
        handle_names=handle_names,
        handle_frees={handle.name: handle.frees for handle in handles},
    )
    _reject_name_collisions(handles, consts, functions, path=path)

    return FfiManifest(
        module=module,
        ps_abi=pinned,
        format=fmt,
        path=path if path is not None else Path("<memory>"),
        sources=sources,
        handles=handles,
        consts=consts,
        functions=functions,
        handle_names=frozenset(handle.name for handle in handles),
    )


def _build_sources(data: dict[str, Any], *, path: Path | None) -> tuple[ManifestSource, ...]:
    entries = data.get("source", [])
    if not isinstance(entries, list):
        raise FfiManifestError("PS4005", "[[source]] is not an array of tables", path=path, where="[[source]]")
    sources: list[ManifestSource] = []
    for index, entry in enumerate(entries):
        where = f"[[source]] #{index + 1}"
        if not isinstance(entry, dict):
            raise FfiManifestError("PS4005", "a source entry is not a table", path=path, where=where)
        _reject_unknown_keys(entry, _SOURCE_KEYS, path=path, where=where)
        source_path = _optional_str(entry, "path", path=path, where=where)
        objects = _optional_str_list(entry, "objects", path=path, where=where)
        if not source_path and not objects:
            raise FfiManifestError("PS4005", "a source entry has neither path nor objects", path=path, where=where)
        if source_path and objects:
            raise FfiManifestError("PS4005", "a source entry has both path and objects", path=path, where=where)
        if source_path and not _is_relative_path(source_path):
            raise FfiManifestError(
                "PS4005",
                f"the source path {source_path!r} must be relative to the manifest file",
                path=path,
                where=where,
            )
        for obj in objects:
            if not _is_relative_path(obj):
                raise FfiManifestError(
                    "PS4005",
                    f"the object path {obj!r} must be relative to the manifest file",
                    path=path,
                    where=where,
                )
        sources.append(ManifestSource(path=source_path or "", objects=objects))
    return tuple(sources)


def _build_handles(data: dict[str, Any], *, path: Path | None) -> tuple[ManifestHandle, ...]:
    entries = data.get("handle", [])
    if not isinstance(entries, list):
        raise FfiManifestError("PS4005", "[[handle]] is not an array of tables", path=path, where="[[handle]]")
    handles: list[ManifestHandle] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"[[handle]] #{index + 1}"
        if not isinstance(entry, dict):
            raise FfiManifestError("PS4005", "a handle entry is not a table", path=path, where=where)
        _reject_unknown_keys(entry, _HANDLE_KEYS, path=path, where=where)
        name = _require_str(entry, "name", path=path, where=where)
        if not _IDENT.match(name):
            raise FfiManifestError("PS4005", f"the handle name {name!r} is not an identifier", path=path, where=where)
        if name in seen:
            raise FfiManifestError("PS4007", f"the handle {name} is declared twice", path=path, where=where)
        seen.add(name)
        c_type = _require_str(entry, "c_type", path=path, where=where)
        if not _C_TYPE.match(c_type):
            raise FfiManifestError(
                "PS4005",
                f"the c_type {c_type!r} is not a C type spelling",
                path=path,
                where=where,
            )
        frees = _optional_str(entry, "frees", path=path, where=where)
        if frees and not _C_SYMBOL.match(frees):
            raise FfiManifestError(
                "PS4005",
                f"the frees symbol {frees!r} is not a C identifier",
                path=path,
                where=where,
            )
        handles.append(ManifestHandle(name=name, c_type=c_type, frees=frees))
    return tuple(handles)


def _build_consts(data: dict[str, Any], *, path: Path | None) -> tuple[ManifestConst, ...]:
    entries = data.get("const", [])
    if not isinstance(entries, list):
        raise FfiManifestError("PS4005", "[[const]] is not an array of tables", path=path, where="[[const]]")
    consts: list[ManifestConst] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"[[const]] #{index + 1}"
        if not isinstance(entry, dict):
            raise FfiManifestError("PS4005", "a const entry is not a table", path=path, where=where)
        _reject_unknown_keys(entry, _CONST_KEYS, path=path, where=where)
        name = _require_str(entry, "name", path=path, where=where)
        if not _IDENT.match(name):
            raise FfiManifestError("PS4005", f"the const name {name!r} is not an identifier", path=path, where=where)
        if name in seen:
            raise FfiManifestError("PS4007", f"the constant {name} is declared twice", path=path, where=where)
        seen.add(name)
        const_type = _require_str(entry, "type", path=path, where=where)
        if const_type not in CONST_TYPES:
            raise FfiManifestError(
                "PS4005",
                f"the const type {const_type!r} is not one of {sorted(CONST_TYPES)}",
                path=path,
                where=where,
            )
        if "value" not in entry:
            raise FfiManifestError("PS4005", "a const entry has no value", path=path, where=where)
        value = entry["value"]
        if const_type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise FfiManifestError("PS4005", "an int const needs an integer value", path=path, where=where)
        elif const_type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FfiManifestError("PS4005", "a float const needs a numeric value", path=path, where=where)
            value = float(value)
        else:
            if not isinstance(value, str):
                raise FfiManifestError("PS4005", "a str const needs a string value", path=path, where=where)
        consts.append(ManifestConst(name=name, type=const_type, value=value))
    return tuple(consts)


def _build_functions(
    data: dict[str, Any],
    *,
    path: Path | None,
    handle_names: set[str],
    handle_frees: dict[str, str],
) -> tuple[ManifestFunction, ...]:
    entries = data.get("fn", [])
    if not isinstance(entries, list):
        raise FfiManifestError("PS4005", "[[fn]] is not an array of tables", path=path, where="[[fn]]")
    functions: list[ManifestFunction] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"[[fn]] #{index + 1}"
        if not isinstance(entry, dict):
            raise FfiManifestError("PS4005", "a fn entry is not a table", path=path, where=where)
        _reject_unknown_keys(entry, _FN_KEYS, path=path, where=where)
        name = _require_str(entry, "name", path=path, where=where)
        if not _IDENT.match(name):
            raise FfiManifestError("PS4005", f"the function name {name!r} is not an identifier", path=path, where=where)
        if name in seen:
            raise FfiManifestError("PS4007", f"the function {name} is declared twice", path=path, where=where)
        seen.add(name)
        py = _require_str(entry, "py", path=path, where=where)
        # `c_symbol` is review documentation of what the thunk targets; the
        # docs pilot spells composite bodies like "sqlite3_exec COMMIT", so it
        # stays free-form. `abi` is the binding symbol and must be exact.
        c_symbol = _require_str(entry, "c_symbol", path=path, where=where)
        abi = _require_str(entry, "abi", path=path, where=where)
        if not _C_SYMBOL.match(abi):
            raise FfiManifestError(
                "PS4005",
                f"the abi thunk {abi!r} is not a C identifier",
                path=path,
                where=where,
            )
        params, returns = _parse_signature(py, path=path, where=where, handle_names=handle_names)
        borrows = _optional_str_list(entry, "borrows", path=path, where=where)
        consumes = _optional_str_list(entry, "consumes", path=path, where=where)
        resolved_borrows = _resolve_handle_refs(borrows, params, handle_names, path=path, where=where, key="borrows")
        resolved_consumes = _resolve_handle_refs(consumes, params, handle_names, path=path, where=where, key="consumes")
        borrowed_params = {ref.param for ref in resolved_borrows}
        consumed_params = {ref.param for ref in resolved_consumes}
        overlap = sorted(borrowed_params & consumed_params)
        if overlap:
            raise FfiManifestError(
                "PS4008",
                f"the argument {overlap[0]} is both borrowed and consumed",
                path=path,
                where=where,
            )
        for owner in resolved_consumes:
            if not handle_frees.get(owner.handle):
                raise FfiManifestError(
                    "PS4008",
                    f"the handle {owner.handle} is consumed but declares no frees symbol",
                    path=path,
                    where=where,
                )
        functions.append(
            ManifestFunction(
                name=name,
                py=py,
                c_symbol=c_symbol,
                abi=abi,
                params=params,
                returns=returns,
                borrows=resolved_borrows,
                consumes=resolved_consumes,
            )
        )
    return tuple(functions)


def _resolve_handle_refs(
    refs: tuple[str, ...],
    params: tuple[ManifestParam, ...],
    handle_names: set[str],
    *,
    path: Path | None,
    where: str,
    key: str,
) -> tuple[HandleRef, ...]:
    """Map ownership annotations onto argument-level handle references.

    An annotation names either the parameter that carries the handle (`db`)
    or the handle type itself (`Connection`). The parameter form is the
    common one, and it is the one that distinguishes two arguments of the
    same handle type.
    """
    resolved: list[HandleRef] = []
    by_name = {param.name: param for param in params}
    for ref in refs:
        param = by_name.get(ref)
        if param is not None:
            if param.type not in handle_names:
                raise FfiManifestError(
                    "PS4008",
                    f"the {key} entry {ref!r} names the parameter {ref}, which is not a handle",
                    path=path,
                    where=where,
                )
            resolved.append(HandleRef(param=ref, handle=param.type))
            continue
        if ref not in handle_names:
            raise FfiManifestError(
                "PS4008",
                f"the {key} entry {ref!r} is neither a declared handle nor a parameter of this function",
                path=path,
                where=where,
            )
        carriers = [param.name for param in params if param.type == ref]
        if not carriers:
            raise FfiManifestError(
                "PS4008",
                f"the {key} entry {ref!r} names a handle that no parameter carries",
                path=path,
                where=where,
            )
        resolved.append(HandleRef(param=carriers[0], handle=ref))
    return tuple(resolved)


def _parse_signature(
    py: str,
    *,
    path: Path | None,
    where: str,
    handle_names: set[str],
) -> tuple[tuple[ManifestParam, ...], str]:
    """Parse a `.pyi`-shaped signature into parameters and a return type."""
    match = _SIGNATURE.match(py.strip())
    if match is None:
        raise FfiManifestError(
            "PS4006",
            f"the signature {py!r} is not of the form (name: type, ...) -> type",
            path=path,
            where=where,
        )
    raw_params = match.group("params").strip()
    params: list[ManifestParam] = []
    names: set[str] = set()
    if raw_params:
        for piece in _split_params(raw_params):
            param_match = _PARAM.match(piece.strip())
            if param_match is None:
                raise FfiManifestError(
                    "PS4006",
                    f"the parameter {piece.strip()!r} is not of the form name: type",
                    path=path,
                    where=where,
                )
            name = param_match.group("name")
            if name in names:
                raise FfiManifestError(
                    "PS4007",
                    f"the parameter {name} is declared twice",
                    path=path,
                    where=where,
                )
            names.add(name)
            type_name = param_match.group("type").strip()
            _check_type(type_name, path=path, where=where, handle_names=handle_names)
            default = param_match.group("default")
            params.append(ManifestParam(name=name, type=type_name, default=default.strip() if default else None))
    returns = (match.group("ret") or "None").strip()
    _check_type(returns, path=path, where=where, handle_names=handle_names, allow_none=True)
    return tuple(params), returns


def _split_params(raw: str) -> list[str]:
    """Split a parameter list on commas that are not inside brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in raw:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    tail = "".join(current)
    if tail.strip():
        parts.append(tail)
    return [part for part in parts if part.strip()]


def _check_type(
    type_name: str,
    *,
    path: Path | None,
    where: str,
    handle_names: set[str],
    allow_none: bool = False,
) -> None:
    """Refuse a type outside the lowered vocabulary."""
    name = type_name.strip()
    if name in ALLOWED_TYPE_NAMES:
        return
    if name in handle_names:
        return
    if name == "None":
        if allow_none:
            return
        raise FfiManifestError(
            "PS4006",
            "None is only a return type, never a parameter type",
            path=path,
            where=where,
        )
    if name.startswith("list[") or name.startswith("dict["):
        raise FfiManifestError(
            "PS4006",
            f"the type {name!r} is not a lowered container; use list or dict",
            path=path,
            where=where,
        )
    if name in {"Any", "object", "NoneType"}:
        raise FfiManifestError(
            "PS4006",
            f"the type {name!r} has no marshalling in the FFI lane",
            path=path,
            where=where,
        )
    raise FfiManifestError(
        "PS4006",
        f"the type {name!r} is not in the FFI vocabulary {sorted(ALLOWED_TYPE_NAMES)} and is not a declared handle",
        path=path,
        where=where,
    )


def _reject_name_collisions(
    handles: tuple[ManifestHandle, ...],
    consts: tuple[ManifestConst, ...],
    functions: tuple[ManifestFunction, ...],
    *,
    path: Path | None,
) -> None:
    """One module has one namespace: a name may not be declared twice."""
    seen: dict[str, str] = {}
    for kind, names in (
        ("handle", [handle.name for handle in handles]),
        ("const", [const.name for const in consts]),
        ("function", [fn.name for fn in functions]),
    ):
        for name in names:
            owner = seen.get(name)
            if owner is not None:
                raise FfiManifestError(
                    "PS4007",
                    f"the name {name} is declared as both a {owner} and a {kind}",
                    path=path,
                )
            seen[name] = kind


def _reject_unknown_keys(table: dict[str, Any], allowed: frozenset[str], *, path: Path | None, where: str) -> None:
    unknown = sorted(key for key in table if key not in allowed)
    if unknown:
        raise FfiManifestError(
            "PS4005",
            f"unknown key {unknown[0]!r}; this section accepts {sorted(allowed)}",
            path=path,
            where=where,
        )


def _require_str(table: dict[str, Any], key: str, *, path: Path | None, where: str) -> str:
    if key not in table:
        raise FfiManifestError("PS4005", f"the key {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, str) or not value:
        raise FfiManifestError("PS4005", f"the key {key!r} must be a non-empty string", path=path, where=where)
    return value


def _optional_str(table: dict[str, Any], key: str, *, path: Path | None, where: str) -> str:
    if key not in table:
        return ""
    value = table[key]
    if not isinstance(value, str) or not value:
        raise FfiManifestError("PS4005", f"the key {key!r} must be a non-empty string", path=path, where=where)
    return value


def _optional_str_list(table: dict[str, Any], key: str, *, path: Path | None, where: str) -> tuple[str, ...]:
    if key not in table:
        return ()
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise FfiManifestError("PS4005", f"the key {key!r} must be a list of strings", path=path, where=where)
    return tuple(value)


def _require_int(table: dict[str, Any], key: str, *, path: Path | None, where: str) -> int:
    if key not in table:
        raise FfiManifestError("PS4005", f"the key {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise FfiManifestError("PS4005", f"the key {key!r} must be an integer", path=path, where=where)
    return value


def _require_pin_int(table: dict[str, Any], key: str, *, code: str, path: Path | None, where: str) -> int:
    """Read a version pin. A missing or malformed pin gets the pin's own code."""
    if key not in table:
        raise FfiManifestError(code, f"the pin {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise FfiManifestError(code, f"the pin {key!r} must be an integer", path=path, where=where)
    return value


def _is_relative_path(candidate: str) -> bool:
    path = Path(candidate)
    return not path.is_absolute() and ".." not in path.parts
