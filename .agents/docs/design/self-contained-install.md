# Design note: can a plain `pip`/`uv install pyscriptc` be self-contained?

Issue #1291. Question: can we fold the `zig` toolchain into the default install so
that `pip install pyscriptc` (or `uv tool install pyscriptc`) produces a compiler
that works out of the box, with no system zig or clang required? This note only
records the analysis; it changes no packaging.

## What the toolchain actually is

The released artifact is the compiler, not the toolchain (`packaging/TOOLCHAIN.md`):
it lowers Python to textual LLVM IR and shells out to a C compiler driver
(`zig cc` preferred, `clang` fallback) to build the binary. The user-facing
requirements table in `packaging/TOOLCHAIN.md` asks for exactly two things:

1. `zig` (>= 0.13) or `clang` (>= 15) on `PATH`.
2. A C library with headers (`glibc 2.34+` plus `libc6-dev` or the distro
   equivalent) for the host builds.

So the answer to "is `pyscriptc[zig]` a complete C toolchain?" is: **no, never
quite**. `zig cc` bundles libc headers and startup files for the host and for most
cross targets, which covers the ordinary `--emit bin` / `--run` path fully, but two
documented lanes still require a system clang with a real sysroot:

- Library builds for mobile targets refuse the zig driver outright
  (`docs/guide/mobile.md`, "What a mobile target refuses"): "`--driver zig` is
  refused for a library build: zig bundles neither bionic nor the Apple SDKs, so
  the build compiles through clang with an explicit sysroot." The iOS path reads
  the sysroot from `xcrun --show-sdk-path` and the Android path needs the NDK
  (`src/pyscriptc/mobile.py`: "The driver is clang with an explicit sysroot,
  never zig").
- Sanitizer builds prefer clang: `zig cc` compiles with `-fsanitize=` but has no
  linkable sanitizer runtime (`packaging/TOOLCHAIN.md` driver resolution step 5,
  and `_driver.py`).

Additionally the emitted program needs a libc at run time on Linux, which zig
pins at link time but does not install on the user's machine (a musl host or a
glibc older than the pin will not run a default-target binary built against a
newer glibc; zig only guarantees the exact pin for `--target` builds).

## What `pyscriptc[zig]` provides today

