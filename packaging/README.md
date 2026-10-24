# Packaging: the release binary and the FFI adapters

One self contained executable per target, built with [Nuitka](https://nuitka.net) in onefile mode. It carries the compiler, the bundled pyrefly preflight binary and the C runtime. It does not carry clang; see [`TOOLCHAIN.md`](TOOLCHAIN.md) for what the user machine needs.

`adapters/` holds the FFI adapters (notably `pyscriptc-sqlite`), a reference layer that stays outside the installed package.

Nothing here touches `src/`. Nuitka is an optional dependency group (`packaging` in `pyproject.toml`), so `uv sync` for development does not install it.

## Release pipeline

GitHub Actions workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) builds every supported target on its native runner (Nuitka does not cross-compile). Triggers are version tags `v*` and manual `workflow_dispatch`.

| Target | Runner | Smoke in CI |
| --- | --- | --- |
| `pyscriptc-macos-arm64` | `macos-14` | full `packaging/smoke.sh` |
| `pyscriptc-macos-x86_64` | `macos-15-intel` | full |
| `pyscriptc-linux-x86_64` | `ubuntu-24.04` | full (`patchelf` installed on the runner) |
| `pyscriptc-linux-aarch64` | `ubuntu-24.04-arm` | full |
| `pyscriptc-windows-x86_64.exe` | `windows-2022` | explain / `--help` only (experimental) |

macOS x86_64 uses the native `macos-15-intel` runner. `macos-13` is retired. The workflow does not cross-compile that target from `macos-14`.

`pyscriptc-windows-x86_64.exe` is experimental. The compiler binary may build with Nuitka MinGW64 (`--assume-yes-for-downloads`, plus `--experimental=force-mingw64` because Nuitka rejects MinGW64 on Python 3.14 otherwise). Programs it emits need POSIX headers (`dirent.h`, `dlfcn.h`, `unistd.h`) and are out of scope, so the Windows smoke does not compile or run. Only that smoke step may fail without failing the job. The Windows build step may not. There is no Windows arm64 artifact.

Linux x86_64 and aarch64 are built on ubuntu-24.04, whose glibc is 2.39. That floor is recorded in the draft release notes. The workflow does not switch runners to chase an older glibc.

A final job writes `sha256sums.txt` over all five binaries and uploads them together as the `release-bundle` workflow artifact. Pushing a `v*` tag also opens a draft GitHub Release named after the tag, with the binaries and checksums attached. The workflow never publishes that release. `workflow_dispatch` with no tag still produces the artifacts and checksums, and creates the draft only when `github.ref_type` is `tag`.

## Targets

| Target | Output name | Built on | Status |
| --- | --- | --- | --- |
| macOS arm64 | `pyscriptc-macos-arm64` | Apple silicon Mac, system clang (no Xcode shim dependency) | built and smoke tested, see Status |
| macOS x86_64 | `pyscriptc-macos-x86_64` | Intel Mac (`macos-15-intel` in CI) | CI matrix |
| Linux aarch64 | `pyscriptc-linux-aarch64` | Linux aarch64 host or container, `patchelf`, `clang` or `gcc` | see the status section below |
| Linux x86_64 | `pyscriptc-linux-x86_64` | Linux x86_64 host (the Dory VM here is aarch64, so not built locally) | CI matrix |
| Windows x86_64 | `pyscriptc-windows-x86_64.exe` | Windows x86_64, Nuitka MinGW64 (no arm64) | experimental; emitted programs need `dirent.h`, `dlfcn.h`, `unistd.h` and are out of scope |

Nuitka does not cross compile. Build each target on its own OS and architecture.

## Build

```bash
packaging/build.sh --check-env   # which target this host builds, tool versions
packaging/build.sh               # writes packaging/dist/pyscriptc-<os>-<arch>
```

`build.sh` creates a per target venv under `packaging/build/`, installs the `packaging` group with `uv sync --group packaging`, and runs Nuitka on `packaging/entry.py`. A warm build takes about 2 to 4 minutes on an 8 core Mac. Output is about 18 MB.

