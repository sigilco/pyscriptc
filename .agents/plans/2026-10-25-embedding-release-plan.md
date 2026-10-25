# Embedding release plan: default runtime bundle in v0.7.0, configurable engine and PyPy in v0.8.0

Status: plan and issues only, no compiler code (Refs #428, #651). Builds on `.agents/plans/2026-10-25-embedding-libpython.md` (the POC assessment, PR #652, branch `work/embedpoc`) and `.agents/notes/2026-10-25-island-imported-modules.md` (the program-wide island, PR #656, branch `work/bundleisl`). Date of every measurement below: 2026-10-25. "Measured" means run on this machine (macOS arm64, CPython 3.14.9 from python-build-standalone release 20261021, zig 0.16.0) or on the downloaded Linux archive listed in section 1; "reasoned" and "not run" are marked.

## 0. What changed since the POC verdict

The POC plan said "not now, nothing in milestones 4 to 6 asks for it". Two facts reverse that.

- The v0.7.0 gate (owner, 2026-10-25) is "use pyscriptc weekly on real scripts and on programs that depend on PyPI libraries, via the island floor". The release binary cannot produce island programs today: `packaging/TOOLCHAIN.md` line 110 says "In the release binary those paths describe the build machine, not the user's. Island programs are not a supported release target." The release binary is a Nuitka onefile build, so `sysconfig` inside it has no usable `libpython` or headers (`_driver.py` `link_binary`, line 272 onward, reads them from the running interpreter). A shipped runtime pack is therefore not a distribution nicety, it is what makes the island floor exist for anyone who installs the release binary instead of `uv tool install`.
- PR #656 moves a whole multi-module program onto the island (one bootstrap that embeds every unit's source). That makes the island the carrier for exactly the programs the gate cares about (a package plus its dependencies), so the quality of the island's runtime matters more than when the island was a per-function escape hatch.

Owner decisions 2026-10-25 apply: boxed churn accepted, drop flag lowered, island tier stays; v0.7.0 pushes hard on a high compat ratio through the island floor. This plan adds boxed code only inside `boxed:begin`/`boxed:end` markers and expects the ledger window at the v0.7.0 boundary to report the extra share.

## 1. Can default embedding fit in v0.7.0?

Answer: yes for the smallest slice (shared bundle, macOS arm64 and Linux x86_64, safe-trimmed stdlib, pinned python-build-standalone runtime pack fetched on demand), no for the static single binary and for every other platform. Verdict and kill criterion are in section 4.

### 1.1 Evidence

| Fact | Source | Status |
|---|---|---|
| Shared bundle runs from an empty environment with the build tree hidden: island binary 0.61 MB, `libpython3.14.dylib` 18.6 MB, trimmed zip 0.25 MB | POC, macOS arm64 | measured (POC) |
| Static source build: single 6.4 MB executable plus 0.25 MB zip, runs hidden | POC | measured (POC) |
| Startup: native 3.8 ms, island host link 28.1 ms, shared bundle 32.4 ms, static 27.7 ms | POC, quiet-machine medians | measured (POC), load-noisy afterwards |
| Linux x86_64 PBS `install_only_stripped` archive (35.0 MB gz) contains `lib/libpython3.14.so` (symlink) to `libpython3.14.so.1.0` (33.6 MB), `include/python3.14/Python.h`, `lib/python3.14/` (25 MB) and `lib-dynload` with only 2 entries because PBS builds most extension modules into libpython | downloaded `cpython-3.14.9+20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz` and listed it | measured |
| The Linux stdlib minus `test`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `pydoc_data`, `site-packages` and `lib-dynload` is 554 `.py` modules, 10.1 MB as a stored zip. `lib/python3.14/test` is not even present in the `install_only_stripped` archive | built the zip from the extracted archive | measured |
| `install_only` (non-stripped) Linux archives are 58 to 76 MB, stripped 30 to 37 MB, macOS 26.7 to 26.9 MB | release asset list | measured |
| `install_only` archives carry `LICENSE.txt` for CPython only; the `full` archives carry a `licenses/` directory with the third-party texts (POC section 2) | POC plus archive listing | measured |
| The Linux bundle was never linked or run by us: the PBS shared library layout is proven, our `$ORIGIN` rpath link line and the glibc 2.39 floor of the runner are reasoned | n/a | not run |

Resulting bundle sizes (uncompressed, reasoned from the measured parts): macOS arm64 about 19 MB libpython plus 10 MB stdlib zip plus the binary, about 30 MB; Linux x86_64 about 34 MB plus 10 MB, about 45 MB. The safe-trimmed stdlib is 40 times larger than the profile-run trim (0.25 MB), and that is the point: the profile-run trim is only correct for programs whose imports do not depend on input (POC section 2), so the default must be the safe trim and the aggressive trim an opt-in.

### 1.2 Design (what "default" means)

- Mode names: `--embed-python=host|bundle|auto` and the config key `[tool.pyscriptc.island] embed = "auto"`.
  - `host`: today's behaviour (link the compiler's own interpreter). Unchanged, zero download.
  - `bundle`: always link the pinned runtime pack and write a relocatable output directory `name/bin/name` plus `name/lib/...`.
  - `auto` (the v0.7.0 default): `host` when the compiler's interpreter has a usable shared libpython and headers (`uv tool install`, wheel in a venv, development), `bundle` when it does not (the Nuitka release binary, distro Pythons without `libpython3.x-dev`). Rationale: it fixes the release binary without making every developer download 35 MB or lose the host link's tested path. Flipping `auto` to `bundle` everywhere is a decision for v0.8.0, taken against the soak data (section 4).
