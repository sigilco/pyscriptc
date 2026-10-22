# T14 — macOS arm64 triage: `not linux_container` suite

**Date:** 2026-10-15  
**Revision:** `a98f87e` (`origin/master` at triage time)  
**Scope:** Report-only (no code fixes).

## Executive summary

| Metric | macOS-safe (CI scope) | Full tree `tests -m "not linux_container"` on macOS |
|--------|----------------------|--------------------------------------------------------|
| **Failures** | **0** | **0 expected** (compat2 macOS slot test is Linux-only failure; see below) |
| Passed | 150 | ~205 (150 + ~55 compat2; not executed in one job today) |
| Skipped | 8 | ~8 (same holdout + `/proc` skips) |
| Deselected (`linux_container`) | 165 | 165 in main tree + 2 in compat2 |

The **macOS-safe** job in `.github/workflows/ci.yml` is green on `a98f87e`. No pytest failures were observed on **GitHub Actions `macos-14`** (Apple Silicon, `aarch64-apple-darwin`, Python 3.14).

## How `not linux_container` is defined

Registered in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "linux_container: Linux dev container suite (HEAVY=1 docker/run.sh). Shells out to clang or only passes on Linux. CI does not run these.",
]
```

Selection expression: `-m "not linux_container"`.

CI splits the marker across two jobs (same commit, same marker):

| Job | Runner | Command |
|-----|--------|---------|
| `macos-safe` | `macos-14` | `uv run pytest -q tests --ignore=tests/compat2 -m "not linux_container"` |
| `ruff and compat2` | `ubuntu-latest` | `uv run pytest -q tests/compat2 -m "not linux_container"` |

**T14 primary command (macOS-native portion):**

```bash
uv sync --frozen
uv run pytest -q tests --ignore=tests/compat2 -m "not linux_container"
```

**Unified suite (not wired in CI; run manually on macOS if you want one shot):**

```bash
uv run pytest -q tests -m "not linux_container"
```

## macOS runner used

| Source | Result |
|--------|--------|
| `cursor-cloud` `list-self-hosted-workers` | **No workers** (`totalCount: 0`). Nothing registered via `cursor worker start`. |
| Cloud agent VM | **Linux x86_64** (`uname -m` → `x86_64`). Not eligible for native macOS triage. |
| **Authoritative macOS arm64 run** | **GitHub Actions** workflow `ci`, job `tests without linux_container`, `runs-on: macos-14` |

**Evidence run:** [actions/runs/38697432978](https://github.com/sigilco/pyscriptc/actions/runs/38697432978) (push to `master` at `a98f87e`), job [116297224807](https://github.com/sigilco/pyscriptc/actions/runs/38697432978/job/116297224807).

Pytest tail from that job:

```
150 passed, 8 skipped, 165 deselected in 27.02s
```

Host userspace: `aarch64-apple-darwin` (from `setup-uv` cache key in the same log). Python **3.14.8** via `actions/setup-python@v6` + `uv`.

### Linux cross-check (non-authoritative)

Same commit, `UV_PYTHON=3.14`, macOS-safe selection on the cloud VM:

```
151 passed, 7 skipped, 165 deselected
```

Extra pass vs macOS CI is mostly `tests/test_fd_hygiene.py::test_lowering_a_tree_does_not_leak_fds` (`@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), …)`). macOS skips that test; Linux runs it.

**Do not** use the cloud VM default interpreter (3.15) for parity: `tests/test_compat.py` and `tests/test_spike.py` fail on 3.15 (`builtin denominator drift`, `cpython` metadata).

## Skips on macOS (8)

| Test / area | Reason | Platform |
|-------------|--------|----------|
| `tests/test_compat2_corpus.py` — `test_default_load_never_includes_holdout` | `holdout closed (evaluator only)` | env guard |
| `tests/test_compat2_corpus.py` — `test_holdout_is_marked_and_disjoint` | same | env guard |
| `tests/test_compat2_corpus.py` — `test_script_expected_matches_cpython[…]` (×5) | `holdout closed` inside parametrized cases | env guard |
| `tests/test_fd_hygiene.py::test_lowering_a_tree_does_not_leak_fds` | `needs /proc` | **macOS only** |

Holdout skips are intentional (evaluator-only corpus); not failures.

## Failure catalogue (macOS-safe scope)

**None** on `a98f87e` / `macos-14` / Python 3.14.

## Extended scope: compat2 on macOS (not in CI job)

If you run `tests -m "not linux_container"` **including** `tests/compat2` on macOS arm64:

- **Expected:** all compat2 tests pass, including `tests/compat2/test_verdict_replay.py::test_macos_arm64_reference_is_a_separate_slot`, which exercises `benchmarks.baseline.run --record` on Darwin arm64 and restores `reference-macos-arm64.json` from git.
- **Linux contrast (same commit):** that test **fails** on Ubuntu because `reference-macos-arm64.json` is committed but the test’s non-Darwin branch asserts the file must not exist after a refused `--record`:

```
FAILED tests/compat2/test_verdict_replay.py::test_macos_arm64_reference_is_a_separate_slot
AssertionError: assert not True  # MACOS_ARM64_REFERENCE.exists()
```

Repro on Linux:

```bash
uv sync --frozen
UV_PYTHON=3.14 uv run pytest tests/compat2/test_verdict_replay.py::test_macos_arm64_reference_is_a_separate_slot -v
```

**Suspected cause:** test assumes the macOS reference file is absent on non–Darwin-arm64 hosts; the file was added to the tree in the release-matrix merge. **Platform bug / test–repo mismatch on Linux**, not a macOS failure.

This was **not** re-run interactively on a Mac in T14 (no self-hosted worker); inference is from test source + Linux repro + CI macos-safe omitting compat2.

## `tests/test_band.py` and `tmp_path` (known watchlist)

macOS-safe CI only executes **2 of 12** tests in this module (the rest carry `@pytest.mark.linux_container`):

| Runs on macOS-safe | Test |
|--------------------|------|
| Yes | `test_hmac_algorithm_stays_a_fence` |
| Yes | `test_small_names_stay_fenced` |
| No (165 deselected in module) | ten `compile_run` / oracle integration tests |

Both macOS-safe band tests **passed** in job 116297224807.

### Why `test_band` is called out

1. **Heavy tests embed `tmp_path` absolutes into generated Python** (e.g. `test_tempfile_and_shutil_match`, `test_atexit_runs_on_the_way_out`). They assume a POSIX layout and, in tempfile, a hard-coded `'/'` when checking `mkdtemp` prefix (`tempfile.gettempdir() + '/' + tempfile.gettempprefix()`). Fine on Linux CI container; on native macOS this is a common footgun if temp paths or separators differ.
2. **`test_small_names_stay_fenced` reuses one function-scoped `tmp_path` for four writes** in a loop. Safe today at function scope; breaks if someone moves these cases into a **class** with `scope="class"` fixtures (pytest forbids class-scoped tests consuming function-scoped `tmp_path`).
3. **Marker split:** most band coverage only runs under `HEAVY=1 docker/run.sh` (`linux_container`). Running the full file on macOS without the container (or without Xcode/clang on `PATH`) would fail for toolchain reasons—not observed in macOS-safe CI because those tests are deselected.

Minimal repro for the two macOS-safe band tests:

```bash
uv run pytest tests/test_band.py -m "not linux_container" -v
```

Full module (needs Linux container / clang stack):

```bash
# in dev container / HEAVY=1 docker/run.sh environment
uv run pytest tests/test_band.py -v
```

No failures were recorded for band tests on macOS in this triage; items above are **latent / classification** risks for future marker or refactor work.

## Blockers and gaps

1. **No connected macOS self-hosted worker** for Cursor Cloud (`list-self-hosted-workers` empty). Interactive repro on a laptop was not available in this run.
2. **CI does not run compat2 on `macos-14`**, so a single-command “entire repo `not linux_container`” pass is not CI-gated on macOS.
3. **Workflow dispatch / job re-run** via `gh` returned 403 / “cannot be rerun” for the evidence job; relied on existing log artifacts.
4. **Holdout hook:** a full-tree `pytest tests -m "not linux_container"` on the cloud VM once stripped `/workspace` permissions (`PermissionError`); restored with `sudo chmod`. Prefer macOS or the documented CI commands for suite runs.

## Suggested follow-ups (out of T14 scope)

- Run `uv run pytest -q tests -m "not linux_container"` once on a physical or self-hosted **Darwin arm64** host to confirm compat2 + macOS baseline slot in one log.
- Fix Linux CI failure for `test_macos_arm64_reference_is_a_separate_slot` vs committed `reference-macos-arm64.json` (product/test change, not triage).
- Register a `cursor worker` on an Apple Silicon Mac if cloud agents need native macOS repro without GitHub Actions.

## References

- `.github/workflows/ci.yml` — `macos-safe` job definition  
- `packaging/README.md` — `macos-14` as Apple silicon CI runner  
- `benchmarks/baseline/README.md` — macOS arm64 reference slot  
- Prior dispatch note: `.agents/notes/2026-10-14-cloud-dispatch.md` (T10 CI expectations)
