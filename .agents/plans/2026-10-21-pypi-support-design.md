# PyPI support: resolve, fetch, classify, cache

Date: 2026-10-21. Status: design plus a partial implementation (draft PR, `src/pyscriptc/deps.py`, `pyscriptc deps`). Scope comes from `2026-10-21-adoption-compat-ffi-strategy.md` (rung R4, issue P9, lane vocabulary) and item 3 of `2026-10-21-queued-dispatch.md`. Target milestone: resolver and cache in v0.6.0 public (P9 may slip to v0.7.0 without blocking launch), compile-from-source wiring when the V2 `lower/` slot opens.

## Goal

A program can say "I depend on these PyPI packages" in the place Python users already write it, and `pyscriptc` turns that into a reproducible, hash-pinned set of distributions, each assigned to one lane: compile from source, a replacement pack, the `extern` C library lane, the opt-in island, or a refusal with a hint. No CPython is needed at runtime for any binary that stays on the native and pack lanes.

## Inputs (config surface)

Dependencies come from exactly two places, matching the strategy plan's single config surface. A single-file script uses a PEP 723 `# /// script` block (`requires-python`, `dependencies`). A project uses `[project].dependencies` and `[project].requires-python` in `pyproject.toml`. Lane overrides live in the per-import policy table (`[tool.pyscriptc.imports]` in `pyproject.toml`, or `pyscriptc.toml`, or a `[tool.pyscriptc]` table inside the PEP 723 block), owned by the config PR (P3). The resolver never invents a second dependency list; `--with` style ad hoc additions (V4 stage 3) append to the same requirement list and change the lock digest like any other edit.

The policy table maps import names, not distribution names. The bridge is each wheel's `top_level.txt` or, when absent, the top-level entries of its RECORD. The resolver records that mapping per distribution so `coverage` can show "import `yaml` comes from `pyyaml`, lane native".

## Resolution with uv

`uv` is the only resolver. We do not reimplement PEP 440, markers or index protocols. The resolver calls `uv pip compile requirements.in --format pylock.toml --universal --python-version <host minor> --no-build`. `--universal` makes the lock portable across platforms: packages behind environment markers stay in the lock with their `marker`, so the lane table is a superset of what any one target needs. `--python-version` is the CPython minor that pyscriptc is mirroring (the host interpreter, same as `ir.cpython_version()`), because compiled semantics follow that version. `--no-build` means uv never runs a build backend to obtain metadata; an sdist-only dependency fails resolution with uv's message and our hint, instead of silently executing its `setup.py`.

When the user already has a uv lock (`uv.lock` for a project, `<script>.py.lock` for a script), it wins: `uv export --format pylock.toml --frozen --no-dev --no-emit-project` converts it and we never write a second lock. That keeps one source of truth for users who already run `uv lock`.

## Lock handling

The internal lock format is PEP 751 (`pylock.toml`), because uv emits it with per-file URLs and sha256 hashes, it is a standard, and the same parser handles both our resolution and a `uv export`. Our own lock is written beside the input: `pylock.<script stem>.toml` for a script, `pylock.pyscriptc.toml` for a project. It carries `[tool.pyscriptc] input-digest` (sha256 over the sorted requirement strings, `requires-python` and the CPython minor) and `python`. A lock whose digest matches is reused without calling uv. `--frozen` refuses to resolve and fails on a missing or stale lock (CI mode). `--upgrade` re-resolves to the newest allowed versions. Users commit the lock.

## Fetching

Only wheels are fetched in the partial slice. For each locked package we pick a `none-any` wheel whose Python tag the host can use (`py3`, `py2.py3`, `pyXY` or `cpXY` with XY not above the host minor). The download is plain `urllib` over https only, because uv has no "download a wheel into this path" command, and each blob is checked against the lock's sha256 before it is stored. `--offline` passes `--offline` to uv and allows cache hits only. Wheels for the native lane are not downloaded in the partial slice: their tags already decide the lane.

## Classification

Lanes are decided per distribution, cheapest evidence first. Tags: a usable `none-any` wheel makes the distribution a pure candidate; only ABI or platform tagged wheels (`cp314-cp314-manylinux...`, `abi3`) mean native; no wheel at all means sdist. Contents: a pure candidate is downgraded to native if the wheel ships `.so`, `.pyd`, `.dylib` or `.dll` files or its `WHEEL` says `Root-Is-Purelib: false`. A pure wheel whose code imports a native package is still pure; that dependency is its own locked distribution and gets its own lane, and the import-level fence happens in the compiler.

Mapping lanes to the strategy plan's ladder:

| Distribution lane | Default action | Policy override |
|---|---|---|
| pure | compile from source (L0 native) | `absent` to compile the `ImportError` branch instead |
| native, pack exists | refuse with "add `pyscriptc-<lib>`" | `pack` (L1) |
| native, C library behind it | refuse with an `extern` hint | `extern` with an `ffi.toml` (L2) |
| native, anything | refuse | `island` (L3, labelled "requires libpython", counted by `boxed_cost.py --reach`) |
| sdist | refuse: building runs arbitrary code | `build = "sandboxed"` (full scope, below) |

