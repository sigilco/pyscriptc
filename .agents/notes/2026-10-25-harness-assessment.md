# Harness assessment: can pyscriptc compile `harness`? (2026-10-25)

Target: `harness`, the owner's session-management CLI (12 modules, 1434 lines, entry point `harness.cli:main`), copied to `/tmp/harnessassess/src` so the original was never touched. Measured on this branch at master `49608e0e`, CPython 3.14.8 host, libpython 3.14.9 linked by the island, arm64 macOS. Tracking issue: #592.

Short answer: not as a package today. As a package, `pyscriptc run --program --fallback=cpython` exits 3 and the island plan is empty. As a single-file flattening with five mechanical edits it compiles and matches the installed tool on the read-only subcommands, except that every `sys.exit("message")` path is silently turned into exit 0. Startup is 3 to 14 percent faster than CPython; `ls` is dominated by tmux subprocess time and sees no gain.

## Step 1: `pyscriptc assess`

Commands: `uv run pyscriptc assess /tmp/harnessassess/src/src` with `--all` and with `--json` (outputs kept in `/tmp/harnessassess`). Pointing at `src/src` names the modules `src.harness.*`, so 9 of the "untyped import" fences are export cascades from `from .tools import TOOLS` and similar; pointing at `src/src/harness` gives the same shares (22 imports instead of 19).

| Metric | Value |
|---|---|
| Files | 12: 1 lowers fully (empty `__init__.py`), 5 partly, 6 refused |
| Definitions | 83: 14 native (16.9%), 0 island, 69 blocked (83.1%) |
| Import-level island share | 10.5% (2 of 19 imports: `collections.abc`, `__future__`) |
| Fences | 254 total, 242 root, 177 are PS2002, of which 115 are name-resolution cascades |
| Plane map under `--fallback=cpython` | module top level island and the blocked definitions mapped to island (the report claims it, but the compile still exits 3 and `island` stays 0 in the diagnostics, see step 2) |

| File | Status | Native / fenced of definitions | Fences |
|---|---|---|---|
| `cli.py` | refused | 0 / 2 | 39 |
| `cmds.py` | partly | 2 / 12 | 79 |
| `config.py` | partly | 1 / 3 | 3 |
| `freebuff_settings.py` | refused | 0 / 3 | 10 |
| `gate.py` | refused | 0 / 1 | 1 |
| `remote_cmds.py` | partly | 2 / 19 | 59 |
| `runner.py` | refused | 0 / 2 | 2 |
| `snapshot.py` | refused | 0 / 9 | 22 |
| `tmuxio.py` | refused | 0 / 6 | 9 |
| `tools.py` | partly | 1 / 6 | 8 |
| `transport.py` | partly | 8 / 20 | 22 |

Fences by root-cause family (`--print diagnostics`, `byFamily`): name-resolution 115 (cascades), unlowered-call 31, local-inference 19, construct-other 16, field-lookup 15, type-mismatch 10, export-cascade 9 (derived), generic-alias 9, unlowered-type-name 7, call-arity 6, import-table 5, class-cascade 3 (derived), module-assign 2, operator 2, and one each for dynamic-annotation, keyword-args, stdlib:os, typing-value and union-types. By code: PS2002 177, PS2001 21, PS1001 19, PS1103 9, PS1003 10, PS1106 7, PS2003 7, PS2004 2, PS1101 1, PS1104 1.

Imports ladder. Third-party runtime dependencies: none (`pyproject.toml` has an empty runtime dependency list; `pytest`, `ruff` and `pyrefly` are dev only). Stdlib: `argparse`, `base64`, `dataclasses`, `json`, `os`, `pathlib`, `re`, `tempfile`, `time`, `tomllib` lower natively; `typing`, `sys`, `subprocess`, `shutil`, `pathlib` and `os` import natively but their use fences; `shlex` and `concurrent.futures` are not in the closed import table (policy line `island` routes them); `collections.abc` and `__future__` are island at import level.

## Step 2: running it

Package route. A `__main__.py` was added to a copy (`from harness.cli import main` plus `main()`; the relative form `from .cli import main` fences with PS2002 "relative import in __main__ has no parent package", although `python -m harness` accepts it). `pyscriptc run --program --fallback=cpython --report /tmp/harnessassess/prog/harness` exits 3: the module-level fences of the imported modules (`OUT_DIR = Path(os.environ.get(...))`, `CONFIG_PATH = Path(...).expanduser()`, `class Snapshot(TypedDict)`, 21 more) are not planned for the island, and `--print diagnostics` shows `island: 0`, `mechanisms: []` for all 12 files. A two-module repro with one fenced function in the imported module crashes the emitter with `KeyError: 'mp.helper.count'`. Filed as #641.

