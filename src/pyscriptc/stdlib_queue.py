"""Closed import table, in the order the stdlib subset is lowered.

Every name in `QUEUE` has a lowered surface. Importing a module that is not
listed is refused. `typing` is intentionally absent; it stays in the checker.
A name added to `QUEUE` and left out of `LOWERED` imports cleanly and fences
with PS2101 on use.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

# Iterative order from the top-100 import scan. Do not reorder casually:
# tests lock this sequence.
QUEUE: tuple[str, ...] = (
    "math",
    "re",
    "json",
    "urllib.parse",
    "base64",
    "hashlib",
    "struct",
    "datetime",
    "time",
    "collections",
    "functools",
    "itertools",
    "operator",
    "contextlib",
    "os",
    "posixpath",
    "pathlib",
    "stat",
    "fnmatch",
    "warnings",
    "io",
    "sys",
    "importlib",
    "inspect",
    "threading",
    "logging",
    "asyncio",
    "subprocess",
    "socket",
    "ssl",
    "ctypes",
    "string",
    "bisect",
    "errno",
    "importlib.resources",
    "hmac",
    "unicodedata",
    "tempfile",
    "atexit",
    "shutil",
    "enum",
    "dataclasses",
    "random",
    "csv",
    "textwrap",
    "copy",
    "argparse",
    "tomllib",
    "dotenv",
    "yaml",
    "urllib.request",
    "urllib.error",
    "queue",
    "platform",
    "calendar",
    "zipfile",
    "tarfile",
    "selectors",
)

LOWERED: frozenset[str] = frozenset(QUEUE)

# User spellings that resolve onto another module's lowered table. `os.path`
# is the posixpath surface: `import os.path`, `from os.path import join`, and
# `os.path.join(...)` all lower through the posixpath rows while fence
# messages keep the user's dotted spelling.
MODULE_ALIASES: dict[str, str] = {"os.path": "posixpath"}


def canonical_module(name: str) -> str:
    return MODULE_ALIASES.get(name, name)


_F: dict[str, Any] = {"kind": "float"}
_I: dict[str, Any] = {"kind": "int"}
_B: dict[str, Any] = {"kind": "bool"}


def _sig(params: list[dict[str, Any]], ret: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {"params": params, "return": ret, "symbol": symbol, "llvm": "double" if ret["kind"] == "float" else "i64"}


# Domain checks live in ps_rt.c. The symbol is the C function LLVM calls.
MATH_FUNCTIONS: dict[str, dict[str, Any]] = {
    "sqrt": _sig([_F], _F, "ps_math_sqrt"),
    "fabs": _sig([_F], _F, "ps_math_fabs"),
    "sin": _sig([_F], _F, "ps_math_sin"),
    "cos": _sig([_F], _F, "ps_math_cos"),
    "tan": _sig([_F], _F, "ps_math_tan"),
    "exp": _sig([_F], _F, "ps_math_exp"),
    "log": _sig([_F], _F, "ps_math_log"),
    "log2": _sig([_F], _F, "ps_math_log2"),
    "log10": _sig([_F], _F, "ps_math_log10"),
    "hypot": _sig([_F, _F], _F, "ps_math_hypot"),
    "pow": _sig([_F, _F], _F, "ps_math_pow"),
    "atan2": _sig([_F, _F], _F, "ps_math_atan2"),
    "floor": _sig([_F], _I, "ps_math_floor"),
    "ceil": _sig([_F], _I, "ps_math_ceil"),
    "trunc": _sig([_F], _I, "ps_math_trunc"),
    "isnan": _sig([_F], _B, ""),
    "isinf": _sig([_F], _B, ""),
    "isfinite": _sig([_F], _B, ""),
}

# repr() of the CPython 3.14 constants. inf and nan go through float() at emit.
MATH_CONSTANTS: dict[str, str] = {
    "pi": "3.141592653589793",
    "e": "2.718281828459045",
    "tau": "6.283185307179586",
    "inf": "inf",
    "nan": "nan",
}

_STR: dict[str, Any] = {"kind": "str"}
_BYTES: dict[str, Any] = {"kind": "bytes"}
_BUF: dict[str, Any] = {"kind": "buf"}
_LIST_STR: dict[str, Any] = {"kind": "list", "elem": _STR}
_HASH: dict[str, Any] = {"kind": "class", "name": "Hash"}
_PATTERN: dict[str, Any] = {"kind": "class", "name": "Pattern"}
_SPLIT: dict[str, Any] = {"kind": "class", "name": "SplitResult"}
_DICT_STR: dict[str, Any] = {"kind": "dict", "key": _STR, "value": _STR}
_QS: dict[str, Any] = {"kind": "dict", "key": _STR, "value": _LIST_STR}


def _fixed(params: list[dict[str, Any]], ret: dict[str, Any]) -> dict[str, Any]:
    return {"params": params, "return": ret}


def _optional(
    params: list[dict[str, Any]], ret: dict[str, Any], defaults: list[str], names: list[str] | None = None
) -> dict[str, Any]:
    spec: dict[str, Any] = {"params": params, "return": ret, "min": len(params) - len(defaults), "defaults": defaults}
    if names is not None:
        spec["names"] = names
    return spec


# Fixed signatures for the codec wave. json and struct are checked in the
# lowerer: loads follows the annotation, and struct follows a literal format.
WAVE_FUNCTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "re": {
        "escape": _fixed([_STR], _STR),
        "findall": _optional([_STR, _STR, _I], _LIST_STR, ["0"], ["pattern", "string", "flags"]),
        "sub": _optional(
            [_STR, _STR, _STR, _I, _I],
            _STR,
            ["0", "0"],
            ["pattern", "repl", "string", "count", "flags"],
        ),
        "split": _optional(
            [_STR, _STR, _I, _I],
            _LIST_STR,
            ["0", "0"],
            ["pattern", "string", "maxsplit", "flags"],
        ),
        "compile": _optional([_STR, _I], _PATTERN, ["0"], ["pattern", "flags"]),
    },
    "base64": {
        "b64encode": _fixed([_BYTES], _BYTES),
        "standard_b64encode": _fixed([_BYTES], _BYTES),
        "urlsafe_b64encode": _fixed([_BYTES], _BYTES),
        "b64decode": _fixed([_BUF], _BYTES),
        "standard_b64decode": _fixed([_BUF], _BYTES),
        "urlsafe_b64decode": _fixed([_BUF], _BYTES),
    },
    "hashlib": {name: _fixed([_BUF], _HASH) for name in ("md5", "sha1", "sha256", "sha384", "sha512")},
    "urllib.parse": {
        "quote": _optional([_STR, _STR], _STR, ["/"], ["string", "safe"]),
        "quote_plus": _optional([_STR, _STR], _STR, [""], ["string", "safe"]),
        "unquote": _fixed([_STR], _STR),
        "unquote_plus": _fixed([_STR], _STR),
        "urlencode": _fixed([_DICT_STR], _STR),
        "parse_qs": _fixed([_STR], _QS),
        "urlsplit": _fixed([_STR], _SPLIT),
    },
}

# Names that lower, but whose type is not a fixed signature.
SPECIAL_FUNCTIONS: dict[str, frozenset[str]] = {
    "json": frozenset({"loads", "dumps", "load", "dump"}),
    "struct": frozenset({"pack", "unpack"}),
    "re": frozenset({"search", "match", "fullmatch", "subn"}),
}

# Curated distribution packages (pyscriptc-jwt, pyscriptc-orjson) kept as narrow
# exceptions under explicit alias imports (packs track dropped per 2026-10-25 decision).
# The calls lower onto the curated runtime symbols, not onto a stdlib module.
PACK_MODULES: frozenset[str] = frozenset({"jwt", "orjson"})


def wave_function(module: str, name: str) -> dict[str, Any] | None:
    return WAVE_FUNCTIONS.get(module, {}).get(name)


def module_status(name: str) -> str | None:
    """`lowered`, `queued`, or None when the import is outside the table.

    A package root such as `urllib` is lowered once every queued child under
    it has been lowered. A mix of lowered and still-queued children stays
    queued, so a later wave can keep the parent closed without blocking the
    children that already emit.
    """

    name = canonical_module(name)
    manifest = _FFI_MANIFESTS.get(name)
    if manifest is not None:
        return "lowered"
    if name in LOWERED:
        return "lowered"
    if name in QUEUE:
        return "queued"
    children = [item for item in QUEUE if item.startswith(name + ".")]
    if not children:
        return None
    if all(item in LOWERED for item in children):
        return "lowered"
    return "queued"


def queue_index(name: str) -> int:
    root = name.split(".")[0]
    for i, item in enumerate(QUEUE):
        if item == name or item == root or item.startswith(root + "."):
            return i
    return -1


# The `--ffi` manifests of the running compile, set by `with ffi_modules(...)`
# around lowering. This is the one registration point for manifest modules:
# the closed import table is frozen, so a manifest binds through the same
# membership checks the stdlib rows do, scoped to one compile (`.agents/docs/
# ffi/ffi-manifest.md` section 7). Module-level on purpose: a subprocess or a
# long-lived process (the CLI runs one lower per process; the tests use the
# context manager) never sees a stale table.
_FFI_MANIFESTS: dict[str, Any] = {}


def ffi_manifests() -> dict[str, Any]:
    """The manifest modules of the running compile, keyed by module name."""
    return _FFI_MANIFESTS


@contextmanager
def ffi_modules(manifests: Iterable[Any]) -> Iterator[None]:
    """Install `--ffi` manifests for the duration of one lowering pass."""
    global _FFI_MANIFESTS
    saved = _FFI_MANIFESTS
    _FFI_MANIFESTS = {manifest.module: manifest for manifest in manifests}
    try:
        yield
    finally:
        _FFI_MANIFESTS = saved


# The locked dependencies of the running compile, keyed by import name: the
# lane report the compiler fences non-pure lanes against (issue #398).
# Module-level like `_FFI_MANIFESTS`, set around the same lowering pass.
_DEP_LANES: dict[str, dict[str, str]] = {}


def dep_lanes() -> dict[str, dict[str, str]]:
    """The locked dependencies of the running compile, keyed by import name."""
    return _DEP_LANES


@contextmanager
def dependency_lanes(lanes: dict[str, dict[str, str]]) -> Iterator[None]:
    """Install the dependency lane report for the duration of one lowering pass."""
    global _DEP_LANES
    saved = _DEP_LANES
    _DEP_LANES = lanes
    try:
        yield
    finally:
        _DEP_LANES = saved


# boxed:begin
# The island lane of the running compile (issue #553): import roots the
# `[imports]` table pins to `island`, plus an opt-in CLI default that routes
# every import outside the closed table there so a demo needs no config.
# Module-level like `_DEP_LANES`, set around the same lowering pass. The
# per-import policy owner (#157) replaces this table; the trampoline only
# needs the yes/no answer.
_ISLAND_LANES: frozenset[str] = frozenset()
_ISLAND_DEFAULT = False


def island_lane(name: str) -> bool:
    """True when `name` resolves to the island lane for this compile."""
    root = name.split(".", 1)[0]
    return _ISLAND_DEFAULT or name in _ISLAND_LANES or root in _ISLAND_LANES


@contextmanager
def island_policy(names: Iterable[str], default: bool = False) -> Iterator[None]:
    """Install the island lane for the duration of one lowering pass."""
    global _ISLAND_LANES, _ISLAND_DEFAULT
    saved = (_ISLAND_LANES, _ISLAND_DEFAULT)
    _ISLAND_LANES = frozenset(names)
    _ISLAND_DEFAULT = default
    try:
        yield
    finally:
        _ISLAND_LANES, _ISLAND_DEFAULT = saved


# boxed:end


# The staged data-file root of the running compile (issue #410): the
# directory beside the emitted binary that carries the pure wheels' data
# files under their dotted paths. Empty when the compile stages nothing, so
# `importlib.resources.files` searches the host sys.path exactly as before.
# Module-level like `_DEP_LANES`, set around the same lowering pass.
_RESOURCE_ROOT = ""


def resource_root() -> str:
    """The staged data-file root of the running compile, "" when none."""
    return _RESOURCE_ROOT


@contextmanager
def staged_resources(root: str) -> Iterator[None]:
    """Install the staged data-file root for the duration of one lowering pass."""
    global _RESOURCE_ROOT
    saved = _RESOURCE_ROOT
    _RESOURCE_ROOT = root
    try:
        yield
    finally:
        _RESOURCE_ROOT = saved
