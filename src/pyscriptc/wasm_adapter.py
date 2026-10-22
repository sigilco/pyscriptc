"""The pinned WASI preview1 adapter, for the WASI 0.2 component wrapper.

A WASI 0.1 core module is not a component: it imports the preview1 host
functions directly. `wasm-tools component new --adapt` wraps it into a
0.2 component that imports `wasi:cli/run@0.2.x` instead, using an adapter
module that translates between the two worlds.

The adapter is not part of wasm-tools and not part of wasi-sdk: it ships as a
release asset of wasmtime itself. So the pin lives here, with the URL, the
version and the sha256 of the exact bytes, and the file is fetched and
verified before it is used. Without the pin the wrap is not reproducible: a
different adapter build changes the component's imports.

The adapter is architecture independent (it is a pure wasm module), so one
copy serves every host.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

from pyscriptc.errors import IRError

# The wasmtime release that ships the adapter. Pinned with the wasmtime
# version the smoke lane runs, so the adapter and the runtime agree on the
# preview1 surface.
WASMTIME_VERSION = "50.0.0"
ADAPTER_NAME = "wasi_snapshot_preview1.command.wasm"
ADAPTER_URL = f"https://github.com/bytecodealliance/wasmtime/releases/download/v{WASMTIME_VERSION}/{ADAPTER_NAME}"
ADAPTER_SHA256 = "b3de00ac47528237211c6404b4a7e4235a67878665dc3e8853071908c3127a85"

# The wasm-tools release that does the wrapping. Pinned the same way: the
# component encoding is a tool version, not a runtime one.
WASM_TOOLS_VERSION = "1.262.0"

_ADAPTER_ENV = "PYSC_WASI_P1_ADAPTER"


def cache_dir() -> Path:
    override = os.environ.get("PYSC_WASI_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "pyscriptc" / "wasm"


def adapter_path() -> Path:
    """The local path of the verified adapter, fetching it if needed."""
    override = os.environ.get(_ADAPTER_ENV)
    if override:
        path = Path(override)
        if not path.is_file():
            raise IRError(f"{_ADAPTER_ENV} points at a missing file: {path}")
        return path
    path = cache_dir() / f"{WASMTIME_VERSION}-{ADAPTER_NAME}"
    if path.is_file() and _sha256(path) == ADAPTER_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(ADAPTER_URL, timeout=120) as response:  # noqa: S310
            tmp.write_bytes(response.read())
    except OSError as exc:
        raise IRError(f"could not fetch the WASI preview1 adapter from {ADAPTER_URL}: {exc}") from exc
    digest = _sha256(tmp)
    if digest != ADAPTER_SHA256:
        tmp.unlink(missing_ok=True)
        raise IRError(
            f"the WASI preview1 adapter from {ADAPTER_URL} has sha256 {digest}, "
            f"expected {ADAPTER_SHA256}; refusing to wrap with it"
        )
    tmp.replace(path)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wasm_tools() -> str:
    """The `wasm-tools` binary, or a diagnostic naming where to get it."""
    import shutil

    found = shutil.which("wasm-tools")
    if found is None:
        raise IRError(
            "wasm-tools is not on PATH; the WASI 0.2 target needs it to wrap the "
            "core module into a component. Install it from "
            f"https://github.com/bytecodealliance/wasm-tools/releases/tag/v{WASM_TOOLS_VERSION} "
            f"(binary version {WASM_TOOLS_VERSION})."
        )
    return found