- Where the runtime lives: not in the compiler wheel and not in the Nuitka binary. A runtime pack per platform, `pyscriptc-runtime-cp314-<platform>.tar.zst`, is produced by our release workflow from a pinned python-build-standalone archive, attached to the GitHub release (and mirrored there so we do not depend on a third-party asset staying up), fetched on demand by `pyscriptc embed fetch` (also run implicitly by a `bundle` compile, with a clear message and size), sha256-verified against a lock file `packaging/runtime.lock.toml` in the repo, cached under `~/.cache/pyscriptc/runtime/<pack-id>/`. Offline and air-gapped use: `PYSC_RUNTIME_DIR` points at an unpacked pack. Wheel size is unchanged by design (a PyPI wheel carrying 35 MB per platform is the rejected alternative: PyPI releases are irreversible, `pyscriptc-runtime` as separate wheels stays option C in the POC plan until a user needs `pip`-only installs).
- Pinning and provenance: the lock records the PBS release tag (`20261021`), asset name, sha256 and the CPython micro (3.14.9). We take the `install_only_stripped` asset for the runtime (libpython, headers, stdlib) and the `licenses/` directory from the matching `full` asset, so the pack carries third-party notices. Source builds (needed only for the static option) are not part of the 0.7.0 slice.
- Headers and ABI: a `bundle` compile uses the pack's headers and library, never the host's, so the version-specific link (`-lpython3.14`) is decided by the pack. The compiler targets CPython 3.14.x; the pack's micro may differ from the host's and that is allowed within 3.14 (stable ABI between micros, reasoned). A minor change is a pack change and a lock change, nothing else.
- Output layout and rpath: reuse the POC seam exactly (libpython passed by path, a single relative rpath, `@executable_path/../lib` on macOS and `$ORIGIN/../lib` on ELF, because `zig cc` otherwise adds absolute rpaths for every `-L`). Linux additionally needs `-ldl -lm -lpthread -lutil` and the `libpython3.14.so` symlink shipped next to `libpython3.14.so.1.0`.
- Stdlib: `lib/python3.14/` is copied as a stored zip of `.pyc` (not `.py`, so startup does not compile; effect on the 32 ms bundle startup is unmeasured), safe-trimmed as above, plus an empty-or-real `lib-dynload` directory because getpath prints a fatal-looking warning without it (POC). `--embed-stdlib=minimal` (static closure plus allowlist, with the profile-run trim as a developer aid only) is opt-in and not on the 0.7.0 critical path.
- Dependencies (site-packages): the real use case. The resolved pure-Python wheels of the program's dependencies are copied into `lib/python3.14/site-packages` of the output, so a library-using island program is relocatable. Wheels with native code need the same interpreter ABI tag, which the pinned pack makes decidable at build time (ties into `.agents/plans/2026-10-21-pypi-support-design.md`); those are refused with a coded diagnostic in 0.7.0, not bundled.

### 1.3 Interaction with the program-wide island (PR #656)

The #656 bootstrap embeds every unit's source in the binary and serves it through a `sys.meta_path` finder with the compile-time path as origin. Three consequences for a relocatable bundle.

