# Island engine descriptors (schema v1)

Issue #666. An island engine descriptor is a TOML document describing a
pluggable engine behind the boxed island tier. The built-in engine is the
embedded CPython the island tier already links (`--island-engine cpython`,
always resolvable); issue #668 targets PyPy as the first external engine.
In this slice the selection is inert: `--island-engine` resolves and
validates the descriptor and records it in the debug summary — no lowering
or link behavior changes.

## Format

```toml
schema_version = 1

[engine]
name = "pypy"           # required, non-empty string
version = "7.3.16"      # required, non-empty string
kind = "interpreter"    # required, non-empty free string

capabilities = ["fallback", "reflection"]   # optional, open list of strings

[abi]                   # optional table of string -> string expectations
libpython = "3.14"
stable_abi = "no"
```

- `schema_version`: integer. A descriptor with a **newer major version**
  than the compiler supports is rejected with a clear diagnostic; same or
  older versions load (v1 is the first, so today only `1` loads).
- `capabilities`: an **open string list**. There is no closed vocabulary
  yet — engines and the compiler negotiate semantics in later embed
  issues. Unknown capability names are kept, never rejected.
- `[abi]`: string-to-string expectations of the runtime surface the engine
  provides (for example `libpython`, `stable_abi`). Unknown entries are
  kept for forward compatibility.
- Unknown top-level keys and tables are kept on the loaded descriptor, so
  an older compiler can still load a newer descriptor.

## Loader

`src/pyscriptc/island_engine.py`:

- `island_engine.resolve(name_or_path)` — a path (contains a separator or
  ends in `.toml`) is loaded directly; a bare name is looked up as
  `<name>.toml` in the `os.pathsep`-separated `PYSC_ISLAND_ENGINES` search
  dirs; `cpython` resolves to the built-in descriptor.
- Malformed descriptors raise `island_engine.EngineError` with a
  one-line diagnostic (surfaced by the CLI as an `IRError`, no traceback):
  invalid TOML, missing/wrong-typed required fields, a newer
  `schema_version`.
- `island_engine.builtin_descriptor()` — the built-in embedded-CPython
  engine (`name=cpython`, `kind=interpreter`, `capabilities=["fallback"]`).

## CLI and config

- `pyscriptc compile --island-engine <name-or-path>`; also read from the
  `engine` key of `[tool.pyscriptc.island]` when the flag is unset
  (explicit flag wins).
- The resolved descriptor appears in the debug output
  (`--debug=stages` prints an `island-engine:` line) and as the
  `islandEngine` key of `--debug-dir`'s `summary.json`.
- The run-cache key includes the selection, so a future engine that
  changes the linked output cannot collide with a cached default build.

## Capability manifest (schema v1, issue #669)

A descriptor may name an engine capability manifest: a JSON document the
`probe` subcommand of `scripts/engine_manifest.py` produces *inside* the
engine being described (identity, stdlib coverage, language features,
platform). The descriptor's `[engine] manifest` key holds the path,
relative to the descriptor file:

```toml
[engine]
name = "pypy"
version = "7.3.16"
kind = "interpreter"
manifest = "pypy-manifest.json"
```

The manifest shape (validated by `pyscriptc/engine_manifest.py`, class
`EngineManifest`):

```json
{
  "schema": 1,
  "engine": {"name": "pypy", "version": "7.3.16", "language_level": "3.11",
              "platform": "linux-x86_64", "free_threaded": false},
  "stdlib": {"available": ["json"], "missing": ["annotationlib"]},
  "features": {"walrus_pep572": true, "tstring_pep750": false}
}
```

- `schema`: integer; a newer major version than the compiler supports is
  rejected with a one-line diagnostic.
- `stdlib.available` / `stdlib.missing`: disjoint lists of stdlib module
  names the engine resolves or lacks.
- `features`: object of booleans, one entry per probed language feature.
- Unknown top-level keys are kept (`extra`) for forward compatibility.

### Consumers

- `pyscriptc imports --island-engine <descriptor>` (or the
  `[tool.pyscriptc.island] engine` key): when the engine carries a
  manifest, the JSON report gains an `engine` section and each import row
  gains `engine.stdlib` (`available` / `missing` / `unknown`; third-party
  imports are always `unknown` — existence in the manifest is not
  loadability). The text report appends one `engine:` line.
- `pyscriptc assess` with the same selection: the report gains an
  `engine` capability section (`stdlibStatus` per import root) and the
  text report appends one `engine:` line.
- Without an engine (or with the built-in `cpython`, which has no
  descriptor file and no manifest) both commands behave exactly as
  before; the surface is additive only. A manifest that fails to parse
  warns on stderr and falls back to the native-only report.

## Boundary: what pyscriptc does not own

The engine contract splits responsibility at the descriptor:

- **The engine owns** its descriptor file, its capability manifest
  (produced inside the engine — `scripts/engine_manifest.py` is the
  reference probe), its headers, and the library or binary the compiled
  island links against. pyscriptc never installs, patches or relocates
  an external engine.
- **pyscriptc owns** descriptor schema v1 validation
  (`src/pyscriptc/island_engine.py`), manifest schema v1 parsing and the
  `imports`/`assess` consumers (`src/pyscriptc/engine_manifest.py`), the
  engine seam in `src/pyscriptc/runtime/ps_island.c`
  (`PS_ENGINE_START`, with `PS_ENGINE_PYPY` selecting the PyPy init
  order), and the `-DPS_ENGINE_*` define emission in the LLVM driver.
- **Manifest truth is the engine's claim.** pyscriptc parses and
  reports the manifest; it does not verify at compile time that the
  engine really resolves a listed stdlib module. Consumers degrade to
  `unknown`/native-only on a missing or broken manifest rather than
  trusting it blindly.

### Unsupported engines

- **GraalPy**: no C embedding path — `ps_island.c` hosts the runtime
  through the C API, which GraalPy does not provide.
- **MicroPython**: does not implement the CPython C API.
- **`rpc` transport engines**: out of scope, tracked in #651.

The descriptor loader does not reject these names (the schema is open
by design); what is missing is a `PS_ENGINE_*` seam, so selecting them
changes nothing in the emitted binary.

### PyPy seam status

Under `PS_ENGINE_PYPY`, `PS_ENGINE_START()` calls
`rpython_startup_code()`, `pypy_setup_home(home, 1)` and
`pypy_init_threads()` instead of `Py_InitializeEx(0)`. The init order is
documented in the source as **assumed, not verified** — no PyPy SDK is
available in CI, so the first real PyPy pack (#668) is the verification
gate. Exception arguments are read with
`PyObject_GetAttrString(val, "args")`, which is portable across both
engines' C APIs. The CPython path is unchanged and fully tested.

## Open questions (deferred to later embed issues)

- Closed capability vocabulary and what each capability obligates the
  compiler to do (#668+).
- Whether `[abi]` values should gain types (versions, booleans) once a
  second engine exists.
- Descriptor discovery beyond `PYSC_ISLAND_ENGINES` (a per-project
  descriptor directory, a registry).
