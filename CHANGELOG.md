# Changelog

## [0.5.1] - 2026-10-23

### Added
- bridged fence census, with the measured param-inference and uninitialized-field verdicts (#329)
- PyreflyBridge, the pyrefly lane behind the CheckerBridge surface (#327)
- record the checker version in the irscore baseline header (#326)
- lower generators in package units and as methods (#165) (#324)
- native HTTP client with redirects and request control (#322)
- the R2 tree corpus and the adoption ladder on the compat page (#323)
- lower yaml.safe_load through a C YAML-to-JSON pass (part of #62) (#315)
- lower dotenv_values / load_dotenv through a C dotenv-to-JSON pass (#314)
- assemble one site: landing, docs and compat at pyscriptc.pages.dev (#305)
- --debug-info emits DWARF so a debugger names the Python line (#299)
- add a test-case reducer (pyscriptc reduce) (#300)
- build the compat site data file and update it nightly (#298)
- --timings per-stage wall times (#277)

### Fixed
- regenerate compat.json after the generator census change (#332)

### Changed
- move docmd build and node workspace under docs/.docmd (#310)
- split llvm.py into the emit package with an IR-reader seam (#303)

### Docs
- move ARCHITECTURE.md into .agents/docs (#308)
- unify the documentation sources (#302)
- minimal hand-edited README, generator retired (#296)
- add security policy (#281)

### Build
- move .agents/docker to top-level docker/ (#282)

### CI
- install wrangler with npm, not pnpm (#307)
- put the pnpm global bin on PATH for wrangler (#306)

### Other
- D3: emitter seam tests and per-branch golden corpus (#317)
- Docs: backend contract and the curl HTTP bridge (#316)
- Make pip install ziglang satisfy which(zig): PATH shim, version pin, support matrix (#311)
- IR verifier in debug mode plus LLVM verification of the emitted .ll (#278)
- D3: emitter unit-test seam and per-node-kind golden IR corpus (#279)
- Add --sanitize=address,undefined to compile plus an ASan+UBSan CI job (#276)

## [0.5.0] - 2026-10-22

### Added
- checker bridge — feed mypy's inferred-type map into lowering (#202)
- lower tomllib.loads through a C TOML-to-JSON pass (part of #62) (#252)
- typed extern manifest parser and validator (#182)
- pyscriptc-sqlite adapter (manifest, pyi, thunk, pinned sqlite) (#247)
- allow urllib.parse.SplitResult in annotations and imports (#248)
- native Thread, Event, Condition and Semaphore (P2a phase 2) (#194)
- minimal async/await on coroutine frames (P3a) (#193)
- native Lock, RLock, get_ident and current thread (P2a phase 1) (#190)
- pin the scorer oracle to CPython 3.14 (#210)
- math and operator families (#201)
- script stdlib families (os, pathlib, shutil, subprocess, datetime, itertools) (#188)
- exception hierarchy, object surface, and try/finally semantics (E2 M2-M5) (#186)
- coroutine lowering decision doc and reserved coroutine exception codes (#200)
- stub-path and ffi keys, PEP 723 dependency lanes (#199)
- pyscriptc run FILE with a content-hash binary cache (#198)
- resolve PyPI dependencies and cache pure-Python wheels (partial) (#138)
- multi-file programs with __main__.py as the entry point (R2) (#191)
- script corpus for the R1 rung of the adoption ladder (P6) (#185)
- per-import lane classifier for the adoption ladder (#184)
- stub discovery and typed-signature registry (partial) (#140)
- generator frames pass skeleton (P0) (#142)
- PEP 723 + pyproject/pyscriptc.toml config surface with imports policy table (#187)

### Fixed
- evaluate module-call arguments once (#245)
- require Python 3.14, the version the source and parser need (#244)
- write every fixture atomically so the tar writer never reads a truncated file (#205)
- write bundle.tar atomically so xdist workers never read a partial archive (#197)

### Docs
- add logo assets and wire into README and docs site (#256)
- generated support status tables (#246) (#251)
- point to GitHub milestones and Horizon instead of release versions (#216)
- GitHub Project line names only v0.5.0, v0.6.0 and Future (#214)
- state the CPython 3.14 pin and parity contract (W1, W3) (#209)
- re-census PS4xxx refused-features + T0-T3 tier model (#203)
- plan lives in GitHub milestones and project, drop roadmap file (#206)

### CI
- regenerate IR_HASHES on Linux and enforce in CI (#212)

## [0.4.1] - 2026-10-22

### Added
- erase ABC/Protocol bases and typing overloads (T2-2) (#124)
- T2-5 static folds for builtin calls on constants (#123)
- normalize positional-only and keyword-only parameters (T2-3) (#122)
- Optional widening (nested None arms, PS1102 for multi-arm optionals) (#136)
- **kwargs params lower as dict[str, T] + leftover-keyword packing (#121)

### Fixed
- smoke compiles its own hello(n) fixture, not the first-run demo (#189)
- v0.4.1 alpha first-run package (#133)
- first-run notice, no events before it (#56) (#131)

### Performance
- cache the Nuitka onefile payload under the user cache dir (#135)
- runtime object cache with parallel builds (#132)

### Docs
- delegation models and subprocess harnesses (#141)
- project board scheme, PR-only master rule, active plan pointer
- PR-only master ruleset, cut-release bypass path, threshold explainer
- bar-gated release policy, milestones, rehearsal requirement
- note the retroactive GitHub releases for 0.2.x

### CI
- add content-keyed caches (zig, ccache/Nuitka, scorer) (#134)
- rehearsal dispatch, bar-gated cut-release, publish gate (#130)
- shard macos-safe with pytest-split and xdist (#128)

## [Unreleased]

First-run fixes for the v0.4.1 alpha.

- New: `pyscriptc imports FILE|DIR` classifies every import of a program or package tree into its compat lane: standard library, native-backed (a C, C++ or Rust extension), curated-replacement (a planned pack) or pure Python. The verdict comes from the closed import table, a data file of known native-backed distributions, and offline distribution metadata (the wheel `RECORD`, an unpacked tree). Nothing reaches the network. See `.agents/docs/spec/import-classifier.md`.
- Fix: `compile FILE --run` without `--call` now runs the file's own script, including the body of `if __name__ == "__main__":` when the module also defines `main`. Before, it compiled a no-op binary and exited 0 silently. When there is nothing to run it prints a note naming `--call` on stderr.
- `compile` checks for the C driver (clang, or zig with `--driver zig` / `PYSC_CC`) before doing any work when the output needs one, and points at `pyscriptc doctor` instead of failing deep in the link step. `--emit ir|llvm` need no toolchain.
- Fence messages name operators and constructs as written in source (`` `*` on str and float``, `f-string`, `starred expression`) instead of AST class names (`Mult`, `JoinedStr`, `Starred`).
- Docs: README and getting-started now agree with the changelog (binaries bundle pyrefly, macOS x86_64 is built, emitted programs cannot compile on Windows yet) and lead with `pyscriptc coverage`. New `examples/hello.py`.

## [0.4.0] - 2026-10-21

The rest-of-stdlib lowering moved out of one 5000-line file into a package, with no behavior change.

- Refactor: `restlower.py` is now the `restlower/` package (one module per stdlib area: datetime, pathlib, hmac, time, random, csv, textwrap, copy, collections, itertools, functools, operator, min/max/sum/sorted, builtins, io, subprocess and resource, ctypes), with registry-based dispatch instead of long if-chains. Generated IR is byte-identical (IR hashes unchanged).
- Dev: `pytest-xdist` is a dev dependency; the full suite runs in about 10 minutes with `-n 6` instead of 40.
- Fix: the release workflow no longer counts the wheel and sdist as binaries, so the GitHub draft release is created again.
- Self-host census re-pinned: 47 modules, 1382 fences.

## [0.3.0] - 2026-10-21

pyrefly is the only runtime oracle, and the release binaries work on Linux x86_64.

- Fix: the Linux x86_64 release binary did nothing and exited 0. mypy's compiled extension crashed at import inside the Nuitka build; release binaries now ship without mypy and bundle the vendored typeshed.
- The facts provider defaults to `pyrefly-tsp` (97.5% of mypy's typed positions, 85.9% kind agreement on the 14-file corpus; see `benchmarks/factsdiff.py --corpus`).
- mypy and pyright are optional extras (`oracle-mypy`, `oracle-pyright`); `--checker mypy|pyright` without the extra stops with an install hint. See `.agents/docs/spec/external-oracles.md`.
- The checker's stdlib patches (`range`, `frozenset`, `str.split`) now apply to a vendored typeshed copy (`pyscriptc/_typeshed`, from mypy 2.4) through pyrefly's `typeshed-path`, so mypy is no longer needed for the default lane. Larger wheel.
- Removed the mypy, pyright and pyright-typeserver facts adapters (about 1260 lines).
- Release 0.2.1 went to PyPI first; its Linux x86_64 binary was broken, so its GitHub Release (added retroactively) carries only the wheel and sdist.

## [0.2.1] - 2026-10-21

Release binaries work. The 0.2.0 Nuitka binaries failed their smoke test because the default checker (pyrefly, a native executable) was not inside them.

- Fix: the release binaries bundle the pyrefly executable and use it by default; `PYREFLY_BIN` still overrides.
- `pyscriptc doctor [--json]` reports the checker (path, version, resolution source), the C toolchain, cache dir, target default and install shape.
- `PYSC_TARGET`, `PYSC_CC` and `PYSC_LINKER` set the matching flag defaults; `--linker`, `--emit asm,obj` and `--out-dir` are new.
- Telemetry v1 (`compile.finished`, `coverage.reported`): anonymous, opt out with `PYSC_NO_TELEMETRY=1` or `pyscriptc telemetry disable`. See `docs/project/telemetry.md`.
- GitHub Release notes are now built from the matching CHANGELOG.md section.
- Nightly and manual cut-release workflows (`.agents/docs/release/release-process.md`).

## [0.2.0] - 2026-10-21

Installs work. Every install path from 0.1.0 failed on the default checker, and no binaries were published (its GitHub Release, added retroactively, has the wheel and sdist only).

- Fix: the pyrefly checker is found in the installed environment instead of by bare PATH lookup, so `uv tool install pyscriptc` then `pyscriptc compile` works. `PYREFLY_BIN` still overrides.
- Fix: pyrefly checks against the running interpreter's site-packages, not the host Python's.
- Fix: the pysa facts adapter matches reports by their `source_path`, so it works on real packages instead of failing on every non top-level file.
- `compile --run -- ARGS` forwards arguments to the produced binary.
- `compile --print diagnostics` prints the coverage JSON report (exit 3 when there are fences).
- `coverage --fail-on blockers|any` exits 1 when fences exceed the threshold.
- CI: a wheel smoke job builds the wheel, installs it in a fresh venv with a stripped PATH, and compiles a fixture on every PR that touches the checker, CLI or packaging.
- README: install instructions point at PyPI and GitHub Releases.

## [0.1.0] - 2026-10-17

Initial release.

- `pyscriptc` CLI (`compile`, `coverage`, `explain`): lowers an annotated Python subset to typed IR, then to LLVM, and links emitted programs against the bundled C runtime.
- CPython island: calls outside the subset (`subprocess`, `socket`, `threading`, `inspect`, `logging`, `asyncio`, `ssl`, `ctypes`) embed libpython only when the emitted code uses them.
- Closed stdlib surface: `math`, `re`, `json`, `urllib.parse`, `base64`, `hashlib`, `hmac`, `struct`, `datetime`, `collections`, `itertools`, `os`, `pathlib`, `tempfile`, `atexit`, `shutil`, `enum`, `dataclasses`. Anything outside the table fences instead of guessing.
- `try`/`except` for builtin exceptions and user subclasses; `sys.argv` as `list[str]`.
