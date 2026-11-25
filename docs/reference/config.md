---
title: Config
description: "The pyscriptc config surface: pyscriptc.toml, pyproject.toml and PEP 723."
---

# Config

One config file feeds every kind of project: a single-file script, a multi-module tree, or a pyproject package.
Sources, nearest first:

1. `pyscriptc.toml` at or above the source, holding a `[pyscriptc]` table (or the keys at the file root).
2. `pyproject.toml` at or above the source, holding a `[tool.pyscriptc]` table. A pyproject without that table does not shadow anything; discovery keeps walking up.
3. The script's own PEP 723 inline metadata (`# /// script`), read for `requires-python` and `dependencies`.

Nearest directory wins between the two TOML sources.
Project config wins over PEP 723 for the keys it sets; PEP 723 is the only source of script dependency metadata.

## Keys

```toml
[tool.pyscriptc]
entry = "main"        # entry point used by project mode
src = "src"           # module root(s), project mode
checker = "pyrefly"   # preflight type checker
driver = "clang"      # C toolchain driver
target = ""           # zig cc cross-compilation triple
fail-on = "any"       # exit rule for pyscriptc coverage
stub-path = "stubs"   # extra stub roots, relative to this file
ffi = "ffi.toml"      # extern manifests, relative to this file

[tool.pyscriptc.imports]
orjson = "replace"    # curated native replacement
sqlite3 = "extern"    # C ABI via an ffi.toml manifest
numpy = "island"      # embedded CPython, marks the binary as libpython-linked
rich = "refuse"       # always a compile error naming the lane
"*" = "compile"       # default

[tool.pyscriptc.island]
embed = "auto"        # host | bundle | auto: where the embedded CPython's libpython comes from
stdlib = "safe"       # safe | minimal: stdlib trim of a bundle
```

The accepted keys, their types and their allowed values:

<!-- gen:keys -->
| Key | Type | Values | What it does |
| --- | ---- | ------ | ----------- |
| `entry` | `string` |  | entry point function or module stem, used by project mode |
| `src` | `string` or `list` |  | module root, used by project mode |
| `checker` | `string` | pyrefly or mypy or pyright | type checker run before compiling |
| `driver` | `string` | clang or zig | C toolchain driver |
| `target` | `string` |  | cross-compilation triple for `zig cc` |
| `fail-on` | `string` | blockers or any | exit code rule for `pyscriptc coverage` |
| `stub-path` | `string` or `list` |  | extra stub roots, relative to this file |
| `ffi` | `string` or `list` |  | extern manifests, relative to this file |
| `imports` | `table` |  | import lane per top-level module name |
| `typing` | `table` |  | typing strictness levels: `mode`, and the per-level glob lists |
| `fallback` | `table` or `string` or `boolean` |  | fallback mode settings: `definitions`, or the tier `cpython` |
| `island` | `table` |  | embedded CPython settings: `embed`, `stdlib`, `engine`, `allow_host_libpython` |

<!-- gen:lanes -->
| Lane | What it does |
| ---- | ------------ |
| `compile` | compile from source, and stop with a lane hint when it fails |
| `replace` | a curated native replacement covers this import |
| `extern` | bound through the C ABI, with an `ffi.toml` manifest |
| `island` | run through embedded CPython; the binary needs libpython |
| `refuse` | refuse the compile with a message naming the lane |

Without a matching entry the lane is `compile`.
<!-- /gen:lanes -->
<!-- /gen:keys -->

Unknown keys, wrong value types and unknown lanes are `PS3102` errors naming the offending key (`pyscriptc explain PS3102`).

## Import lanes

`imports` maps a top-level module name to a lane.
An exact name wins over `*`; without either, the lane is `compile`, so the zero-config single-file product is unchanged.

<!-- gen:lanes -->
<!-- /gen:lanes -->

The `refuse` lane is enforced while compiling: an import routed to `refuse` stops the compile with `PS2002`, naming the lane and the policy site.
The other lanes are recorded in config and reported by `pyscriptc config`.
The `island` lane is what `--fallback=cpython` (alias `--island`) acts on; see [Runtime pack](runtime-pack.md) for what a libpython-linked binary carries.

## Typing levels

`typing` sets how much of the typing quality report a project sees.
It is a quality lane: a level never changes what compiles, what lowers or the exit code of a compile.

```toml
[tool.pyscriptc.typing]
mode = "standard"        # off | basic | standard | strict: the project default
strict = ["src/core/**"] # glob list per level, relative to this file
basic = ["src/legacy/**"]
```

| Level | What the report shows |
| ----- | --------------------- |
| `off` | no typing findings |
| `basic` | missing annotations (the `PS1001` family) as warnings |
| `standard` | missing annotations as warnings, plus the coverage numbers |
| `strict` | missing annotations and the construct forms (`PS1103`, `PS1104`, `PS1105`, `PS1106`) as errors |

`mode` is the project default; a project without the table gets `standard`.
Each level also accepts a list of glob patterns, relative to the config file's directory, and the longest matching pattern wins.
The level of a file is the strictest of the pattern that matches it and the per-file pragma `# pyscriptc: typed=<level>`, which may only raise the level, never lower it.

The findings surface in `pyscriptc infer --report` and `pyscriptc assess`; see [The Python subset](../guide/python-subset.md) for the PEP mapping of the levels.

## PEP 723

A script may open with:

```python
# /// script
# requires-python = ">=3.14"
# dependencies = ["orjson>=13"]
# ///
```

The block is parsed as TOML.
More than one `# /// script` block is an error.
`requires-python` and `dependencies` are validated and reported by `pyscriptc config`; resolving the dependencies is the separate `pyscriptc deps` command.

Each dependency is also mapped to a lane, shown by `pyscriptc config` as `dependency-lanes` in its JSON.
The `[imports]` table wins when it names the distribution or its import name; otherwise the offline import classifier decides.

## Project mode

A pyproject with a `[project.scripts]` table builds one binary per console script:

```sh
pyscriptc build [DIR]              # every script
pyscriptc build DIR --script NAME  # one script
```

The binary is written to `<project>/build/<name>` (or `--out-dir`).
The module path resolves against the project's module roots: `src`, default `src`, falling back to the project root when the directory does not exist (a flat layout).
`pyscriptc config DIR --json` reports the scripts.

## CLI interaction

`pyscriptc compile` fills unset `--checker`, `--driver`, `--target` and `--fail-on` from config; an explicit flag always wins, and a note on stderr names the config origin.
`pyscriptc coverage` reads `fail-on` the same way.
Run `pyscriptc config SOURCE` to see the resolved merge for any source.
