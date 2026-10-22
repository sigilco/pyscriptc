---
title: Config
description: The pyscriptc config surface: pyscriptc.toml, pyproject.toml and PEP 723.
---

# Config

One config surface feeds every rung of the adoption ladder: a single-file script, a multi-module tree, or a pyproject package. Sources, nearest first:

1. `pyscriptc.toml` at or above the source, holding a `[pyscriptc]` table (or the keys at the file root).
2. `pyproject.toml` at or above the source, holding a `[tool.pyscriptc]` table. A pyproject without that table does not shadow anything; discovery keeps walking up.
3. The script's own PEP 723 inline metadata (`# /// script`), read for `requires-python` and `dependencies`.

Nearest directory wins between the two TOML sources. Project config wins over PEP 723 for the keys it sets; PEP 723 is the only source of script dependency metadata.

## Keys

```toml
[tool.pyscriptc]
entry = "main"        # entrypoint function or module stem (used by project mode, P1/P2)
src = "src"           # module root(s), string or list (project mode)
checker = "pyrefly"   # pyrefly | mypy | pyright, default preflight
driver = "clang"      # clang | zig, C toolchain driver
target = ""           # zig cc cross-compilation triple
fail-on = "any"       # blockers | any, default exit code for `pyscriptc coverage`
stub-path = "stubs"   # extra stub roots, string or list, relative to this file
ffi = "ffi.toml"      # FFI manifests, string or list, relative to this file

[tool.pyscriptc.imports]
orjson = "replace"    # curated native replacement pack
sqlite3 = "extern"    # C ABI lane via an ffi.toml manifest
numpy = "island"      # opt-in embedded CPython, marks the binary as libpython-linked
rich = "refuse"       # always a compile error naming the lane
"*" = "compile"       # default: compile from source or fence
```

Unknown keys, wrong value types and unknown lanes are `PS3102` errors naming the offending key (`pyscriptc explain PS3102`).

## Import lanes

`imports` maps a top-level module name to a lane. An exact name wins over `*`; without either, the lane is `compile`, so the zero-config single-file product is unchanged. Today the table is resolved and printed (`pyscriptc config`, compile notes); the compile fence path consumes it in P7. Lanes:

| Lane | Meaning |
|---|---|
| `compile` | default: compile from source, fence with a lane hint on failure |
| `replace` | a curated native replacement pack covers this import |
| `extern` | bound through the C ABI lane ([FFI manifest](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/ffi/ffi-manifest.md)) |
| `island` | opt-in embedded CPython; the binary requires libpython |
| `refuse` | refuse the compile with a message naming the lane |

## PEP 723

A script may open with:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["orjson>=13"]
# ///
```

The block is parsed as TOML. More than one `# /// script` block is an error. `requires-python` and `dependencies` are validated and reported by `pyscriptc config`; dependency resolution itself is the separate `deps` work (PR #138).

Each dependency is also mapped to an import lane, shown by `pyscriptc config` and as `dependency-lanes` in its JSON: `native`, `pack`, `extern`, `island` or `refused`. The `[imports]` table wins when it names the distribution or its import name (`compile` is `native`, `replace` is `pack`, `refuse` is `refused`); otherwise the offline import classifier decides (known native-backed distributions go to `pack` when a curated pack exists, else `extern`; everything else is `native`).

`stub-path` and `ffi` are resolved against the config file directory and exposed on the config (`stub_paths`, `ffi_manifests`); the compile path consumes them in later work.

## CLI

- `pyscriptc config SOURCE [--json]`: show the resolved config for a source file or directory.
- `pyscriptc compile SOURCE`: fills unset `--checker`, `--driver`, `--target` and `--fail-on` from config; an explicit flag always wins. Notes on stderr name the config origin.
- `pyscriptc coverage SOURCE`: `--fail-on` defaults from config when the flag is absent.