The command exits 0 when every distribution is on the pure lane and 3 otherwise, mirroring `compile --print diagnostics`. Errors use code `PS3101`.

## Cache

Everything lives under `~/.cache/pyscriptc/deps/` (override `PYSC_CACHE_DIR` or `--cache-dir`), next to the existing `log/` directory. `blobs/sha256/<ab>/<sha256>.whl` stores verified wheels by content hash. `src/<sha256>/` holds the unpacked import root of a pure wheel, written to a temp directory and renamed into place with a `.complete` marker, so a crash never leaves a half tree. Purelib and platlib `.data` content lands at the root; `scripts`, `headers` and `data` are skipped. Keys are wheel hashes, not names, so two projects that lock the same file share it and a re-published file with the same name can never be confused. Later stages add `ir/<sha256>-<compiler version>/` for lowered IR of each dependency and reuse the runtime object cache (#132) for objects. Eviction is a future `pyscriptc cache prune` keyed on access time.

## Reproducibility

The same lock plus the same pyscriptc version gives the same source trees, byte for byte, because every file is addressed by its locked hash and unpacking is deterministic. Resolution itself is reproducible through the committed lock; we do not try to make two fresh resolutions agree (PyPI moves). If teams want time-pinned fresh resolutions, `--exclude-newer` passes through to uv later. The CPython minor is part of the digest, so a host upgrade from 3.13 to 3.14 marks the lock stale instead of compiling 3.14 semantics against wheels chosen for 3.13. Compiled binaries embed the lock digest in their metadata (V2 integration) so a binary can be traced to its exact dependency set.

## Security

Hash pinning is mandatory: a locked wheel without a sha256 is an error, a download that does not match is discarded, and every file in a wheel must match its RECORD hash (files missing from RECORD are rejected). Archives with absolute paths, `..` segments, backslashes, drive letters or symlinks are rejected before anything is written. Only https URLs are fetched. No code from a dependency runs at resolve time, fetch time or compile time: `--no-build` keeps uv away from build backends, and the compiler only parses the source. Sdists are refused by default; the full scope builds them only in a sandbox and only on opt-in. Index configuration (private indexes, auth) stays uv's (`UV_INDEX_URL`, `uv.toml`), so credentials never pass through pyscriptc.

## Partial scope (this PR) versus full scope

Partial, shipped as a draft PR: PEP 723 and `[project]` parsing; `pyscriptc deps SOURCE [--json] [--frozen] [--upgrade] [--offline] [--no-download] [--cache-dir DIR]`; uv-backed universal resolution into a hash-pinned PEP 751 lock, or export from an existing uv lock; pure, native and sdist lanes from tags and contents; verified, content-addressed wheel cache and unpacked source roots; a JSON report with `sourceRoots`. It does not touch `lower/`, `package.py` or `coverage.py`, and the CLI diff is the subcommand hook only.

Full scope, in later PRs: the compiler consumes `sourceRoots` as module roots and compiles reached functions of pure dependencies (V2 slot); the policy table chooses pack, extern, island or absent per import (needs P3); import-name to distribution mapping in `coverage` (P7); marker evaluation for the target triple (`--target` mapped to uv's `--python-platform`) so a cross-compile only fetches what that target needs; opt-in sandboxed sdist builds (`uv build --wheel` inside a network-less container or `sandbox-exec`), producing a wheel that then goes through the same classification; per-dependency IR cache; `pyscriptc run` (P5) calling the resolver before compiling; lock digest in binary metadata; R4 lane-share metric in `benchmarks/ladder/` (P6).

## Integration points (not edited here)

- `src/pyscriptc/package.py`: accept extra module roots (each `sourceRoots` entry) for absolute imports, after P1 and P2 land.
- `src/pyscriptc/lower/`: resolve `import foo` against dependency roots and fence non-pure lanes with a lane hint; V2 owns the slot.
- `src/pyscriptc/coverage.py`: import lane table (P7) reading the deps report.
- `src/pyscriptc/diagnostics.py`: `PS3101` is registered in this PR (a test requires every emitted code to be).
- Config (P3): `[tool.pyscriptc.imports]` overrides lane decisions; the resolver reports, the compiler enforces.

## Open questions

1. Lock file name for projects: `pylock.pyscriptc.toml` beside `pyproject.toml` (chosen) versus a plain `pylock.toml` that other tools may also write.
2. Whether `pyscriptc compile` should resolve implicitly (like `uv run`) or require an explicit `pyscriptc deps` first. Recommendation: implicit when a lock is current, explicit otherwise.
3. Sandbox for sdist builds on macOS (no containers by default): `sandbox-exec` profile versus requiring Docker. Recommendation: defer; most pure-Python packages publish wheels.
