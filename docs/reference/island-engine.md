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

## Open questions (deferred to later embed issues)

- Closed capability vocabulary and what each capability obligates the
  compiler to do (#668+).
- Whether `[abi]` values should gain types (versions, booleans) once a
  second engine exists.
- Descriptor discovery beyond `PYSC_ISLAND_ENGINES` (a per-project
  descriptor directory, a registry).