- Code is fine: sources are in the binary, so relocating the output does not break imports. This is a strength of #656 over the per-module scheme.
- Data is not: `__file__` points at the compile-time directory, so `Path(__file__).parent / "data.json"` reads from a directory that is absent on another machine. The bundle must either copy the program directory's non-Python files into the output and rewrite the finder's origin base, or the divergence is documented. Acceptance test in the slice: relocate the output, hide the source tree, run a program that reads a sibling data file. Today's expected result is failure, so the slice records it as a numbered divergence unless the copy is done (S).
- The runtime seed in `ps_island.c` falls back to the directory of `argv[0]` for `sys.path` when `PYSC_PROGRAM_DIR` is unset (POC section 1); in a bundle that would put `bin/` on the path. The test must run with `env -i` so neither `PYSC_PROGRAM_DIR` nor `VIRTUAL_ENV` exists.

Ordering: the embedding slices that touch the island seed depend on #656 merging (or at least on `work/bundleisl` being the base for the acceptance programs); the link and pack slices do not.

### 1.4 Issues for v0.7.0 (milestone 4)

Sizes: S under a day, M one to three days, L three to five days. Issue numbers are in section 5.

| Key | Title | Size | Depends on |
|---|---|---|---|
| E1 | Runtime pack: lock file, release-workflow producer, `pyscriptc embed fetch`, cache and `PYSC_RUNTIME_DIR` | M | none |
| E2 | Promote the embed seam: `--embed-python`, `[tool.pyscriptc.island] embed`, bundle output layout, Linux link flags | M | E1 |
| E3 | Safe-trimmed stdlib zip of `.pyc`, `lib-dynload` handling, `--embed-stdlib=minimal` opt-in | M | E2 |
| E4 | Dependencies into the bundle: pure wheels to `site-packages`, native wheels refused with a coded diagnostic | M | E2, #656 |
| E5 | Empty-environment acceptance test, macOS arm64 and Linux x86_64, with a relocated and source-hidden run | S | E2, E3 |
| E6 | CI: embed jobs (path filtered on PRs, full on nightly and release rehearsal), caches, runner cost budget | M | E1, E5 |
| E7 | Licensing and notices in the pack and in the output (PSF license plus the third-party texts), update TOOLCHAIN.md and install.md | S | E1 |
| E8 | Security cadence: scheduled runtime-bump workflow, runtime version in `--version` and receipts, SLA | S | E1, E6 |
| E9 | Relocated-program data files and `__file__` under the program-wide island (copy or numbered divergence) | S | E2, #656 |

Dependency order: E1, then E2, then E3 and E7 and E9 in parallel, E5 after E3, E6 and E4 after E5 or E2, E8 last. The smallest slice (section 4) is E1, E2, E3, E5, E7 and the macOS arm64 plus Linux x86_64 parts of E6.

### 1.5 Acceptance tests

The headline test (E5), for each of macOS arm64 and Linux x86_64:

1. Compile `tests/fixtures/embed/island_fractions.py` (an untyped `fractions` import routed to the island, prints `11/12`; plus a second program that imports a pure-Python dependency installed from a lock, prints a known value) with `pyscriptc compile --embed-python=bundle --fallback=cpython -o OUT/name`.
2. Copy `OUT` to a fresh directory, delete the build tree and the pack cache, remove the compiler's interpreter from `PATH`.
3. Run `env -i HOME=$TMP OUT/bin/name` and assert stdout, stderr and exit code equal CPython's for the same script. `otool -L` / `ldd` and `readelf -d`/`otool -l` assertions: no absolute path into the build tree, no `LC_RPATH`/`RUNPATH` other than the relative one, libpython resolved inside `OUT`.
4. Negative tests: pack missing gives a message naming `pyscriptc embed fetch` (no traceback); sha256 mismatch refuses; `--embed-python=bundle` with `--target` stays "explain-only" (cross-targets are out of scope).
5. Regression pin: with the flag unset or `host`, the link line is byte-identical to today's (the existing POC test already pins this).
6. Size gates in the test: Linux bundle at most 60 MB uncompressed, macOS at most 40 MB; startup within 10 ms of the host-link island on the same machine (POC: +4 ms, within noise).

Linux x86_64 runs in CI on `ubuntu-24.04` (glibc 2.39); the aarch64 Linux row exists on `ubuntu-24.04-arm` and is a stretch (same code, one more matrix cell). The local Linux check on this arm64 Mac is a Dory container (aarch64 only), so x86_64 correctness is established by CI, which is the reason E6 precedes any claim.