It exists (#1196, landed 2026-11-11, before this note): `pyproject.toml` carries
`zig = ["ziglang>=0.16,<0.18"]` beside the deprecated oracle lanes
(`oracle-mypy`, `oracle-pyright`), so `uv tool install "pyscriptc[zig]"` and
`pip install "pyscriptc[zig]"` are the one-command routes. The note was written
against the state before that extra landed; the option analysis below still
holds, because the extra is opt-in and does not make a plain install
self-contained. Zig reaches the wheel user through two mechanisms, both already
built:

- `src/pyscriptc/zigshim.py`: when `shutil.which("zig")` misses but the
  `ziglang` package is importable (checked against `sys.executable`, then
  `python3`, then `python`), pyscriptc writes a `zig` shim (plus `zig.bat` on
  Windows) into `~/.cache/pyscriptc/shim` that execs `python -m ziglang`, and
  prepends that directory to `PATH` **for the current process only**
  (`zigshim.ensure_on_path`). A stale shim whose interpreter is gone is probed,
  rejected and rebuilt (`_valid_shim`).
- `src/pyscriptc/emit/llvm/_driver.py`: `_cc_argv` resolves a `zig` prefix that
  does not resolve on PATH through the same shim, and `link_binary` calls
  `zigshim.ensure_on_path()` whenever the driver is zig. So `pip install
  pyscriptc ziglang` in one environment is already a complete host-compile
  story, no PATH editing by the user.
- `pyscriptc doctor` reports zig with a `via_shim` flag and installs the shim on
  demand (`src/pyscriptc/doctor.py`, `_zig_tool`), and the missing-toolchain
  error (`cli/_main.py:_require_toolchain`) already names `pip install ziglang`
  in its hint (`zigshim.install_hint`), before any compile work runs
  (asserted by `tests/test_first_run.py`).

The code PR for this note (issue #1291, branch `work/1291-self-contained`) lands
the shape-aware hint the recommendation asks for: `zigshim.install_command(shape)`
and `zigshim.install_hint(shape)` lead with the one command that reaches the
install the user actually has (`uv tool install pyscriptc --with ziglang --force`
for a uv tool environment, `pipx install "pyscriptc[zig]" --force` for pipx,
`pip install ziglang` for a plain venv install), `doctor` exposes it as
`toolchain.fix` on its `toolchain:` line, and the up-front compile check uses
the same hint.

## Wheel sizes on PyPI (`ziglang` 0.17.1, fetched from the PyPI JSON API)

Per-platform wheels, one per machine; sizes are the download size of the wheel
for the user's platform:

| Platform wheel | Size |
| --- | --- |
| manylinux/musllinux x86_64 | 101.1 MB |
| manylinux aarch64 | 98.1 MB |
| macOS arm64 | 100.4 MB |
| macOS x86_64 | 104.5 MB |
| win_amd64 | 101.9 MB |
| win_arm64 | 97.4 MB |
| others (i686, armv7l, ppc64le, s390x, riscv64, win32) | 99-108 MB |

Every wheel is roughly 100 MB. Installed on disk it is larger (the wheel is
compressed; the toolchain unpacks to several hundred MB). This is the single
dominating number in the decision: making ziglang unconditional multiplies every
pyscriptc install, everywhere, by about two orders of magnitude of download, in
exchange for removing one install step on machines that mostly already have a
C toolchain (every macOS with Xcode CLT, every Linux dev box with a compiler,
every CI runner image).

## What pip and uv can and cannot do

- **No post-install hooks.** pip/uv run no arbitrary code after installing a
  wheel. The wheel ecosystem's answer is platform-conditional *dependencies*,
  not scripts. There is no equivalent of npm's `postinstall` at all.
- **Environment markers (PEP 508) are the only conditional mechanism**, and they
  can only see what Python sees at resolution/install time: `sys_platform`,
  `platform_machine`, `platform_system`, `python_version`, `os_name`, and a few
  more (`platform_release`, `platform_version`, `implementation_name`, ...).
  They cannot express "a `zig` or `clang` binary already exists on PATH", which
  is exactly the condition we want. So "skip ziglang when a system toolchain is
  found" is not expressible as a dependency; the issue's premise (a wheel
  dependency cannot be conditional at install time on toolchain presence) is
  correct. Even `platform_machine` tricks only reach per-CPU conditioning, not
  toolchain detection.
- uv's docs confirm the model: resolution is marker-driven, there are no build
  or install hooks for dependencies (the feature requests for per-dependency
  build hooks, e.g. astral-sh/uv#13267, are open and unanswered).

So any "self-contained by default" design has to either (a) pay the ~100 MB
unconditionally, (b) pay it lazily at first run, or (c) leave the split to the
user. That gives the four options below.

## Options

### (a) `ziglang` as a hard dependency

`dependencies = ["pyrefly...", "ziglang>=0.13"]`. Every install of pyscriptc
carries ~100 MB and a working host toolchain, and the shim machinery makes it
usable without PATH edits.

- Pro: genuinely self-contained for the host `--emit bin`/`--run` path;
  cross-compilation works too, because the zig driver is what bundled sysroots
  are for (`--target` requires zig, `_driver.py`).
- Con: ~100 MB on every install, including CI machines that use clang, library
  builds (which refuse zig anyway), `--emit ir`-only users, and every project
  that depends on pyscriptc as a library. It is also a versioning liability: the
  supported zig range (>= 0.13 floor per `packaging/TOOLCHAIN.md`) would need an
  upper bound and a repin every time zig breaks something, for every user, on
  every `pip install`.
- Verdict: the cost is paid by people who do not need it, always. Rejected.

### (b) Keep no extra; first-run check in doctor and on first compile offers `uv tool install --with ziglang`

Keep pyscriptc light. `pyscriptc doctor` and the up-front toolchain check (which
already runs before any compile work, `cli/_main.py:_require_toolchain`) detect
that neither zig nor clang exists, and the diagnostic offers the one-liner that
actually works for the tool-shaped install:
`uv tool install --with ziglang pyscriptc` (reinstall with `--with ziglang`, or
`uv tool install pyscriptc --with ziglang --force` on an existing tool). The shim
then resolves zig through the tool environment (`zigshim.find_ziglang_interpreter`
already checks `sys.executable` first, which is exactly the `uv tool` case), so
no PATH editing follows.

- Pro: zero cost for the majority (clang present: macOS, Linux dev boxes, CI);
  correct and actionable message for the minority; the mechanism (shim + doctor
  + up-front check) already exists and is tested (`tests/test_zigshim.py`,
  `tests/test_first_run.py`, `tests/test_cross.py`). No new dependency graph, no
  zig version pin in our metadata.
- Con: not self-contained; the first compile on a bare machine stops with a
  diagnostic instead of working. The offered command is uv-specific; pip users
  get `pip install ziglang` in the same environment (which the hint already
  names).
- Verdict: the cheapest option that closes the actual gap (the gap is a
  diagnostic quality problem, not a packaging problem), at the cost of not
  being "self-contained" in the literal sense.

### (c) A `pyscriptc-toolchain` meta package

Publish a tiny distribution that depends only on `ziglang` (bounded to a known
good minor), documented as the optional companion install:
`uv tool install pyscriptc pyscriptc-toolchain` or a plain
`pip install pyscriptc pyscriptc-toolchain` into the same environment.

- Pro: self-containment is opt-in and cheap for users who want it; the meta
  package can carry the zig version policy (pin/repin) without touching
  pyscriptc's metadata; nothing changes for clang users.
- Con: a second package to release, version-coordinate and support forever; the
  installed result is the same environment state as option (b)'s
  `--with ziglang`, so it adds process rather than capability; two artifacts on
  PyPI invite "which one do I install" confusion in docs.
- Verdict: defensible if users actually ask for a pip-only path, but it
  duplicates what `--with ziglang` already does.

### (d) Status quo

What exists today: no zig extra; `doctor`, the up-front check and the shim; the
`install_hint` names `pip install ziglang`, mise, brew, winget and ziglang.org.

- Pro: zero work.
- Con: the hint's first suggestion (`pip install ziglang`) is slightly wrong for
  the tool install shape, where a separate `pip install` lands in an environment
  the tool does not use, and the shim's interpreter probe (`python3`, `python`
  on PATH) papers over it at best; there is no doctor output that says "you are
  one `--with ziglang` away from a working toolchain".

## Interference with other projects

The shim cannot leak. `zigshim.ensure_on_path` prepends
`~/.cache/pyscriptc/shim` to `PATH` on `os.environ` of the running process, and
that environ dies with the pyscriptc process: shells, IDEs and sibling projects
never see it. It does not write anywhere else (no `~/.local/bin`, no shell rc
edits), and the shim itself only exists inside `~/.cache/pyscriptc/shim`. If a
user later installs a real zig, `resolve_zig` prefers the PATH hit, so the shim
stops being used without being removed. Consequence for all options: making
ziglang easier to obtain does not put a pyscriptc-owned `zig` on anyone else's
PATH; the interference surface is empty.

One real interference is the reverse direction: a pyscriptc-created shim outlives
the environment that made it. That case is handled (`_valid_shim` probes the
interpreter, `ensure_on_path` unlinks and rebuilds), but any option that makes
wheel-zig more common makes stale-shim rebuilds more common, which is a
`~/.cache` write on first compile after an environment change - acceptable, and
already the behavior.

## Windows

Windows is the weakest platform and the note should not pretend otherwise:

- `default_driver()` returns `zig` on `win32` (`doctor.py`), and the driver
  resolution forces zig on Windows (`_driver.py` step 4), because there is no
  clang guarantee there.
- ziglang publishes `win32`, `win_amd64` and `win_arm64` wheels (~97-104 MB), so
  a wheel-only Windows install is fully covered by the shim (which also writes a
  `zig.bat`).
- But emitted Windows-native programs are experimental: `packaging/TOOLCHAIN.md`
  says no CI job builds or runs Windows-emitted binaries anywhere, and "real
  Windows support is a later milestone". So a self-contained Windows install
  would deliver a toolchain for a lane we do not yet stand behind. Nothing in
  options (a)-(d) changes this; it only means the Windows case must not be the
  argument that decides the packaging question.

## What pnpm and bun do

Both ecosystems converged on the same answer to this exact problem (install-time
code is a supply-chain risk, so you cannot just run a script to fetch a native
toolchain), and neither made the toolchain an unconditional dependency:

- pnpm since v10 blocks dependency build scripts by default; approval is explicit
  (`pnpm approve-builds`, recorded as `allowBuilds` in `pnpm-workspace.yaml`),
  with `strictDepBuilds` making unapproved build scripts an error.
- bun is "default-secure": lifecycle scripts run only for packages on an
  allowlist, either the built-in curated list (`trustedDependencies` semantics)
  or an explicit project list; everything else is skipped and surfaced with
  `bun pm untrusted` / `bun pm trust`.
- The natively-rust/npm packages that need a binary (esbuild, sharp, ...)
  therefore ship *platform-conditional optional dependencies*: one dependency
  per platform triple, resolved by marker at install time. That is the closest
  npm-side analogue of what ziglang already does on PyPI (one wheel per
  platform), not of a postinstall hook.

The lesson for us: the modern default across package managers is "do not run
install-time code; make the native artifact a plain, per-platform artifact; put
a human-approved step in front of fetching it". Option (b) is the pip/uv-native
rendering of exactly that: no install-time code, the artifact is a wheel, and
the human approves the fetch at first run with one command.

## Recommendation

Option (b), plus the small fix that motivates the issue: teach `doctor` and the
up-front toolchain diagnostic about the install shape. Concretely, in a later
code PR (not this one):

- `doctor` already knows the install shape (`install_shape()` in
  `doctor.py`); when shape is `tool` and no toolchain is found, the hint should
  offer `uv tool install pyscriptc --with ziglang` (reinstall form) instead of
  leading with `pip install ziglang`; for `pip`/`wheel` shapes the existing hint
  is already correct.
- Keep the `pip install ziglang` wording somewhere in the hint for pip users and
  keep the tests that pin it (`tests/test_first_run.py`,
  `tests/test_cross.py`) updated in the same PR.

Rejected alternatives, summarized: (a) pays ~100 MB per install for everyone
forever to save one command for the few; (c) adds a second release artifact to
coordinate for no capability `--with ziglang` lacks; (d) leaves the tool-shaped
hint misleading, which is the actual defect behind #1291. The literal goal in the
issue title ("self-contained") is not reachable for free in the pip/uv model -
markers cannot detect a toolchain and there are no postinstall hooks - so the
honest version of self-containment is: detect, then offer the exact one-liner.

What a `pyscriptc[zig]` extra would still be good for (documented, not decided
here): `uv tool install pyscriptc --with pyscriptc[zig]` and
`pip install pyscriptc[zig]` as a spelled-out opt-in, which is option (b)'s
mechanism with a name attached; it costs nothing at rest and can be added any
time without re-opening this decision.

## Proposed decisions.md row

Not appended to `.agents/decisions.md`; proposed wording for the owner to land
with (or after) the eventual code PR:

```
2026-11-17 | pyscriptc: first-run toolchain hint over a hard ziglang dependency | the wheel world has no postinstall hooks and PEP 508 markers cannot detect a compiler on PATH, so self-containment by install is either ~100 MB for every user forever (hard dep) or a second artifact to release forever (meta package); the shim in ~/.cache/pyscriptc already makes `--with ziglang` complete for host builds (mobile lib builds refuse zig regardless: bionic and Apple SDK sysroots need system clang), and doctor + the up-front check are the realistic equivalent of a pnpm/bun-style explicit approval step.
```
