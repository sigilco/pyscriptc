"""Self-contained single-binary compiles (issue #678 follow-up, self-host).

The compiler needs a handful of external files to compile a program: the
runtime C sources the emitter links beside every program, `target.json`
(the pinned CPython), `native_packages.json` (the import lane table), the
vendored typeshed snapshot the checker hands to pyrefly, the pyright stub
overrides, and the python-build-standalone license texts. When the
compiler itself is the compiled artifact (`pyscriptc compile src/pyscriptc
--program --fallback=cpython --emit bin`) those files do not travel: the
binary carries the code, and the package data stays in the source tree,
so the self-built binary cannot compile hello world from a clean machine.

The fix mirrors the program-island staging (issue #665): a program
compile of a directory that IS the pyscriptc package embeds the package
data as a compressed payload in the emitted binary, and the extracted
tree is materialized under `<cache>/selfpack/<digest>/` at run time so
every `Path(__file__)`-relative lookup in the compiled compiler resolves
against the copy.

Only the leaf data files embed. `_pyright_stubs/` and `_typeshed/` are
read through `Path(__file__).with_name(...)` exactly like the runtime C
sources are read through `runtime_dir()`, so they ride the same
extraction. The compiler never resolves its own Python modules from disk
at run time (the island finder serves the embedded text), so nothing
else needs a home.
"""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import zipfile
from pathlib import Path

# The package-relative data roots a relocated compiler binary still needs.
# Nothing here is Python: the island finder serves the module text, and a
# `.pyc` cache would be a second copy of code the binary already carries.
DATA_ROOTS = ("runtime", "_typeshed", "_pyright_stubs", "pbs_licenses")
DATA_FILES = ("target.json", "native_packages.json")

EXTRACT_ENV = "PYSC_SELFPACK_DIR"


def collect_package_data(package_root: Path) -> list[tuple[str, bytes]]:
    """The package's data files, package-relative, in stable order.

    `package_root` is the `src/pyscriptc` directory being compiled. The
    list feeds the island seed, so it must be stable: repr() of it lands
    in the emitted binary.
    """

    payload: list[tuple[str, bytes]] = []
    for name in DATA_FILES:
        path = package_root / name
        if path.is_file():
            payload.append((name, path.read_bytes()))
    for root in DATA_ROOTS:
        directory = package_root / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            payload.append((path.relative_to(package_root).as_posix(), path.read_bytes()))
    return payload


def build_payload(payload: list[tuple[str, bytes]]) -> str:
    """The compressed, base64-encoded payload string the binary embeds.

    zlib and zipfile are stdlib and the embedded interpreter has them
    (the compiled binary links the host libpython), so no build-time
    tool beyond the compiler itself is needed. The result is one
    printable string: the island seed carries it like any other literal.
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in payload:
            archive.writestr(name, body)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def extract_payload(encoded: str, target: Path) -> Path:
    """Materialize the embedded payload under `target` (the digest dir).

    Idempotent: the payload is content-addressed by the digest of its own
    bytes, so an existing tree is reused. A torn extraction (a previous
    run killed mid-write) is replaced wholesale, which matters for CI
    runners killed on timeout.
    """

    raw = base64.b64decode(encoded)
    marker = target / ".selfpack-complete"
    if marker.is_file():
        return target
    scratch = target.parent / f".{target.name}.extracting"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        archive.extractall(scratch)
    (scratch / ".selfpack-complete").write_text("", encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    scratch.rename(target)
    return target


def digest_of(encoded: str) -> str:
    """The content digest of an encoded payload (the extraction dir name)."""

    return hashlib.sha256(encoded.encode("ascii")).hexdigest()[:16]
