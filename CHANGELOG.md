# Changelog

## [0.7.0] - 2026-10-27

### Added
- os.walk, json.load, json.dump and the time clocks (#509) (#724)
- stage license texts into share/licenses of bundle outputs (#718)
- trimmed stdlib zip and bundled pure-Python dependencies (#659, #660) (#716)
- the pinned CPython runtime pack and the --embed-python seam (#657, #658) (#707)
- relocate program data files and __file__ under the program island (#665) (#708)
- pin unannotated parameters from argparse namespace fields (#703)
- mechanical deltas for real-script ports (narrowing, kwargs order, argparse, class loops) (#654)
- run multi-module programs whole on the island (#656)
- route fenced class definitions to the island floor (#628) (#637)
- native ports of real sibling scripts and delta receipts (#633)
- foldable unannotated module constants and stdlib import rows (#631)
- route module-level import fences to island top level (#627) (#630)
- cascade island caller definitions to native siblings (#620, #621, #622) (#625)
- support keyword arguments and varargs forwarding at island call sites (#605) (#624)
- suppress checker diagnostics in island definitions (#623)
- island module top level (#619)
- per-definition plane map and run --report (#618)
- per-definition fallback planner routes fenced definitions to the island (#612) (#617)
- unmarshal function return values into typed locals (#602) (#607)
- lower stdlib keywords and format Path/exc in f-strings (#595, #596) (#616)
- closed surface for script CLIs (#510) (#565)
- fast shutil.copyfile using platform ladder (#581)
- enforce refuse lane from imports policy table (#406) (#577)
- nativeDefinitions positive count in summary, report, and telemetry (#470) (#576)
- compile with --emit binding to produce ctypes loader module (#457) (#573)
- filter self routes, report import share, and cap output in assess (#570) (#572)
- assess PATH for native gains and island routes (#558) (#569)
- call-table rows for int base, open encoding and subprocess check (#563)
- requests.get pilot, generic trampoline and call wiring (#553) (#567)
- name runtime reflection and add the capability ladder (#564)
- differential receipt against the pinned CPython oracle (#555) (#559)
- island as per-import policy only, requires libpython label and reach export (#157) (#566)
- must-fence adversarial corpus and soundness ratchet gate (#560)
- stage a package's data files at the compile-time path (importlib.resources) (#550)
- S3, format, the is* predicates and repr over code points (#552)
- S2, the code-point runtime for constructors and the case family (#547)

### Fixed
- pass pbs_licenses to Nuitka as a file glob, not a directory (#718) (#726)
- realpath(strict=False) joins a missing tail lexically (#714) (#723)
- match CPython 3.14 platform.platform() output (#713) (#722)
- mirrored returns int where CPython expects (#712) (#721)
- default argparse prog keeps the source .py name (#711) (#720)
- help-probe no longer crashes on repeated add_argument registration (#710) (#719)
- refuse cross-package island imports with a named PS2002; route module assignments to the island top level (issue #641) (#705)
- island module handles re-entered from a nested def scope (#702) (#704)
- lower -> NoReturn and sys.exit(str) instead of fencing (#645) (#701)
- set island sys.executable and register the seed module (#698)
- route user-decorated module functions to the island (#676) (#694)
- island module handle peeked outside a lowering scope (#650, #643) (#691)
- island function calls see process argv; native function values demote (#635, #675) (#692)
- route unbounded annotated int growth to the island (issue #677) (#685)
- demote native callers that pass mutable containers to island callees (#674) (#684)
- carry SystemExit str payloads across the island seam (#683)
- fence untyped decorator expressions with PS2006 (#644)
- environment alignment, dependency search path and CLI routing (#615)
- report a compiled raise to the ctypes caller (#604) (#610)
- distinguish submodule relative imports from cycles (#598, #599) (#608)
- exit with the SystemExit value, not 1, when uncaught (#603) (#609)
- backfill .imports for older unpack cache entries (#562) (#584)

### Performance
- double the set index table instead of re-deriving it (#646)
- add ARMv8 and SHA-NI hardware dispatch for SHA-256 (#583)
- stat-based cache key and lazy compiler imports on warm run path (#580)

### Changed
- use docmd's llms plugin and append only the data files section (#640)

### Docs
- runtime pack licensing, security cadence and the embed CI jobs (#706)
- remove owner machine and identity leaks from notes and plans (#699)
- the native Python typing guide, and the typing framing fix (#697)
- assess nono sandbox for bounding agent sessions (#696)
- analyze mixed-plane modules, the 58.4 vs 18.4 gap and multiprocessing (#678, #641) (#687)
- measure the island remainder across scripts, libraries and packages (#682)
- record the accepted boxed churn and the lowered drop flag (#655)
- assess compiler and user-code profiling and idiom preferences (#647)
- evaluate fence lowering sequencing options (#634)
- assess compiling the harness session CLI (#649)
- add pytest native-compile assessment note (#648)
- split documentation by update cadence and align with typed subset scope (#632)
- island floor user guide for weekly use (#629)
- island floor granularity, per-definition fallback design (#597) (#611)
- document stability tiers, contracts, and packaging hygiene (#465) (#582)
- the first-class script bar and beta-readiness checklist (#507) (#579)
- renumber divergences 35-41 down to 34-40 after 34 was dropped (#578)
- generate what-will-fail page from R0 corpus fences (#508) (#574)

### CI
- drop the 5-day minimum between minors (#725)
- nightly compat snapshot 2026-10-24 (#549)

### Other
- bench(real): time the native ports and keep one island floor page (#639)
- bench(real): island floor performance on the eight dogfood scripts (#636)
- bench(scaling): add data-shaped scaling cases and complexity guards (#387) (#589)
- docs+fix(divergences): floordiv-by-zero doc, os.mkdir FileExistsError, and chr fences (#529) (#575)
- dx(compile): print the root fence and count the cascade (#511) (#571)
- bench: the library receipt, certifi compiles and matches CPython (#409) (#561)
- lower: local, parameter and return inference from the bound value (#500) (#551)

## [0.6.1] - 2026-10-24

### Added
- S1, the decision record, the fence fixes, and the interim warning (#538)
- __getattr__ reads lower to a direct hook call with a constant name (#434, D2) (#542)
- prune the unreached dependency fences by reach (#424) (#535)
- a re-export in an __init__.py binds like CPython (#516)
- the four generator statement shapes lower (G1) (#482)
- migrate pyscriptc-orjson onto the #377 pack format (#471)
- feed the deps report sourceRoots into the compile path (#398) (#463)
- refuse the dynamic attribute protocol hooks by name (PS42xx) (#461)
- skip tests, test_*.py and dist-info when unpacking pure wheels (#431)

### Fixed
- byte-parity with CPython 3.14 for sum, round, tuple display, NUL in str and universal newlines (#514, #517, #518, #519, #528) (#544)
- module-scope containers keep reference semantics (#512), plus sort, mutators, assert and bare-raise (#541)
- bare deque annotation crash, regenerate IR_HASHES for IR_VERSION 2 (#545)
- compute the snapshot date in the shell, env values are not expanded (#462)

### Performance
- the map and set indexes replace the linear scans (#464)

### Docs
- the real-script support statement and the beta scope expectation (#537)
- the 2026-10-24 CPython divergence audit (#536)
- the packages and apps page, the tiering policy for the blessed list (#521)
- the code-point str model plan and the owner decision (#480)
- the pack format spec for #377 (#460)

### Other
- lower: local, parameter and return inference from the bound value (#500) (#540)
- deps: map import names to distributions from top_level.txt, recorded at unpack (#539)

## [0.6.0] - 2026-10-23

### Added
- wasm32-wasi modules via zig cc (stacked on #249) (#313)
- windows portability and zig driver preference (#249)
- the network receipt, a loopback HTTP client and server harness (#389)
- the asyncio Lock, Event, Semaphore, Queue and to_thread (#180 D3) (#379)
- the sqlite end-to-end pilot, host and musl (#66) (#367)
- await inside with and the child's failure reaches the awaiter (#337 C1) (#365)
- beta channel tooling (v0.6.0-beta.N tags, beta bar, tester docs) (#362)
- lower asyncio.wait_for, task.cancel and CancelledError (#179 D2) (#363)
- --emit lib, the static-library mode for the mobile triples (#364)
- list and dict marshalling for manifest functions (#236)
- link manifest sources and archives in link_binary (#220) (#349)
- lower asyncio.create_task and Task handles (#179 D1) (#350)
- str, bytes and opaque handle marshalling with borrows and consumes (#224) (#348)
- lower queue.Queue, LifoQueue and SimpleQueue (#172 Stage C) (#346)
- lower yield inside try/except/finally with a susp-guarded finally (#345)
- lower @contextmanager through the generator frame (#343)
- --ffi flag with scalar manifest call lowering, nativeprobe as a manifest (#218) (#339)
- lower cross-module await through the export table (#338)
- lower orjson.dumps / orjson.loads through the pyscriptc-orjson pack over ps_json (#330)
- lower jwt.encode / jwt.decode for the HS* family through the pyscriptc-jwt pack (#328)
- project mode, one binary per [project.scripts] entry point (#325)
- lower async def as a class method on the coroutine frame (#335)
- split generators and contextmanager families (#336)
- PSxxxx histogram, binary size and the run.finished event (#257) (#331)
- generator protocol surface - send, throw, close, next with default (#333)

### Fixed
- capture the snapshot PR number from gh pr create output (#397)
- guard ps_http.c for targets without BSD sockets (#376)
- coverage_map SHARED_CORE names the emitter package over the llvm.py shim (#375)
- anchor every sequence and map in an exit registry (#370)
- a wasm target refuses the thread primitives at link time (#352)
- tempdir trailing slash and stderr print link on Darwin (#344)
- a thread target that raises does not kill the process (#342)
- replace stale argparse fence pin with sqlite3 (#341)
- master test failures: typeshed Iterator rows and jwtapp package shape (#340)

### Performance
- generator receipt tool and three perf fixes in the try path (#351)

### Docs
- complete the 0.6.0 section and record the network receipt (#418)
- DRAFT root PRIVACY.md, needs owner approval (#297)
- 0.6.0 support-state sweep (#369)
- the boxed window after the stage C merges (#371)
- the boxed window after the threading stages (#359)

### Build
- make mise.toml the default task runner (#360)

### CI
- the ThreadSanitizer receipts (#172 Stage D) (#347)

## [0.6.0] - Unreleased

### Added
- `urllib.request` and `urllib.error` through a native HTTP/1.1 client (`ps_http.c`): `urlopen` with `data`, `timeout`, headers and method via `Request`, redirects, chunked decoding, `HTTPError` and `URLError`; `https://` stays fenced until TLS (#235) lands (#322)
- threads: `threading.Thread`, `Condition`, `Semaphore`, `Event` and `queue.Queue`, `LifoQueue`, `SimpleQueue`; on `wasm32` the link is refused with a diagnostic (#172 stages C/D, #353, #346)
- asyncio surface: `asyncio.run`, `asyncio.sleep`, `asyncio.gather`, `asyncio.create_task` with `Task` handles, `asyncio.wait_for`, `task.cancel` and `CancelledError`, `async def` as a class method, cross-module await through the export table, `await` inside `with` (#335, #338, #350, #363, #365)
- generators: the protocol surface (`send`, `throw`, `close`, `next` with default), `yield` inside `try`/`except`/`finally` with a suspension-guarded `finally`, and `@contextmanager` through the generator frame (#333, #343, #345, #367)
- FFI: the `--ffi` flag with scalar manifest-call lowering, `nativeprobe` as a manifest, `str`/`bytes`/opaque-handle marshalling with borrows and consumes, list and dict marshalling, and linking manifest sources and archives in `link_binary` (#339, #348, #361, #349)
- project mode: one binary per `[project.scripts]` entry point (#325)
- `--emit lib` static-library mode for the mobile triples (iOS, iOS simulator, Android), a smaller artifact than the full mobile build (#364)
- `jwt.encode` / `jwt.decode` for the HS* family through the `pyscriptc-jwt` pack (#328)
- `orjson.dumps` / `orjson.loads` through the `pyscriptc-orjson` pack over `ps_json` (#330)
- PSxxxx fence-code histogram, binary size and the `run.finished` telemetry event (#331)
- asyncio `Lock`, `Event`, `Semaphore`, `Queue` and `to_thread` (#379)
- Windows and `wasm32-wasi` cross-compilation via zig cc, experimental (#249, #283, #313); see divergences 30-33
- FFI F3 sqlite pilot (#367)
- `PRIVACY.md` at the repo root documenting telemetry (#297)
- beta channel tooling: `v0.6.0-beta.N` tags, a beta bar, tester docs (#362)

### Fixed
- a thread target that raises no longer kills the process (#342)
- a `wasm32` target refuses the thread primitives at link time with a diagnostic instead of a link failure (#353)
- tempdir trailing slash and stderr print link on Darwin (#344)
- master test failures: typeshed `Iterator` rows and the `jwtapp` package shape (#340)

- `ps_http.c` is guarded on WASI and Windows and raises `URLError` there (#376)
- ASan exit-registry fix for runtime allocations (#370)
- nightly snapshot PR number capture (#397)

### Changed
- coverage `SHARED_CORE` prefix helper (#375)
- `.python-version` pins 3.14 (#393)
- `mise.toml` is the default task runner (#360)

### Performance
- network receipt (#389), compiled client vs CPython wall, median of 5: sequential GET 56.7 vs 99.2 ms, POST 40.4 vs 84.3 ms, threaded GET 55.6 vs 95.8 ms, POST 30.4 vs 76.0 ms (1.3x to 2.5x). Run `uv run python -m benchmarks.net.run`
- generator receipt tool and three perf fixes in the try path (#351)

### Docs
- the boxed window after the threading stages (#359)

### Internal
- generator and contextmanager coverage families split (#336), bridged fence census (#329), PyreflyBridge checker lane (#327), checker version in the irscore baseline header (#326)

Receipt: the network benchmark above (perf number with a CPython baseline).

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