Single-file route. The environment needed is small because there are no third-party dependencies: the host CPython 3.14 (the binary links `libpython3.14.dylib` from the uv Python), and a `pyproject.toml` next to the source containing

```
[tool.pyscriptc.imports]
shlex = "island"
"concurrent.futures" = "island"

[tool.pyscriptc.fallback]
definitions = true
```

The modules were concatenated into one file in dependency order (relative imports stripped; the four remote verbs renamed `r_*` to avoid colliding with `cmds.launch`/`steer`). The file runs under CPython unchanged (`python3 flat.py ls` matches the installed tool). Then, in this order, what the compiler needed:

| Edit | Why | Tracked |
|---|---|---|
| none: compile `flat.py` | exit 3, `this class base is not lowered` x3 (`Tool`, `Snapshot`, `Row` are `TypedDict`) | #628 |
| `Verdict = Literal[...]` to `Verdict = str`, `CloseAction` to `list`, `class X(TypedDict)` to `X = dict` | module-level alias and TypedDict fences stay unplanned | #628 |
| `Transport` protocol and the two transport classes replaced by a stub `get_transport`, `remote_cmds` replaced by a stub `dispatch` | classes with fenced methods cannot route (`subprocess.run` keywords, `self.host` field) | #628, #503 |
| `ThreadPoolExecutor` loop in `rows_for` replaced by a list comprehension | lowerer crash `KeyError: '_ps_imod_1'` | #643 |

With those, `pyscriptc compile --fallback=cpython --emit bin` succeeds (binary 662 KB, links libpython). Comparison against the installed `harness` (uv tool) and `python3 flat4.py`, no network, no tmux changes (the `ls` subcommand only reads `/tmp/*.meta` and runs read-only `tmux` queries, no sessions were launched or steered):

| Invocation | Compiled exit / installed exit | stdout | stderr |
|---|---|---|---|
| `--help`, `ls --help`, `status --help` | 0 / 0 | identical | identical |
| `ls`, `ls --json`, `ls --all` | 0 / 0 | identical after masking the age column | identical |
| `launch` (no args), `launch maki m s`, `bogus` | 2 / 2 | identical | identical |
| `status nosuch`, `status nosuch --json` | 0 / 1 | identical (empty) | missing `no meta for nosuch` |
| `wait nosuch --timeout 1` | 0 / 1 | identical (empty) | missing `no meta for nosuch` |
| `steer nosuch hi`, `steer nosuch --close` | 0 / 1 | identical (empty) | missing `no session nosuch` |
| `r h`, `r h ls`, `status nosuch --host nohost` | 0 / 1 or 255 | n/a | stubbed slice, expected |

The one real divergence is a `SystemExit` with a `str` payload raised in an island function: exit 0 and no message instead of exit 1 and the message. Integer payloads cross correctly. Filed as #642. The fenced `ls` path through the island prints exactly what CPython prints, so the seam itself is sound for this program; `sys.argv` reached the island (issue #635 did not bite because the module top level routed to the island, not native).

## Step 3: blockers in order

"First blocker" means the first thing that stops the real package from running, then what stops it from going native.