### 1.6 CI cost

- Fetching the pack: 35 MB (Linux) or 27 MB (macOS) per job, cached with `actions/cache` keyed on the lock file hash, so one download per lock change.
- Producing the pack (release workflow only): downloads two PBS assets per platform, builds a stored `.pyc` zip with the pinned interpreter, uploads one `.tar.zst`. About 2 to 3 minutes per platform (estimate, not run), on runners the release matrix already uses (`macos-14`, `ubuntu-24.04`).
- Test job: compile and run two programs per platform, about 2 to 4 minutes (estimate: zig compile of the runtime plus link is the dominant part). On pull requests it runs path-filtered (`boxed.py`, `_driver.py`, `ps_island.c`, `packaging/runtime.lock.toml`, `scripts/embed*`); on nightly and on the release rehearsal it always runs. macOS minutes are the expensive ones, so the macOS cell is nightly and release-rehearsal only after the first month if the PR cost proves high; Linux x86_64 stays on every path-matched PR.
- No source build of CPython in CI for the 0.7.0 slice (that is the static option, in v0.8.0 as a stretch).
- Patch cadence: a scheduled workflow (weekly) checks the PBS latest release for a newer 3.14 micro and opens a PR that bumps the lock file; merging requires the embed jobs green. SLA: a CPython security release (checked on the first working day after python.org announces it) is shipped as a patch release within 7 days, which fits the existing "no unreleased user-visible change older than 7 days" ceiling. The runtime micro is printed by `pyscriptc --version --verbose` and written to every compile receipt so an old binary can be audited.

### 1.7 Licensing

The CPython license (PSF License Agreement for Python, GPL-compatible, permissive) allows redistribution in binary form provided the license text and copyright notice travel with it. The PBS `full` archives also list the third-party components compiled into libpython (expat, libffi, mpdecimal, OpenSSL 3, SQLite, zlib, zstd, ncurses, tcl and others, per the POC inventory); their texts travel in `licenses/` inside the pack and are copied to `OUT/share/licenses/`. The compiler itself is Apache-2.0 (`pyproject.toml`), unaffected. Inventory, not legal advice; E7 asks the owner to confirm before the first public release that ships the pack.

### 1.8 Risk list

| Risk | Likelihood and impact | Mitigation |
|---|---|---|
| Linux link line works for PBS shared libpython only on paper (nobody has run it) | medium, blocks the Linux half | E2 starts with a Linux run in a container before the CI wiring; if Linux fails, the kill criterion below applies |
| Safe-trimmed stdlib too large to be called "default" | low, 10 MB stored | `.pyc` in a deflate zip is an option if zlib is builtin (it is in PBS); measure before choosing |
| Programs whose imports depend on input fail on a minimal stdlib | high if minimal is default | safe trim is default; minimal is opt-in |
| `Path(__file__).parent` data files break after relocation (section 1.3) | high for real scripts | E9 copies the data or records a numbered divergence |
| Third-party asset removal or retagging at python-build-standalone | low, high impact | mirror in our release assets; sha256-pinned lock |
| Security patch lag of a bundled interpreter | certain over time | E8 cadence and SLA, version in receipts |
| Pack size drives users to disable the feature | medium | `auto` keeps the host link for developers; fetch is lazy and explicit |
| macOS notarization and Gatekeeper reject an unsigned `dylib` run from a downloaded directory | medium, user-visible only for distribution | out of scope for 0.7.0; document that signing is the distributor's job |
| Boxed ledger share rises (churn is accepted by the owner, but the drop flag rules still need to be reported) | certain, small | all new code marked, window reported at the boundary |
| Free-threaded or JIT builds requested | low | pack pins the default GIL build; those were slower in the POC |
| abi3-only dependencies and native wheels | medium | E4 refuses native wheels with a coded diagnostic |
| Windows and musl | out of scope | PBS has musl and msvc builds; not in the slice, tracked in #651 |

## 2. v0.8.0: configurable engine and PyPy

Goal: the engine becomes a descriptor, not a hard-coded CPython, with PyPy as the one second engine we test, and a capability manifest the compiler reads. Everything about other engines (GraalPy, MicroPython, future ones) is the engine owner's, not ours (section 2.4).

### 2.1 Engine descriptor