One Nuitka trap: `--include-data-dir` silently drops `.pyi` files, because Nuitka treats `.pyi` as code and ignores the suffix for data inclusion (`nuitka/freezer/IncludedDataFiles.py`, `default_ignored_suffixes`). The preflight then dies with `Failed to find builtin module "_collections_abc", perhaps typeshed is broken?`. `build.sh` ships the vendored typeshed stubs with the three argument form `--include-data-files=<typeshed>=pyscriptc/_typeshed/=**/*.pyi`, which resolves each matched file and keeps its relative path.

Environment knobs: `NUITKA_LOW_MEMORY=1` and `NUITKA_JOBS=N` for small hosts, `MACOSX_DEPLOYMENT_TARGET` for an older macOS floor.

### Linux through the dev container

The dev container image has `clang` and `uv` but not `patchelf`, so install it inside the run (nothing persists):

```bash
docker/run.sh bash -c 'apt-get update -qq && apt-get install -y -qq patchelf ccache >/dev/null && NUITKA_LOW_MEMORY=1 packaging/build.sh'
```

The container mounts the worktree at `/work`, so the binary lands in `packaging/dist/` on the host. A Nuitka build is not a light run; on a 1 CPU container expect it to take a long time.

### Linux x86_64 on a real host

```bash
sudo apt-get install -y clang build-essential patchelf   # or the distro equivalent
curl -LsSf https://astral.sh/uv/install.sh | sh          # or mise use uv
packaging/build.sh
```

## Smoke test

```bash
packaging/smoke.sh [BINARY] [SOURCE.py] [FUNCTION] [ARGS_LITERAL]
```

It copies the binary to an empty temp directory, runs it with a scrubbed environment (only `PATH`), checks that `explain PS2002` answers, compiles the source with `--run --call`, and compares stdout with the same function under CPython 3.14 (`uv run --python 3.14`). The default source is `examples/hello.py`; the repo has none yet, so it falls back to `packaging/smoke/hello.py`. Exit 0 means the outputs match.

Two gotchas this script works around: `-o` must be an absolute path (cli.py runs the built program with `subprocess.run([str(binary)])`, and a bare name is resolved through `PATH`, never the cwd), and the preflight needs the typeshed stubs shipped inside the binary (see the build section).

Run it on every target before attaching a binary to a release. The plan's acceptance line for this step: unpack the release binary on macOS arm64 and Linux aarch64, compile `examples/hello.py`, run it, and compare the output with CPython 3.14.

## Status

- 2026-10-14, macOS arm64 (Apple M1, macOS 26.x), Apple clang 21.0.0, Nuitka 4.3, Python 3.14 host, uv 0.13.0.
- Command: `packaging/build.sh` then `packaging/smoke.sh packaging/dist/pyscriptc-macos-arm64 examples/add.py add '[2, 3]'`.
- Binary: `packaging/dist/pyscriptc-macos-arm64`, 18 MB, Mach-O arm64, no libpython dependency. Runs `explain PS2002` under `env -i` with no Python on `PATH`.
- `examples/add.py add [2, 3]`: binary `5`, CPython 3.12 `5`, PASS.
- `packaging/smoke/hello.py hello [20]`: binary `41`, CPython 3.12 `41`, PASS (default smoke run).
- Cold vs warm build: about 3m48s warm (ccache), single target on 8 cores.
- Known gap: preflight inside the binary needed an extra `--include-data-files` line for the typeshed stubs (`Nuitka` ignores `.pyi` as data by default). Landed in `build.sh` on this date.
- Test suite on this branch: 221 failed, 87 passed. Every failure traces to the A0 `errno.EADV` macOS crash in `stdmore.py`, fixed on `master` and not merged into `work/s0-packaging` yet. Not caused by the packaging files (verified by stashing them and re-running).

## Files

| File | Role |
| --- | --- |
| `entry.py` | Nuitka entry point. Points `sys.executable` and `PYREFLY_BIN` at the bundle, carries a temporary platform constant workaround |
| `build.sh` | Per host build recipe |
| `smoke.sh` | Release smoke test |
| `smoke/hello.py` | Fixture used until `examples/hello.py` exists |
| `TOOLCHAIN.md` | What the binary needs from the machine, what it bundles, known issues |
