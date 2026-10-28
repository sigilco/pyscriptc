---
title: Stability and contracts
description: Support tiers, stability guarantees, versioned axes, and internal compiler surfaces.
---

# Stability and contracts

pyscriptc compiles the typed subset of Python 3.14.
Code either compiles to native code or runs through embedded CPython (fallback mode); the compiler provides no shims or dynamic runtime emulation for untyped Python.

This page states what is unstable, not what works.
The live compat status across modules and language features lives on the [compat site]({{PYSC_COMPAT_URL}}).
Known runtime differences from standard CPython are cataloged in [Divergences from CPython](divergences.md), and the codes that stop a compile are in [Diagnostics](diagnostics.md).

## Support tiers

The compiler codebase and its public interface are organized into three explicit stability tiers: Frozen contracts (Tier 1), Best-effort driver API (Tier 2), and Internal implementation surfaces (Tier 3).

### Tier 1: Frozen contracts

Tier 1 surfaces represent contractual boundaries between the compiler, external tools, emitted artifacts, and user code.
Changes to any Tier 1 contract require a formal recorded decision, following the same governance discipline as the frozen v2 evaluation gates.

Every Tier 1 guarantee is grounded in an owning file:

- **IR JSON schema and `irVersion`**: defined and validated in [`src/pyscriptc/ir.py`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/ir.py). The textual IR JSON emitted via `pyscriptc compile --emit ir` carries an explicit `irVersion`, the typed `subset` identifier, and the CPython target stamp. Modules failing schema validation or carrying mismatched target versions are rejected by `validate_module`.
- **Runtime C ABI and `PS_ABI`**: defined in [`src/pyscriptc/runtime/ps_rt.h`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/runtime/ps_rt.h) and pinned by `CURRENT_PS_ABI` in [`src/pyscriptc/ffi_manifest.py`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/ffi_manifest.py). The runtime header maintains append-only struct definitions and frozen `ps_*` C symbol signatures. Any modification to a `ps_*` signature or `%ps.*` LLVM struct type bumps `CURRENT_PS_ABI` or requires an explicit recorded no-bump decision.
- **FFI manifest format**: defined in [`src/pyscriptc/ffi_manifest.py`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/ffi_manifest.py). Manifest files (`ffi.toml`) enforce `format = 1` and require `ps_abi = CURRENT_PS_ABI`. Manifests with mismatched ABI versions fail validation up front with diagnostic `PS4004`.
- **CLI exit codes**: defined in [`src/pyscriptc/cli.py`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/cli.py). The CLI exit codes are contractually fixed: `0` for successful compilation or child run status, `1` for internal compiler faults (`SpikeError`), `2` for type-checker preflight failures (`CheckFailed`) or argument syntax errors, `3` for typed subset refusals and unsupported constructs, and `4` for target architecture or CPython version fit mismatches (`VersionError`, `FitError`).
- **Diagnostic codes**: defined in [`src/pyscriptc/diagnostics.py`](https://github.com/sigilco/pyscriptc/blob/master/src/pyscriptc/diagnostics.py). Diagnostic codes (`PSxxxx`) are permanent, append-only identifiers divided into numeric categories: syntax and compilation (`PS1xxx`), types and static semantics (`PS2xxx`), runtime operations (`PS3xxx`), and architectural refusals (`PS4xxx`). A code is never repurposed or renumbered.
- **Divergence-list numbering**: defined in [`docs/reference/divergences.md`](divergences.md). Every intentional divergence from CPython 3.14 receives a permanent numeric identifier. When an entry is resolved or removed, existing numbers are preserved or renumbered through an explicit decision record.

#### Dropped pack format

The pack format track (`pack.toml`, binary wheel pack distribution, and precompiled library archives) was permanently dropped per the 2026-10-25 typed-subset positioning decision (Decision 5). The pack format is not a stability tier. PyPI dependencies are consumed either by compiling pure-typed source directly or by routing through an embedded in-process CPython (fallback mode); consumption of compiled code from CPython routes through `--emit binding` (`ctypes`). Curated distribution packages (`pyscriptc-orjson`, `pyscriptc-jwt`) remain as narrow standalone exceptions under explicit alias imports.

### Tier 2: Best-effort driver API

Tier 2 encompasses programmatic Python APIs and machine-readable CLI formats designed for tool integrators, build runners, and editor extensions. These interfaces avoid arbitrary breaking changes within minor release lines, and any breaking adjustment is documented in release changelogs. They are not frozen contracts.

- **Programmatic driver entrypoints**:
  - `pyscriptc.lower.lower_source`: compiles an annotated Python AST or source string into the intermediate representation.
  - `pyscriptc.emitter.emit_ir_text`: serializes validated IR dictionaries into canonical JSON text.
  - `pyscriptc.ffi_manifest.load_manifests`: parses and validates external FFI TOML specifications.
  - `pyscriptc.imports.build_import_report`: categorizes module imports into standard library, native, and embedded-CPython lanes.
  - `pyscriptc.factsbridge.FactsProvider`: provides static checker inference facts to the compiler pipeline.
- **Machine-readable JSON outputs**:
  - `pyscriptc compile --debug=summary` and `--print diagnostics`: emits structured stage timings, lists of `PSxxxx` codes, and capability summaries.
  - `pyscriptc config --json`: serializes the resolved project configuration and import lane mappings.
  - `pyscriptc coverage --json`: serializes per-file and aggregate counts of unsupported constructs, reflection mechanisms, and porting hints.
  - `pyscriptc imports --json`: serializes import classifications, reflection mechanisms, and libpython linkage requirements.
  - Machine-readable outputs carry a top-level `schemaVersion` integer field when versioned schemas are introduced.

### Tier 3: Internal surfaces

Tier 3 covers internal compiler implementation modules, code generators, and deprecated bridge mechanisms. These surfaces carry no stability promise, can be refactored or deleted across any commit, and should not be imported or relied upon by external tools.

- **Compiler passes**: all modules under `src/pyscriptc/lower/` (AST visitors, desugaring passes, type inference routines).
- **LLVM code generation internals**: all modules under `src/pyscriptc/emit/llvm/` (instruction builders, ABI layout helpers, block emitters).
- **Runtime shims and private C helpers**: unexported structures and static helpers within `src/pyscriptc/runtime/`.
- **Mypy plugin and bridge**: the mypy plugin (`src/pyscriptc/check.py:plugin`), the mypy facts bridge (`src/pyscriptc/factsbridge.py:MypyBridge`), and the `--checker mypy` CLI lane. The default compiler preflight is `pyrefly`, bundled in release binaries. The release binary compiles with `--nofollow-import-to=mypy` and omits mypy entirely. The mypy integration is deprecated, retained solely for cross-validation (`benchmarks/factsdiff.py`), and offers no API stability.

## Versioned axes and canonicity

The compiler tracks four distinct versioned axes:

| Axis | Indicator | Purpose |
| ---- | --------- | ------- |
| IR Schema | `irVersion` in JSON IR (`src/pyscriptc/ir.py`) | Identifies the textual JSON IR structure and required node fields |
| Runtime C ABI | `CURRENT_PS_ABI` (`src/pyscriptc/ffi_manifest.py`) | Identifies the struct layout and calling convention between emitted LLVM IR and `ps_rt.h` |
| FFI Manifest | `format` and `ps_abi` in `ffi.toml` | Guarantees external C extension manifests pair with the compiling runtime |
| Tool Output | `schemaVersion` in machine-readable JSON | Tracks schema evolutions in CLI JSON output reports |

### IR hashing and canonicity

`PS_ABI` and C runtime revisions do not affect IR canonicity. In accordance with the IR canonicity specification (`.agents/docs/spec/ir-canonicity.md`) and the canonical normalizer (`benchmarks/compat2/canonical.py`), canonical IR hashing evaluates only the validated semantic IR tree. Compiler environment details, host paths, and runtime ABI versions never enter the IR hash calculation.
