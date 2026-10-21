# Cross-checking with mypy and pyright

pyscriptc's type oracle is pyrefly.
The preflight (`pyscriptc compile`) runs `pyrefly check`, and the tier-2 facts seam reads `pyrefly tsp` (`PyreflyTspFactsAdapter`, default since v0.3.0) with the Pysa-report adapter (`facts_provider("pyrefly")`) as fallback.
Since v0.3.1 mypy and pyright are no longer dependencies and no longer have facts adapters.
They remain useful as independent second opinions when you suspect a pyrefly false positive or false negative.

## Install

Both are optional extras.

```sh
uv pip install 'pyscriptc[oracle-mypy]'     # mypy
uv pip install 'pyscriptc[oracle-pyright]'  # pyright (PyPI wrapper, downloads node on first run)
```

Or use whichever copy you already have on PATH.
Nothing else in pyscriptc imports them.

## Deprecated built-in lanes

`pyscriptc compile --checker mypy FILE` and `--checker pyright FILE` still work and print a DeprecationWarning.
Without the extra they stop with `install the extra: pip install 'pyscriptc[oracle-mypy]'`.
The mypy lane loads a small plugin (`pyscriptc.check`) that realigns `range`, `frozenset`, `struct.unpack` and `functools.partial` with the lowered contract.
The pyright lane points `stubPath` at the vendored `_pyright_stubs/`.
Set `PYRIGHT_BIN` to pick a specific pyright binary.

## Running them yourself

Plain invocations answer "is this file type-correct?" without pyscriptc in the loop.

```sh
mypy --follow-imports=silent path/to/file.py
pyright --pythonversion 3.13 path/to/file.py
```

Expect a few known deltas from the subset contract, because stock mypy and pyright do not know about the lowering realignments:

- `range(...)` assigned to `list[int]` is an error on stock checkers (the subset materializes a list).
- `frozenset(...)` passed to a `set[T]` parameter is an error on stock checkers (the subset treats `frozenset[T]` as a `set[T]` subclass).
- `struct.unpack` is `tuple[Any, ...]` on stock checkers, a homogeneous `list` in the subset.
- Bare `x: int` declarations are flagged unbound by some checkers, while the subset zero-initializes them.

To get the subset behavior in mypy, enable the plugin: put `plugins = pyscriptc.check` in a `mypy.ini` and run `mypy --config-file mypy.ini path/to/file.py`.
For pyright, write a `pyrightconfig.json` with `"stubPath"` set to the `pyscriptc/_pyright_stubs` directory inside your install.

## Comparing against pyrefly's facts

`uv run python -m benchmarks.factsdiff [file]` prints, per oracle, how many expression positions were typed and where resolved kinds disagree.
`uv run python -m benchmarks.factsdiff --corpus 14` writes the corpus report to `benchmarks/factsdiff-report.json` (gitignored).
The mypy column appears only when mypy is importable, and it compares kinds only (typed positions and function names), not full signatures.
Pyrefly lanes (`pyrefly`, `pyrefly-tsp`) always run.

## When they disagree

Treat pyrefly as authoritative for the compile, since it is what the preflight and the facts seam use.
If mypy or pyright reports an error that pyrefly accepts, check whether it is one of the known deltas above before filing anything.
If it is not, a reduced repro against pyrefly is the useful report.