The TOML descriptor from the POC plan section 3, finalized for v0.8.0:

```toml
schema = 1
name = "cpython"             # or "pypy"
version = "3.14.9"
language = "3.14"            # the Python language level the engine implements
abi = "capi"                 # capi | cpyext (rpc is explicitly out of scope)
include = "include/python3.14"
library = "lib/libpython3.14.so"
home = "lib/python3.14"      # stdlib root, relative to the descriptor
init = "Py_InitializeEx"     # Py_InitializeEx | pypy_setup_home
manifest = "capabilities.json"
features = ["gil"]
```

Resolution: `[tool.pyscriptc.island] engine = "cpython" | "pypy" | "<path to descriptor>"`, flag `--island-engine`. The CPython descriptor is generated by the runtime pack (E1), so `engine = "cpython"` is just the default pack. `abi` values other than `capi` and `cpyext` are refused with a message naming the engine.

### 2.2 `ps_island.c` changes (verified by reading the file and by the POC compile against PyPy headers)

The POC compiled `ps_island.c` against PyPy's `Python.h` and it failed at exactly two APIs. In the current file that is three lines:

- `ps_island_init`, line 37: `Py_InitializeEx(0)`. PyPy has no equivalent; startup is `rpython_startup_code()`, then `pypy_setup_home(home, 1)`, then `pypy_init_threads()` (exports confirmed in the POC; the exact call order and whether `pypy_init_threads` is required for `PyGILState_Ensure` to work from a native thread were not run). Plan: a single `PS_ENGINE_START()` macro selected by a `-DPS_ENGINE_PYPY` define emitted by the driver from the descriptor's `init` field; the CPython branch is today's code.
- Lines 276 and 297: `PyException_GetArgs(val)`. Replace by `PyObject_GetAttrString(val, "args")`, which both engines support; this is behaviour-neutral on CPython and must keep the existing exception-message tests green.
- Everything else is source-compatible because PyPy's `Python.h` renames `Py*` to `PyPy*`; the library exports 806 cpyext functions under that prefix (POC). The island ABI is value marshalling (scalar, str, bytes, list), which cpyext supports. Performance of cpyext marshalling is a known cost (cpyext is slower than CPython's C API) and is not measured.

Risks specific to this change: PyPy 3.11 lacks 3.14 syntax (PEP 649, 695, 750, 758, measured by the manifest probe), so definitions bound to the island must be gated by the engine's language level; `libpypy3.11-c` is 33 MB; the legacy embedding interface is documented as deprecated in favour of cffi embedding (reason to re-check at each PyPy release).

### 2.3 Capability manifest

Schema v1 is the prototype's (`scripts/engine_manifest.py`) with two additions from the POC plan: `pep_support` derived from the feature probes, and `stdlib.private_missing` split from `stdlib.missing`. Producers: `probe` run inside the engine (authoritative; third parties run it), and for CPython the pack generates it at pack-build time. Consumers (this repo): `pyscriptc imports` and `pyscriptc assess` add an `engine` column ("island: unsupported on pypy 3.11.17"), the compile step refuses an island import the manifest lacks with a coded diagnostic (PS code allocated at implementation), and language-feature gating of island-bound definitions. Known limits stay documented: `find_spec` proves existence not loadability; packages need a per-engine wheel/ABI check; a manifest describes the OS it was generated on. The portability trap from the POC applies: probe code must stay in the lowest dialect (no `except A, B`).

### 2.4 Explicitly not our responsibility

- Other engines own their manifest, descriptor, headers and library. We validate the schema and refuse what we cannot drive; we do not ship or test GraalPy (no C embedding path found, Java Polyglot only) or MicroPython (no CPython C API, second `ps_island.c` needed).
- Compatibility of third-party packages with an engine (cpyext wheel coverage, PyPy-specific wheels) is the engine's and the package's; we report from the manifest and the wheel tags.
- The subprocess `rpc` engine ABI is out of scope for v0.8.0 (crash isolation and latency are a separate design); tracked in #651.
- Performance claims about PyPy's JIT: we measure marshalling overhead only.

### 2.5 Issues for v0.8.0 (milestone 5)

