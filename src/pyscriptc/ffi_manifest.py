"""The typed extern-function manifest: parse, validate and describe FFI surface.

A manifest is a TOML file that names one FFI module: the Python-visible
functions bound to C ABI thunks, the opaque handle types, the compile-time
constants and the C inputs that go on the cc and link lines. The format is
pinned (`format = 1`), and so is the ABI epoch the compiler speaks
(`ps_abi`): a manifest from another epoch is refused, not guessed at.

Validation raises `FfiManifestError` with a stable `PS4000` code, so tests
and the CLI match on the code and never on prose. Every message names the
offending key and, when the manifest came from a file, the path and the
`[[section]]` row (`where`) the error is in.

Self-host spell: the module compiles native with 0 census fences. The
payload annotations use the `Dict[str, str]` container form with the read
site proving the element type (the census lowerer does not resolve PEP 585
spellings; the ruff per-file ignore keeps the deliberate `Dict` spell).
Members the set and tuple spellings fenced are annotated `set[str]` and
`list[...]`; the record tuple fields carry no default (an empty variadic
tuple default fences). The exception stores its attributes through
class-level annotations and takes its arguments positionally (`raise` of
an exception whose `__init__` spells more than one required scalar fences,
so `location` rides the message), the regexes spell numbered groups
(`Match.group("name")` does not lower, `m.group(2)` does), the version
pins are the int literals 1 spelled here rather than imported (the drift
is the PS4003/PS4004 refusal tests), and `tomllib.loads` is imported
inside its function with a `Dict[str, Dict[str, str]]` result annotation
and a broad `except Exception` (a `tomllib.TOMLDecodeError` handler does
not lower).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from pyscriptc.errors import SpikeError

SUPPORTED_FORMATS = frozenset({1})
CURRENT_PS_ABI = 1
SCALAR_TYPES = frozenset({"int", "float", "bool", "str", "bytes"})
CONTAINER_TYPES = frozenset({"list", "dict"})
ALLOWED_TYPE_NAMES = SCALAR_TYPES | CONTAINER_TYPES
CONST_TYPES = frozenset({"int", "float", "str"})
_MANIFEST_KEYS = frozenset({"module", "ps_abi", "format", "libs", "lib_dirs"})
_SOURCE_KEYS = frozenset({"path", "objects"})
_HANDLE_KEYS = frozenset({"name", "c_type", "frees"})
_CONST_KEYS = frozenset({"name", "type", "value"})
_FN_KEYS = frozenset({"name", "py", "c_symbol", "abi", "borrows", "consumes"})
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_C_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+-]*$")
_C_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*\*$|^[A-Za-z_][A-Za-z0-9_ ]+$")
_CONTAINER = re.compile(r"^(list|dict)\[(.+)\]$")
_SIGNATURE = re.compile(r"^\(([^()]*)\)\s*(?:->\s*([^()]+))?$")
_PARAM = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^=,]+)(?:\s*=\s*(.+))?$")

class FfiManifestError(SpikeError):
    """A manifest that does not validate. `code` is stable and testable."""

    code: str
    message: str
    path: Path | None
    where: str

    def __init__(self, code: str, message: str, manifest_path: Path | None = None, where: str = "") -> None:
        location = ""
        if manifest_path is not None:
            location = f"{manifest_path}"
            if where:
                location = f"{location}: {where}"
        elif where:
            location = where
        super().__init__(code, f"{location}: {message}" if location else message)
        self.code = code
        self.message = message
        self.path = manifest_path
        self.where = where


@dataclass(frozen=True)
class ManifestSource:
    """A C unit or archive that the link line gains.

    `path` names one C source the target cc compiles; `objects` names
    prebuilt archives or objects that go straight on the link line. The two
    are mutually exclusive (the validator refuses both or neither).
    """

    path: str = ""
    objects: list[str] = field(default_factory=list)

    def resolved(self, base: Path) -> list[Path]:
        """The paths of this source, resolved relative to the manifest file."""
        out: list[Path] = []
        if self.path:
            out.append(base / self.path)
        else:
            for obj in self.objects:
                out.append(base / obj)
        return out

    @property
    def is_compile_unit(self) -> bool:
        """True when the target cc compiles this unit; False for prebuilt objects."""
        return bool(self.path)


@dataclass(frozen=True)
class ManifestHandle:
    """An opaque pointer that crosses the boundary as a token."""

    name: str
    c_type: str
    frees: str = ""


@dataclass(frozen=True)
class ManifestConst:
    """A compile-time constant exposed on the module.

    `kind` is the manifest type name (`int`, `float` or `str`) and `value`
    is the constant spelled the way the lowerer spells a leaf constant:
    ints and floats as `str(value)`, strings raw. The read sites in the
    lowerer spell `float(...)`/`int(...)` conversions on the string.
    """

    name: str
    type: str
    value: str


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
    params: list[ManifestParam]
    returns: str
    borrows: list[HandleRef]
    consumes: list[HandleRef]
    handle_names: set[str]

    @property
    def arity(self) -> int:
        return len(self.params)

    @property
    def required_arity(self) -> int:
        return sum(1 for param in self.params if param.required)

    def borrowed_params(self) -> set[str]:
        """The parameter names this call borrows: the handle stays alive."""
        out: set[str] = set()
        for ref in self.borrows:
            out.add(ref.param)
        return out

    def consumed_params(self) -> set[str]:
        """The parameter names this call consumes: the handle dies here."""
        out: set[str] = set()
        for ref in self.consumes:
            out.add(ref.param)
        return out

    def returns_handle(self) -> str | None:
        """The handle type this function returns, if the return is a handle.

        A returned handle transfers ownership to the caller: the callee does
        not free it, the caller does.
        """
        if self.returns in self.handle_names:
            return self.returns
        return None

    def param_handle(self, param: str) -> str | None:
        """The handle type a parameter carries, if it carries one."""
        for declared in self.params:
            if declared.name == param:
                if declared.type in self.handle_names:
                    return declared.type
                return None
        return None


@dataclass(frozen=True)
class FfiManifest:
    """A validated manifest: the module surface plus the link inputs.

    `libs` and `lib_dirs` are the per-manifest system-library flags: each
    `libs` entry becomes one `-l` argument and each `lib_dirs` entry one
    `-L` argument, emitted only when the manifest declares them (a manifest
    that vendors its C adds nothing to the default `-lm` tail).
    """

    module: str
    ps_abi: int
    format: int
    path: Path
    sources: list[ManifestSource]
    handles: list[ManifestHandle]
    consts: list[ManifestConst]
    functions: list[ManifestFunction]
    handle_names: set[str]
    libs: list[str]
    lib_dirs: list[str]

    def function(self, name: str) -> ManifestFunction | None:
        """The function bound to a Python-visible name, if any."""
        for fn in self.functions:
            if fn.name == name:
                return fn
        return None

    def handle(self, name: str) -> ManifestHandle | None:
        """The handle bound to a Python-visible name, if any."""
        for entry in self.handles:
            if entry.name == name:
                return entry
        return None

    def frees_symbol(self, handle: str) -> str:
        """The C symbol that releases a handle, or the empty string."""
        found = self.handle(handle)
        if found is not None:
            return found.frees
        return ""

    def const(self, name: str) -> ManifestConst | None:
        """The constant bound to a Python-visible name, if any."""
        for entry in self.consts:
            if entry.name == name:
                return entry
        return None

    def unit_paths(self) -> list[Path]:
        """Every C unit and archive the manifest adds to the link line.

        Flat and order-preserving. The linker itself wants the two lists
        apart, so it calls `compile_units()` and `link_units()` instead.
        """
        base = self.path.parent
        units: list[Path] = []
        for source in self.sources:
            units.extend(source.resolved(base))
        return units

    def compile_units(self) -> list[Path]:
        """The C sources the target cc compiles, resolved against the manifest file."""
        base = self.path.parent
        units: list[Path] = []
        for source in self.sources:
            if source.is_compile_unit:
                units.extend(source.resolved(base))
        return units

    def link_units(self) -> list[Path]:
        """The prebuilt archives and objects that go straight on the link line."""
        base = self.path.parent
        units: list[Path] = []
        for source in self.sources:
            if not source.is_compile_unit:
                units.extend(source.resolved(base))
        return units

    def link_flags(self) -> list[str]:
        """The `-L` and `-l` arguments this manifest adds, in that order.

        A manifest that vendors its C contributes nothing here, so the link
        line of a manifest-free build stays exactly the shape it had before
        the FFI gate.
        """
        flags: list[str] = []
        for directory in self.lib_dirs:
            flags.append(f"-L{directory}")
        for library in self.libs:
            flags.append(f"-l{library}")
        return flags


def parse_manifest_text(text: str, *, path: Path | None = None, ps_abi: int = CURRENT_PS_ABI) -> FfiManifest:
    """Parse and validate a manifest from TOML text.

    `ps_abi` is the epoch the running compiler stamps; a manifest pinning a
    different epoch is refused.
    """
    import tomllib

    try:
        data: Dict[str, Dict[str, str]] = tomllib.loads(text)
    except Exception as exc:
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
    except UnicodeDecodeError:
        raise FfiManifestError("PS4001", "the manifest is not valid UTF-8 text", path=file_path) from None
    return parse_manifest_text(text, path=file_path, ps_abi=ps_abi)


def load_manifests(paths: list[Path | str], *, ps_abi: int = CURRENT_PS_ABI) -> list[FfiManifest]:
    """Load several manifests and refuse two that claim the same module."""
    manifests = [load_manifest(path, ps_abi=ps_abi) for path in paths]
    claimed: Dict[str, Path] = {}
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


def _build(data: Dict[str, Dict[str, str]], *, path: Path | None, ps_abi: int) -> FfiManifest:
    where = "[manifest]"
    header = data.get("manifest")
    if header is None:
        raise FfiManifestError("PS4002", "the manifest has no [manifest] section", path=path)
    if not isinstance(header, dict):
        raise FfiManifestError("PS4002", "[manifest] is not a table", path=path, where=where)

    module = _require_str(header, "module", path=path, where=where)
    if not _MODULE.match(module):
        raise FfiManifestError(
            "PS4002",
            f"the module name {module!r} is not a dotted identifier",
            path=path,
            where=where,
        )

    fmt = _require_pin_int(header, "format", "PS4003", path=path, where=where)
    if fmt != 1:
        raise FfiManifestError(
            "PS4003",
            f"the manifest format {fmt} is not supported; this compiler reads format {sorted(SUPPORTED_FORMATS)}",
            path=path,
            where=where,
        )

    pinned = _require_pin_int(header, "ps_abi", "PS4004", path=path, where=where)
    if pinned != ps_abi:
        raise FfiManifestError(
            "PS4004",
            f"the manifest pins ps_abi {pinned} but this compiler targets ps_abi {ps_abi}",
            path=path,
            where=where,
        )

    _reject_unknown_keys(header, _MANIFEST_KEYS, path=path, where=where)
    libs = _optional_str_list(header, "libs", path=path, where=where)
    lib_dirs = _optional_str_list(header, "lib_dirs", path=path, where=where)
    for library in libs:
        _check_link_name(library, "libs", path=path, where=where)
    for directory in lib_dirs:
        _check_lib_dir(directory, path=path, where=where)

    sources = _build_sources(data, path=path)
    handles = _build_handles(data, path=path)
    consts = _build_consts(data, path=path)
    handle_names: set[str] = set()
    for entry in handles:
        handle_names.add(entry.name)
    functions = _build_functions(
        data,
        path=path,
        handle_names=handle_names,
        handle_frees=_frees_table(handles),
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
        handle_names=handle_names,
        libs=libs,
        lib_dirs=lib_dirs,
    )


def _frees_table(handles: list[ManifestHandle]) -> Dict[str, str]:
    """The handle name -> frees symbol table of one manifest."""
    out: Dict[str, str] = {}
    for entry in handles:
        out[entry.name] = entry.frees
    return out


def _check_link_name(library: str, key: str, path: Path | None, where: str) -> None:
    """A `libs` entry is the bare soname: `z`, not `-lz` or `libz.so`."""
    if not _LIB_NAME.match(library):
        raise FfiManifestError(
            "PS4005",
            f"the {key} entry {library!r} is not a bare library name (write z, not -lz or libz)",
            path=path,
            where=where,
        )


def _check_lib_dir(directory: str, path: Path | None, where: str) -> None:
    """A `lib_dirs` entry is a directory path, not a flag."""
    if directory.startswith("-") or not directory.strip():
        raise FfiManifestError(
            "PS4005",
            f"the lib_dirs entry {directory!r} is not a bare directory path",
            path=path,
            where=where,
        )


def _build_sources(data: Dict[str, Dict[str, str]], *, path: Path | None) -> list[ManifestSource]:
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
    return sources


def _build_handles(data: Dict[str, Dict[str, str]], *, path: Path | None) -> list[ManifestHandle]:
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
    return handles


def _build_consts(data: Dict[str, Dict[str, str]], *, path: Path | None) -> list[ManifestConst]:
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
        consts.append(ManifestConst(name=name, type=const_type, value=_const_text(value)))
    return consts


def _const_text(value: Any) -> str:
    """One const value spelled the way the lowerer spells a leaf constant."""
    if isinstance(value, str):
        return value
    return str(value)


def _build_functions(
    data: Dict[str, Dict[str, str]],
    *,
    path: Path | None,
    handle_names: set[str],
    handle_frees: Dict[str, str],
) -> list[ManifestFunction]:
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
        borrowed: set[str] = set()
        for ref in resolved_borrows:
            borrowed.add(ref.param)
        consumed: set[str] = set()
        for ref in resolved_consumes:
            consumed.add(ref.param)
        overlap = sorted(borrowed & consumed)
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
                handle_names=handle_names,
            )
        )
    return functions


def _resolve_handle_refs(
    refs: list[str],
    params: list[ManifestParam],
    handle_names: set[str],
    *,
    path: Path | None,
    where: str,
    key: str,
) -> list[HandleRef]:
    """Map ownership annotations onto argument-level handle references.

    An annotation names either the parameter that carries the handle (`db`)
    or the handle type itself (`Connection`). The parameter form is the
    common one, and it is the one that distinguishes two arguments of the
    same handle type.
    """
    resolved: list[HandleRef] = []
    by_name: Dict[str, ManifestParam] = {}
    for param in params:
        by_name[param.name] = param
    for ref in refs:
        carrier = by_name.get(ref)
        if carrier is not None:
            if carrier.type not in handle_names:
                raise FfiManifestError(
                    "PS4008",
                    f"the {key} entry {ref!r} names the parameter {ref}, which is not a handle",
                    path=path,
                    where=where,
                )
            resolved.append(HandleRef(param=ref, handle=carrier.type))
            continue
        if ref not in handle_names:
            raise FfiManifestError(
                "PS4008",
                f"the {key} entry {ref!r} is neither a declared handle nor a parameter of this function",
                path=path,
                where=where,
            )
        carriers: list[str] = []
        for param in params:
            if param.type == ref:
                carriers.append(param.name)
        if not carriers:
            raise FfiManifestError(
                "PS4008",
                f"the {key} entry {ref!r} names a handle that no parameter carries",
                path=path,
                where=where,
            )
        resolved.append(HandleRef(param=carriers[0], handle=ref))
    return resolved


def _parse_signature(
    py: str,
    *,
    path: Path | None,
    where: str,
    handle_names: set[str],
) -> tuple[list[ManifestParam], str]:
    """Parse a `.pyi`-shaped signature into parameters and a return type.

    The signature regex spells numbered groups (1 params, 2 return) because
    a named-group read (`Match.group("params")`) does not lower.
    """
    match = _SIGNATURE.match(py.strip())
    if match is None:
        raise FfiManifestError(
            "PS4006",
            f"the signature {py!r} is not of the form (name: type, ...) -> type",
            path=path,
            where=where,
        )
    raw_params: str | None = match.group(1)
    params: list[ManifestParam] = []
    names: set[str] = set()
    if raw_params is not None and raw_params.strip():
        for piece in _split_params(raw_params.strip()):
            param_match = _PARAM.match(piece.strip())
            if param_match is None:
                raise FfiManifestError(
                    "PS4006",
                    f"the parameter {piece.strip()!r} is not of the form name: type",
                    path=path,
                    where=where,
                )
            name = param_match.group(1)
            if name in names:
                raise FfiManifestError(
                    "PS4007",
                    f"the parameter {name} is declared twice",
                    path=path,
                    where=where,
                )
            names.add(name)
            raw_type: str | None = param_match.group(2)
            type_name = (raw_type or "").strip()
            _check_type(type_name, path=path, where=where, handle_names=handle_names)
            raw_default: str | None = param_match.group(3)
            default = raw_default.strip() if raw_default is not None else None
            params.append(ManifestParam(name=name, type=type_name, default=default))
    raw_ret: str | None = match.group(2)
    returns = (raw_ret or "None").strip()
    _check_type(returns, path=path, where=where, handle_names=handle_names, allow_none=True)
    return params, returns


def _check_container(
    name: str,
    *,
    path: Path | None,
    where: str,
    handle_names: set[str],
) -> None:
    """Validate a `list[T]`, `dict[K, V]` or `list[dict[K, V]]` annotation.

    Scalars and one container inside a list are the shapes the lane
    marshals: `list[str]` is a returned column and `list[dict[str, str]]`
    is the sqlite row shape. Anything deeper, a container with a handle
    inside, or a typed dict value that is itself a container, is refused.

    The container regex spells numbered groups (1 head, 2 inner) because a
    named-group read does not lower.
    """
    match = _CONTAINER.match(name)
    if match is None:
        raise FfiManifestError(
            "PS4006",
            f"the container type {name!r} is not of the form list[T] or dict[K, V]",
            path=path,
            where=where,
        )
    raw_head: str | None = match.group(1)
    head = raw_head or ""
    raw_inner: str | None = match.group(2)
    inner = (raw_inner or "").strip()
    if head == "list":
        nested = _CONTAINER.match(inner)
        if nested is not None:
            # The one allowed nesting: `list[dict[K, V]]`. A list of lists
            # has no subset element type (a list value is a pointer, not a
            # scalar the runtime slot can hold).
            raw_nested_head: str | None = nested.group(1)
            if raw_nested_head == "dict":
                _check_type(inner, path=path, where=where, handle_names=handle_names)
                return
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} nests a list; the lane marshals list of scalars or records",
                path=path,
                where=where,
            )
        if "," in inner and not _inside_brackets(inner):
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} takes one element type",
                path=path,
                where=where,
            )
        if inner == "list" or inner == "dict":
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} nests a bare container; spell the inner types, list[dict[K, V]]",
                path=path,
                where=where,
            )
        if inner in handle_names:
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} holds the handle {inner}; a handle has no subset element type "
                "(the value would be a bare ptr the subset cannot touch)",
                path=path,
                where=where,
            )
        _check_type(inner, path=path, where=where, handle_names=handle_names)
        return
    pieces = _split_params(inner)
    if len(pieces) != 2:
        raise FfiManifestError(
            "PS4006",
            f"the type {name!r} takes exactly two type arguments, key and value",
            path=path,
            where=where,
        )
    for piece in pieces:
        piece = piece.strip()
        if piece == "list" or piece == "dict" or _CONTAINER.match(piece):
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} nests a container; the lane marshals scalar rows only",
                path=path,
                where=where,
            )
        if piece in handle_names:
            raise FfiManifestError(
                "PS4006",
                f"the type {name!r} holds the handle {piece}; a handle has no subset element type "
                "(the value would be a bare ptr the subset cannot touch)",
                path=path,
                where=where,
            )
        _check_type(piece, path=path, where=where, handle_names=handle_names)


def _inside_brackets(text: str) -> bool:
    depth = 0
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth > 0:
            return True
    return False


def type_ir(type_name: str, handle_names: frozenset[str] | set[str] = frozenset()) -> Dict[str, str]:
    """The IR type dict for a manifest type name.

    The scalar names map onto the type dicts the lowerer already uses
    (`int` to `{"kind": "int"}`). A declared handle rides the `class` path,
    which the emitter lowers as a `ptr` (`.agents/docs/ffi/ffi-manifest.md`
    section 3). The caller passes the handle names so the mapping stays a
    pure function of the manifest.

    A handle type carries the `handle` marker so the lowerer can tell an
    opaque token from a user class: a handle has no fields and no
    arithmetic (issue #224).
    """
    name = type_name.strip()
    if name in handle_names:
        return handle_type(name)
    if name == "int":
        return {"kind": "int"}
    if name == "float":
        return {"kind": "float"}
    if name == "bool":
        return {"kind": "bool"}
    if name == "str":
        return {"kind": "str"}
    if name == "bytes":
        return {"kind": "bytes"}
    if name == "list":
        return {"kind": "list", "elem": {"kind": "none"}}
    if name == "dict":
        return {"kind": "dict", "key": {"kind": "none"}, "value": {"kind": "none"}}
    if name.startswith("list["):
        inner = name[5:-1].strip()
        return {"kind": "list", "elem": type_ir(inner, handle_names)}
    if name.startswith("dict["):
        pieces = _split_params(name[5:-1].strip())
        return {
            "kind": "dict",
            "key": type_ir(pieces[0].strip(), handle_names),
            "value": type_ir(pieces[1].strip(), handle_names),
        }
    if name == "None":
        return {"kind": "none"}
    raise FfiManifestError("PS4006", f"the type {name!r} has no IR type in the FFI vocabulary")


def handle_type(name: str) -> Dict[str, str]:
    """The IR type dict of a handle, with the `handle` marker set.

    The lowerer uses the marker to refuse field access and arithmetic on an
    opaque token, and to keep the handle out of the user-class machinery.
    """
    return {"kind": "class", "name": name, "handle": True}


def is_handle_type(typ: Dict[str, str]) -> bool:
    """True when an IR type dict is an opaque manifest handle."""
    return typ.get("kind") == "class" and bool(typ.get("handle"))


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
    out: list[str] = []
    for part in parts:
        if part.strip():
            out.append(part)
    return out


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
        _check_container(name, path=path, where=where, handle_names=handle_names)
        return
    if name == "Any" or name == "object" or name == "NoneType":
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
    handles: list[ManifestHandle],
    consts: list[ManifestConst],
    functions: list[ManifestFunction],
    *,
    path: Path | None,
) -> None:
    """One module has one namespace: a name may not be declared twice."""
    seen: Dict[str, str] = {}
    for kind, names in (
        ("handle", [entry.name for entry in handles]),
        ("const", [entry.name for entry in consts]),
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


def _reject_unknown_keys(table: Dict[str, str], allowed: set[str], *, path: Path | None, where: str) -> None:
    unknown = sorted(key for key in table if key not in allowed)
    if unknown:
        raise FfiManifestError(
            "PS4005",
            f"unknown key {unknown[0]!r}; this section accepts {sorted(allowed)}",
            path=path,
            where=where,
        )


def _require_str(table: Dict[str, str], key: str, *, path: Path | None, where: str) -> str:
    if key not in table:
        raise FfiManifestError("PS4005", f"the key {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, str) or not value:
        raise FfiManifestError("PS4005", f"the key {key!r} must be a non-empty string", path=path, where=where)
    return value


def _optional_str(table: Dict[str, str], key: str, *, path: Path | None, where: str) -> str:
    if key not in table:
        return ""
    value = table[key]
    if not isinstance(value, str) or not value:
        raise FfiManifestError("PS4005", f"the key {key!r} must be a non-empty string", path=path, where=where)
    return value


def _optional_str_list(table: Dict[str, str], key: str, *, path: Path | None, where: str) -> list[str]:
    if key not in table:
        return []
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise FfiManifestError("PS4005", f"the key {key!r} must be a list of strings", path=path, where=where)
    return list(value)


def _require_int(table: Dict[str, str], key: str, *, path: Path | None, where: str) -> int:
    if key not in table:
        raise FfiManifestError("PS4005", f"the key {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise FfiManifestError("PS4005", f"the key {key!r} must be an integer", path=path, where=where)
    return value


def _require_pin_int(table: Dict[str, str], key: str, code: str, *, path: Path | None, where: str) -> int:
    """Read a version pin. A missing or malformed pin gets the pin's own code."""
    if key not in table:
        raise FfiManifestError(code, f"the pin {key!r} is required", path=path, where=where)
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise FfiManifestError(code, f"the pin {key!r} must be an integer", path=path, where=where)
    return value


def _is_relative_path(candidate: str) -> bool:
    path = Path(candidate)
    if path.is_absolute():
        return False
    for part in path.parts:
        if part == "..":
            return False
    return True
