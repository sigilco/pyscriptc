# Pack format spec (DROPPED)

Status: permanently dropped and retired per the 2026-10-25 typed-subset positioning decision. The pack format track (issues #430, #377, #378, #456, #458, #411, #152) is closed. PyPI consumption goes through compile-from-source (typed) or the island floor (untyped), and CPython consumption goes through `--emit binding`. Curated distribution packages (`pyscriptc-orjson`, `pyscriptc-jwt`) remain narrow standalone exceptions under explicit alias imports.

The historical spec is recoverable from git history (this file before 2026-10-28). What still matters, because the two shipped adapters still follow it:

- A pack is a directory named `pyscriptc-<name>` with a `pack.toml` identity block (`name`, `version`, `replaces`, `replaces-version`, `ps-abi`, `format`), a `surface/<import>/__init__.pyi` per claimed import, C sources xor per-triple prebuilt objects, and a `conformance/suite.toml` with expected CPython outcomes.
- The FFI manifest tables (`[[ffi.source]]`, `[[ffi.handle]]`, `[[ffi.const]]`, `[[ffi.fn]]`) embed verbatim under the `[ffi]` prefix; the validator (`src/pyscriptc/ffi_manifest.py`, PS4001 to PS4009) enforces them unchanged.
- Everything a user program can call must appear in a `.pyi`; anything outside the surface fences at compile time (`PS2001`), never approximated.