| Key | Title | Size | Depends on |
|---|---|---|---|
| P1 | Engine descriptor schema v1, loader, `--island-engine` and config key, refusal of unknown `abi` | M | E1, E2 |
| P2 | `ps_island.c` engine seam: `PS_ENGINE_START` and `PyException_GetArgs` replacement, driver defines from the descriptor | M | P1 |
| P3 | PyPy runtime pack: `libpypy3.11-c`, home layout, pinned lock, relocatable run from an empty environment on Linux x86_64 and macOS arm64 | L | P2 |
| P4 | Capability manifest v1: schema, CPython manifest produced by the pack, `imports` and `assess` consumers, compile refusal | M | P1 |
| P5 | Engine conformance corpus and CI: the same island programs on CPython and PyPy, cpyext marshalling micro-benchmark | S | P3 |
| P6 | Docs: engine contract, descriptor and manifest reference, "not our responsibility" statement | S | P4 |
| P7 | Stretch: static single-binary CPython from a pinned source build with a curated builtin module list | L | E2, E6 |
| P8 | Decision: flip `embed = auto` to `bundle` by default, using the soak data | S | E5, E8 |

## 3. Interaction summary

- With #656: E4, E9 and the acceptance programs use the program-wide island; the relocation test for data files is new coverage over #656.
- With #174 (GIL-safe entries, closed): P2 must preserve the `PyGILState`/`SaveThread` discipline; PyPy needs its own init-threads check.
- With #612, #613, #614: untouched, the planner is not changed by embedding.
- With the PyPI design (`2026-10-21-pypi-support-design.md`): E4 consumes the resolver's wheel set and tag; native wheels are the library-depth minor's problem.
- With the boxed ledger: the seam in `boxed.py` and `_driver.py` is already marked; new `embed` code goes in a new boxed-marked module `src/pyscriptc/embed.py` (decided here to keep churn out of `_driver.py`).

## 4. Go / no-go for v0.7.0

Decision: GO on the smallest slice, NO-GO on everything else for 0.7.0.

Smallest slice that is still valuable: E1, E2, E3, E5, E7 and the Linux x86_64 and macOS arm64 cells of E6. It delivers: a release-binary user compiles an island program and gets a relocatable directory that runs on a machine with no Python, with a pinned and licensed runtime. Not in the slice: static single binary (P7), Linux aarch64, macOS x86_64, musl, Windows, mobile, wasm, native-wheel dependencies, E4 (dependencies in the bundle) if time is short, E8 beyond the manual bump procedure (the scheduled workflow can land in 0.8.0), flipping the default to `bundle` (P8).

Kill criterion (checked at the point where the non-embedding items of milestone 4 have 3 or fewer open issues, so before the minor's last week):

- If the Linux x86_64 empty-environment acceptance test (E5) is not green, or the runtime pack exceeds 60 MB compressed per platform, or the bundle startup exceeds the host-link island by more than 10 ms on a quiet machine, move the open embedding issues to v0.8.0 by changing their milestone (no doc edit), keep the already merged default-off code in `boxed.py` marked, and ship v0.7.0 with the TOOLCHAIN.md statement unchanged ("island programs are not a supported release target").
- If at the v0.9.0 close there has been no user request for a relocatable island binary and the `auto` mode never selected `bundle` in a receipt, delete the seam (the original kill criterion of #651, retained).
- If the boxed drop flag is raised at the v0.7.0 boundary for reasons other than this work, ask the owner before adding more embedding code, per `AGENTS.md`.

What I would cut first if time runs short: E4, then E9 (document the divergence), then the macOS cell of PR CI, then E8.

## 5. Issue index

Keys used in sections 1 and 2 map to issues. Milestone 4 (v0.7.0): E1 #657, E2 #658, E3 #659, E4 #660, E5 #661, E6 #662, E7 #663, E8 #664, E9 #665. Milestone 5 (v0.8.0): P1 #666, P2 #667, P3 #668, P4 #669, P5 #670, P6 #671, P7 #672, P8 #673. Tracker: #651 (stays in Future, unchanged scope). Planning lives in the milestones and the board, so a slice that moves releases is a milestone change, not an edit here.

## 6. Verification log

- Linux x86_64 PBS archive downloaded, listed, extracted and the safe-trimmed stdlib zip built: run (section 1.1).
- `ps_island.c` init and `PyException_GetArgs` lines read: run (section 2.2).
- Milestone numbers and descriptions read from `gh api repos/sigilco/pyscriptc/milestones`: run.
- Not run: any Linux link or run, the `.pyc` zip startup effect, PyPy init order, cpyext overhead, runner minutes, `.pyc`-in-deflate size.
