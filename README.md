<h1 align="center">pyscriptc</h1>

<p align="center">Compile annotated Python to a native binary. No CPython at runtime.</p>

<p align="center">
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pyscriptc"></a>
<a href="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

A compiler that lowers a small, annotated subset of Python to a typed IR and then to LLVM. Parsing is CPython’s own `ast` module. [pyrefly](https://pyrefly.org/) is a preflight: its tree is not an input. Anything the subset does not prove is a fence, not a guess.

## Try it now

```bash
uv tool install pyscriptc          # or: pipx install pyscriptc / pip install pyscriptc
pyscriptc coverage your_script.py  # how much of YOUR file compiles, and what blocks the rest
```

pyscriptc compiles a small, annotated subset of Python, and a fence stops the whole file at the first unsupported construct. Most real scripts will not compile yet: on the 3.12 calibration corpus about 16 percent of surface probes compile. `coverage` needs no C toolchain, lists every fence with a hint, and ranks what to fix first. Run it on your own file before anything else.

## Three examples

Hello. A script with `main()` and a `__main__` guard runs as-is with `--run` (it needs the C toolchain from the next section):

```bash
pyscriptc compile examples/hello.py --run
```

`pyscriptc run FILE [-- args]` is the `uv run` shaped shortcut: it compiles once, caches the binary under `~/.cache/pyscriptc/bin` keyed on the source content, the compiler and the flags, then runs it. A second run of an unchanged script skips the compile; arguments after `--` reach `sys.argv`, and stdin, stdout and the exit code pass through. `--no-cache` (or `PYSC_NO_RUN_CACHE=1`) always recompiles.

```bash
pyscriptc run examples/hello.py
pyscriptc run script.py -- first second
```

A loop. A plain function needs `--call` (and `--args`, a Python list literal); a file that also has a `__main__` guard runs that guard when you pass neither. Both print `285`, the value CPython prints:

```bash
pyscriptc compile examples/sum_squares.py --run
pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]'
```

A fence. `str.encode("ascii")` is outside the subset, so the file stops with a named code and writes no LLVM:

```bash
pyscriptc coverage examples/fence_method.py
pyscriptc compile examples/fence_method.py --emit llvm   # exit 3
```

```
examples/fence_method.py:2:12: PS2003: str.encode lowers the 'utf-8' encoding only
      return name.encode("ascii")
```

`pyscriptc explain PS2003` describes any code. If `--run` finds nothing to run (no module-level script, no `__main__` body, no `--call`), it prints a note on stderr naming `--call` and exits 0.

## Install it

```bash
uv tool install pyscriptc
```

`pipx install pyscriptc` and `pip install pyscriptc` work the same way. The installed command finds the pyrefly checker that ships with it, so no extra PATH setup is needed. PyPI installs require CPython 3.10 or later (CI runs 3.14; the IR is stamped with the interpreter that lowered it). Prebuilt standalone binaries are attached to each [GitHub Release](https://github.com/sigilco/pyscriptc/releases): macOS arm64 and x86_64, Linux x86_64 and aarch64, and Windows x86_64 (experimental). The binaries bundle pyrefly too, so type checking works out of the box. On macOS the first launch of a binary can take several seconds; the PyPI install avoids that.

Compiling a program to a binary needs a clang that can assemble textual LLVM IR (`zig cc` also works, and enables `--driver zig` cross-compilation). `coverage`, `explain`, `--emit ir` and `--emit llvm` do not. If the driver is missing, `compile` stops before doing any work and points at `pyscriptc doctor`, which reports what was found.

| Platform | Command |
| --- | --- |
| macOS | `xcode-select --install` (or `brew install llvm` / `brew install zig`) |
| Debian / Ubuntu | `sudo apt install clang` (or `zig` on newer releases) |
| Fedora | `sudo dnf install clang` (or `zig`) |
| Arch | `sudo pacman -S clang` (or `zig`) |
| Any Linux or macOS | `mise use -g zig` or `brew install zig` |

Windows: the `pyscriptc` binary runs there (experimental) and `coverage` works, but emitted programs cannot be compiled on Windows yet, because the C runtime needs POSIX `dlfcn.h` that MinGW-w64 lacks. Use Linux, macOS or WSL to build programs.

## What it does

- Lowers an annotated Python subset (strict `x: T` typing, checked by pyrefly) to typed IR, then LLVM IR, then a native binary via clang or `zig cc`.
- Anything the subset does not prove stops at a named `PSxxxx` fence with a hint — it never guesses at semantics.
- `int` is a Python int: `i64` only when the value fits, traps on overflow, refuses literals that would truncate. `//` is floor division.
- Island calls (`subprocess.check_output`, `socket.gethostname`, `threading`, `logging`) embed CPython on first use — and only binaries that call them link libpython.
- `--emit ir` / `--emit llvm` dump the intermediate forms; `--run` compiles and executes (a `__main__` guard runs by itself, anything else needs `--call`); `--target` + `--driver zig` cross-compiles.

## Stack

| Layer | What it is |
| --- | --- |
| Parser | CPython's own `ast` module |
| Checker | pyrefly preflight + the `PS1xxx`/`PS2xxx` fence tables |
| IR | typed JSON, stamped per-interpreter (`gates-v1`) |
| Backend | hand-emitted LLVM IR text → `clang` / `zig cc` |
| Island | optional embedded-CPython calls, CPython-linked only when used |
| Scoring | `benchmarks/` layout follows [scriptc](https://github.com/vercel-labs/scriptc) |

The design is in [ARCHITECTURE.md](ARCHITECTURE.md). How to compile a script is in [docs/](docs/README.md). Contributor notes are in [.agents/docs/](.agents/docs/README.md). Programs that compile today, including an invoice, an access-log gate, and a URL allow-list, are in [examples/](examples/README.md). The benchmark layout is scriptc’s `benchmarks/` directory; see [benchmarks/README.md](benchmarks/README.md).

Licensed under Apache 2.0. Copyright 2026 Joaquin Terrasa and contributors.

## Subset

The IR stamp is `gates-v1`. The full list of what lowers, the closed standard-library table, and what fences is in [docs/supported.md](docs/supported.md). In short: annotated functions over `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, `set` and your own classes; `if`, `while`, `for`, `try`/`except` for builtin exceptions; and a closed set of calls from `math`, `re`, `json`, `hashlib`, `datetime`, `pathlib` and others. Anything else fences.

`int` is a Python int on `i64`: overflow and division by zero abort the process, and literals that would truncate are refused. `//` is floor division. `print` of `str`, `int`, `float`, `bool` and `bytes` lowers. An uncaught exception prints its name to stderr and exits 1. `finally`, `except ... as`, f-string format specs, user decorators, `super()`, multiple inheritance, `**kwargs` and non-Latin-1 string literals are fences. Known semantic differences from CPython (such as a loop target after an empty `range`) are listed in [docs/supported.md](docs/supported.md).

## Benchmarks

```bash
uv run python -m benchmarks.baseline.run --check
uv run python -m benchmarks.runtime.run --runs=5
uv run python -m benchmarks.scaling.run
```

`benchmarks/baseline/reference.json` is the reference metric set: wall time and peak RSS for each lowered workload, against the same call under CPython. Printed figures are speedups, `Nx CPython`, the reciprocal of the stored pyscriptc/CPython ratio, so a larger number is a bigger gain. `--check` still compares the stored ratios: it fails when a time ratio grows past 2× that set, or an RSS ratio grows past 1.25×. A reference of 10.0x CPython therefore fails once the run prints less than 5.0x.

The kernel geomean is the original four loops. The stdlib rows time every module that has a lowered call, including the builtin helpers. `importlib.import_module` and `asyncio.run` are fences, so those calls are not timed. `importlib.resources`, `hmac`, and Latin-1 `unicodedata` have their own rows. Calls that embed CPython (`socket.gethostname`, `inspect.cleandoc`, `threading`, `logging`, `subprocess.check_output`) are the island group: they link libpython, and their speedup sits near 1x CPython. `benchmarks/adoption` records why pyperformance, click, typer, pip, and black are not compiled suites yet.

## Telemetry

Release builds collect anonymous usage metrics with PostHog (EU host): the subcommand you ran, pyscriptc/python versions, platform, compile duration, outcome and fence counts with diagnostic family names, keyed by a random install id (no hardware ids, IP not stored, raw events kept 90 days). Never sent: file names, paths, or source text. See [site/guide/privacy.md](site/guide/privacy.md).

Telemetry is off in CI and a no-op in dev checkouts (no baked-in token). One stderr line announces it on first use. Disable it with any of:

```bash
pyscriptc telemetry disable        # writes ~/.config/pyscriptc/config.toml
export PYSC_NO_TELEMETRY=1
export DO_NOT_TRACK=1
pyscriptc telemetry reset-id       # new random install id
```

`pyscriptc telemetry status` shows the effective state and why.

## Development

From a checkout: `mise trust && mise install`, then `uv sync`, then `uv run pyscriptc ...`. More corpus programs are in [examples/](examples/README.md) and [docs/examples.md](docs/examples.md).

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -n 6 -m "not linux_container"   # or -n auto: bounded by CPU and free memory; PYTEST_WORKERS=N overrides
```

## What this does not prove

A new CPython release is not supported yet in the product sense. The stamp and the fences are the hooks that make that possible later: unknown syntax fails closed, and the backend will not silently compile IR from an interpreter it has not been run against. Island calls embed that same interpreter, and only the binary that actually calls them links libpython.