| # | Blocker | Kind | Count in harness | Issue |
|---|---|---|---|---|
| 1 | Island plan not applied to imported modules in `--program` (exit 3, emitter `KeyError`) | structural | 11 of 12 files | #641 (new) |
| 2 | Classes: `TypedDict` bases x3, `Protocol`, transport classes with fenced methods, one `@dataclass` | structural | 3 + 3 classes | #628 |
| 3 | `SystemExit(str)` swallowed at the island seam | structural | every error path | #642 (new) |
| 4 | `-> NoReturn` and `sys.exit(str)` fence lowerable CLI definitions | mechanical | 7 + 6 sites | #645 (new) |
| 5 | argparse: `add_subparsers`, `Namespace` attribute reads (`a.cwd`), `set_defaults(f=fn)` plus `a.f(a)` | structural | 21 field-lookup fences, 1 + 1 | #565 covers the basics; no open issue for subparsers and Namespace fields |
| 6 | `subprocess.run(..., input=, stdout=, stderr=, capture_output=, text=)` keywords | mechanical | 7 PS2003 | #503 (nearest) |
| 7 | Local annotations: first assignment from an attribute or call (`s = a.session`, `argv = [...]`, `opts = X if y else Z`) | mechanical | 19 PS1001 | none needed, annotate |
| 8 | Generic aliases and unlowered type names: `frozenset[str]`, `CompletedProcess[str]`, `Snapshot`, `Row`, `Verdict`, `dict`, `Callable` | mechanical | 9 + 7 | #483, #481 |
| 9 | Module assignments: `Path(os.environ.get(...))`, `Path(...).expanduser()`, `LIB_FILES = ("x",)` 1-tuple, `TOOLS = {... lambdas ...}` | structural | 2 + 3 | #502 |
| 10 | Stdlib surface: `os.walk`, `Path.rglob`, `Path.replace`, `shlex`, `concurrent.futures`, `time.sleep` as value, `list.sort(key=)`, `shutil.copy2(Path, Path)`, any() over a generator | mechanical | about 25 | #503, #173 |
| 11 | Function-level relative imports (`from . import remote_cmds`, `from .freebuff_settings import pin_model`) | structural | 2 | none |
| 12 | `ThreadPoolExecutor` in a `with` crashes the lowerer in a larger module | structural | 1 | #643 (new, unminimized) |

Not hit: #626 (the only decorator is `@dataclass`; no callable decorator expressions) and #635 (argv). Neither blocks `harness`.

Native share if the top 5 blockers were removed. The causal chain in the diagnostics output lists, per family, the definitions that become unblocked if that family alone is fixed: construct-other 9, generic-alias 9, unlowered-call 6, type-mismatch 6, unlowered-type-name 5. The top five families therefore unblock at most 35 definitions, so native rises from 14 of 83 (16.9%) to at most 49 of 83 (59%). Counting the blockers above instead (1 to 5: program-mode islands, classes, SystemExit, NoReturn and sys.exit, argparse) the native gain is smaller, because #641, #642 and #628 mostly move definitions to a working island rather than to native: expect about 14 + 12 (transport classes) + 7 (NoReturn) + 12 (argparse and Namespace users in `cli.py` and `cmds.py`) = 45 of 83 (about 54%) as a generous upper bound, and 30 to 40 percent if the Namespace reads stay fenced. The island floor, not native, is what makes this program run: the plane map is 100 percent island with a correct result.

## Step 4: timing

12 samples per cell, interleaved, one process at a time, on a shared machine whose load was not controlled (it also hosts tmux sessions and other agents). Times in milliseconds, median (min to max). `py` is the flattened single file under `python3` (same code as the binary, including the sequential `ls`); `installed` is the uv tool `harness` (threaded `ls`, so not comparable for `ls`).

| Invocation | compiled binary | `python3 flat4.py` | installed `harness` |
|---|---|---|---|
| `--help` | 110.0 (82.6 to 170.8) | 118.0 (88.8 to 154.0) | 113.9 (80.6 to 179.5) |
| `status nosuch` | 85.5 (73.7 to 194.8) | 96.1 (77.9 to 184.1) | 99.0 (76.4 to 171.2) |
| `ls` (about 120 sessions) | 5094.6 (3535.6 to 7151.7) | 5221.6 (3399.4 to 6932.1) | 2420.4 (1521.9 to 3124.1) |

Reading: on the island floor the binary starts 3 to 14 percent faster than CPython on the same code (the gap is inside the sample range for `--help`), which is a small win, not a reason to dogfood by itself. `ls` spawns one tmux process per session and is bound by that; the compiled sequential version equals CPython's sequential version, and both lose to the installed tool's thread pool, which is exactly the construct that crashes the lowerer (#643). Dogfooding `harness` as a compiled binary is therefore not yet a win; it becomes one when #641 lets the package compile and the thread pool lowers, and even then the cost is the 662 KB binary linking libpython 3.14.9 and the SystemExit seam bug.

## Reproduction

Everything is in `/tmp/harnessassess` (not committed): `assess_all.txt`, `assess.json`, `diag.json`, `flat/flat.py` (concatenation), `flat/flat4.py` (the compiled source), `flat/pyproject.toml`, `mini/` (the minimal repros). The flattening script is the 20 line Python snippet described in step 2.
