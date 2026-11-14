"""Closed import table, in the order the stdlib subset is lowered.

Every name in `QUEUE` has a lowered surface. Importing a module that is not
listed is refused. `typing` is intentionally absent; it stays in the checker.
A name added to `QUEUE` and left out of `LOWERED` imports cleanly and fences
with PS2101 on use.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from collections.abc import Iterator as _Iterator
from contextlib import contextmanager

from pyscriptc.ffi_manifest import FfiManifest
from pyscriptc.sigrec import (
    Sig,
    T,
    optional,
    sig_for,
    t_class,
    t_dict,
    t_leaf,
    t_list,
)

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
    "statistics",
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
    "uuid",
    "binascii",
    "shlex",
    "secrets",
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


_F: T = t_leaf("float")
_I: T = t_leaf("int")
_B: T = t_leaf("bool")


def _fixed(params: list, ret) -> Sig:
    """A wave row: fully fixed, no symbol key."""
    return Sig(list(params), ret, None, None, -1, None)


def _fixed_sig(params: list, ret, symbol: str = "") -> Sig:
    """A math row: fully fixed with an ABI symbol ("" keys the symbol
    pair, the spelling the isnan family and the wave rows carry)."""
    return Sig(list(params), ret, None, None, -1, symbol or "")


# Domain checks live in ps_rt.c. The symbol is the C function LLVM calls.
MATH_FUNCTIONS: dict[str, Sig] = {
    "sqrt": _fixed_sig([_F], _F, "ps_math_sqrt"),
    "fabs": _fixed_sig([_F], _F, "ps_math_fabs"),
    "sin": _fixed_sig([_F], _F, "ps_math_sin"),
    "cos": _fixed_sig([_F], _F, "ps_math_cos"),
    "tan": _fixed_sig([_F], _F, "ps_math_tan"),
    "exp": _fixed_sig([_F], _F, "ps_math_exp"),
    "log": _fixed_sig([_F], _F, "ps_math_log"),
    "log2": _fixed_sig([_F], _F, "ps_math_log2"),
    "log10": _fixed_sig([_F], _F, "ps_math_log10"),
    "hypot": _fixed_sig([_F, _F], _F, "ps_math_hypot"),
    "pow": _fixed_sig([_F, _F], _F, "ps_math_pow"),
    "atan2": _fixed_sig([_F, _F], _F, "ps_math_atan2"),
    "floor": _fixed_sig([_F], _I, "ps_math_floor"),
    "ceil": _fixed_sig([_F], _I, "ps_math_ceil"),
    "trunc": _fixed_sig([_F], _I, "ps_math_trunc"),
    "asin": _fixed_sig([_F], _F, "ps_math_asin"),
    "acos": _fixed_sig([_F], _F, "ps_math_acos"),
    "atan": _fixed_sig([_F], _F, "ps_math_atan"),
    "sinh": _fixed_sig([_F], _F, "ps_math_sinh"),
    "cosh": _fixed_sig([_F], _F, "ps_math_cosh"),
    "tanh": _fixed_sig([_F], _F, "ps_math_tanh"),
    "asinh": _fixed_sig([_F], _F, "ps_math_asinh"),
    "acosh": _fixed_sig([_F], _F, "ps_math_acosh"),
    "atanh": _fixed_sig([_F], _F, "ps_math_atanh"),
    "degrees": _fixed_sig([_F], _F, "ps_math_degrees"),
    "radians": _fixed_sig([_F], _F, "ps_math_radians"),
    "fmod": _fixed_sig([_F, _F], _F, "ps_math_fmod"),
    "factorial": _fixed_sig([_I], _I, "ps_math_factorial"),
    "lcm": _fixed_sig([_I, _I], _I, "ps_math_lcm"),
    "isnan": _fixed_sig([_F], _B),
    "isinf": _fixed_sig([_F], _B),
    "isfinite": _fixed_sig([_F], _B),
}

# repr() of the CPython 3.14 constants. inf and nan go through float() at emit.
MATH_CONSTANTS: dict[str, str] = {
    "pi": "3.141592653589793",
    "e": "2.718281828459045",
    "tau": "6.283185307179586",
    "inf": "inf",
    "nan": "nan",
}

_STR: T = t_leaf("str")
_BYTES: T = t_leaf("bytes")
_BUF: T = t_leaf("buf")
_LIST_STR: T = t_list(_STR)
_HASH: T = t_class("Hash")
_PATTERN: T = t_class("Pattern")
_SPLIT: T = t_class("SplitResult")
_DICT_STR: T = t_dict(_STR, _STR)
_QS: T = t_dict(_STR, _LIST_STR)


def _optional(params: list, ret, defaults: list[str], names: list[str] | None = None) -> Sig:
    """A wave row with an optional tail and no symbol key."""
    row = optional(params, ret, defaults, names)
    return Sig(row.params, row.ret, row.defaults, row.names, row.min, None)


# Fixed signatures for the codec wave. json and struct are checked in the
# lowerer: loads follows the annotation, and struct follows a literal format.
WAVE_FUNCTIONS: dict[str, dict[str, Sig]] = {
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


def math_function(name: str) -> dict[str, object] | None:
    """The dict row of a math call, `sig_for`-cached on first use.

    The record-to-dict boundary for `MATH_FUNCTIONS`: the table stores `Sig`
    records, the lowerer and the LLVM emitter read the dict shape.
    """

    row = MATH_FUNCTIONS.get(name)
    return None if row is None else sig_for(row)


def wave_function(module: str, name: str) -> dict[str, object] | None:
    """The dict row of a wave call, `sig_for`-cached on first use.

    The lowerer reads the dict shape (`_fixed_call`); the table stores the
    records the typing-hygiene slice introduced, so the accessor is the
    record-to-dict boundary.
    """

    row = WAVE_FUNCTIONS.get(module, {}).get(name)
    return sig_for(row) if row is not None else None


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
_FFI_MANIFESTS: dict[str, FfiManifest] = {}


def ffi_manifests() -> dict[str, FfiManifest]:
    """The manifest modules of the running compile, keyed by module name."""
    return _FFI_MANIFESTS


@contextmanager
def ffi_modules(manifests: Iterable[FfiManifest]) -> _Iterator[None]:
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
