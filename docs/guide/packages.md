---
title: Packages and apps
description: How PyPI dependencies run in pyscriptc: the compat ladder, island floor, and native ceiling.
---

pyscriptc compiles the typed subset of Python 3.14.
It does not chase byte-for-byte CPython parity across arbitrary untyped Python, and the compiler provides no shims or dynamic runtime emulation for untyped code.
Instead, PyPI dependencies and third-party libraries follow the capability ladder: the island is the floor and native compilation is the ceiling.

## The compat ladder

Every PyPI package gets a defined execution mode at every maturity stage:

- **Floor (always defined)**: the import runs in the CPython island, an in-process embedded interpreter with value-only marshalling (scalars, strings, bytes, and lists across the boundary) that requires `libpython`. Compatibility never answers "impossible"; it answers "island".
- **Ceiling (earned per package)**: when a package fits the typed subset, it compiles natively into standalone machine code with no interpreter runtime.

`requests.get(url)` from a typed binary is the flagship of the floor: typed user code compiles native, while untyped PyPI dependencies run on the island floor.
The ladder is made user-visible through `pyscriptc imports FILE`, which classifies dependencies as `native`, `native: fences (PS20xx)`, or `island (requires libpython)`.

## Two unidirectional seams

Contact between compiled code and the island exists only as two one-way seams:

1. **Compiled to island**: in-process embedded CPython. The user's typed code is native; an untyped import marshals values across into the island and back with subprocess-shaped semantics and in-process execution speed.
2. **Island to compiled**: CPython consuming compiled native code via `pyscriptc --emit binding`, which generates a loader `.py` and shared library via `ctypes`.

There is no compiled to island to compiled call chain.
Values cross a seam exactly once per direction, ownership transfers at the crossing, and no object identity survives it (island handles are opaque, and native classes never appear as `PyObject*`).
This one-way discipline keeps the boxed ledger honest and avoids the complexity of tracing garbage collection.

## Dropped pack format

The pack format track (`pack.toml`, binary wheel pack distribution, and precompiled library archives) was permanently dropped per the 2026-10-25 typed-subset positioning decision.
Packs recreated the curated-fork burden and required codebase changes per package.
Both jobs are now handled by cleaner architectures: PyPI consumption goes through compile-from-source (typed) or the island floor (untyped), and CPython consumption goes through `--emit binding`.

As a narrow exception, already-shipped curated distributions (`pyscriptc-orjson`, `pyscriptc-jwt`) continue as standalone packages under explicit alias imports.
New curated distributions require high ROI plus codebase-level compatibility decided case by case.

## Dynamic reflection and introspection

Typed-but-reflective frameworks (such as FastAPI or Pydantic that rely on `inspect.signature`, runtime `__annotations__` reads, or import-time schema building) run on the island floor where genuine CPython reflection works.
The compiler never invents fake schema descriptors or partial runtime shims to pretend to compile reflection natively.
When a compile fences on reflection, the diagnostic names the culprit and points to the island lane or the nearest static typed idiom (such as PEP 692 `Unpack[TypedDict]`).

## Where status lives

Per-construct, per-module, and per-library status is tracked on the [compat site]({{PYSC_COMPAT_URL}}), which is generated from the compiler and cannot drift from it.
The positioning decision and architecture plans are in the repository: `.agents/plans/2026-10-25-typed-subset-positioning.md`.
