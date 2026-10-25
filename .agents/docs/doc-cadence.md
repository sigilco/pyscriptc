# Documentation update cadence

This document categorizes all documentation and data artifacts in pyscriptc by update frequency and ownership.
Per the documentation policy and cadence split (Decision 2026-10-25), volatile metrics and nightly measurements must live in data files under `compat/data/`, never hand-edited or generated into `.md` files.

## Document classes

Documentation is partitioned into three distinct cadence classes:

1. **Per-version docs (reference and user guides)**
   These documents define contractual interfaces, supported semantics, guides, and architectural designs.
   They are updated only when compiler features, CLI flags, ABI, diagnostics, or divergences change.
   Format: markdown (`.md`).

2. **Per-release docs (distribution and release flow)**
   These documents track release artifacts, installation versions, and version-window policies.
   They are updated strictly by the release workflow or when bumping milestone versions.
   Format: markdown (`.md`).

3. **Per-nightly data (measurements and metrics)**
   These files hold volatile evaluation numbers, pass rates, census counts, fence breakdowns, and support tables.
   They must NEVER live as hand-edited or generated markdown tables in the repository.
   They are generated exclusively as structured JSON documents under `compat/data/` and published by the compat site.
   Format: JSON (`compat/data/*.json`).

## Cadence inventory

### 1. Per-version docs (update on API, ABI, or behavioral changes)

| Path | Purpose | Owning contract |
| --- | --- | --- |
| `docs/index.md` | Landing guide and quick overview | Public front page |
| `docs/getting-started/index.md` | Quickstart guide | CLI command usage |
| `docs/getting-started/install.md` | Toolchain requirements and drivers | Driver and packaging |
| `docs/examples/index.md` | Example index | Example scripts |
| `docs/examples/programs.md` | Verified sample programs | Emitted program runs |
| `docs/guide/dotenv.md` | `dotenv` parsing rules and differences | Runtime `ps_dotenv.c` |
| `docs/guide/jwt.md` | `jwt` HMAC lowering surface | Runtime `ps_jwt.c` |
| `docs/guide/orjson.md` | `orjson` dumps and loads surface | Runtime `ps_json.c` |
| `docs/guide/http.md` | Native HTTP client guide | Runtime `ps_http.c` |
| `docs/guide/debugging.md` | Debug modes and log inspection | Compiler debug driver |
| `docs/guide/island-floor.md` | Island floor guide for running real scripts | Fallback runtime `ps_island.c` |
| `docs/guide/packages.md` | PyPI dependency strategy and seams | Compat ladder |
| `docs/guide/support-model.md` | Typed subset and refusal rules | Preflight and lowerer |
| `docs/guide/mobile.md` | Static library archive for iOS/Android | Driver `--emit lib` |
| `docs/reference/cli.md` | CLI flags and exit codes | Tier 1 CLI contract |
| `docs/reference/config.md` | `pyproject.toml` tool configuration | Config loader |
| `docs/reference/divergences.md` | Numbered divergences from CPython 3.14 | Divergence contract |
| `docs/reference/first-class-scripts.md` | First-class script bar and criteria | Quality bar |
| `docs/reference/stability.md` | Stability tiers and frozen surfaces | Governance |
| `docs/reference/what-will-fail.md` | Gap categories and root causes | Diagnostic fences |
| `docs/project/versions.md` | Supported CPython target window | Python 3.14 pin |
| `docs/project/telemetry.md` | Telemetry collection policy | Telemetry pipeline |
| `docs/project/contributing.md` | Contributor setup and workflow | Contributor guide |
| `.agents/docs/ARCHITECTURE.md` | Compiler pipeline and module layout | Architecture |
| `.agents/docs/debug.md` | Stage timings and debug flags | Debug subsystem |
| `.agents/docs/invariants.md` | Core compiler invariants | Invariant checks |
| `.agents/docs/map.md` | Subsystem code map | Developer index |
| `.agents/docs/toolchain.md` | Toolchain resolution and Zig | Compiler drivers |
| `.agents/docs/pyscriptc-debug.md` | Audit logging specification | Audit logging |
| `.agents/docs/compat-site.md` | Compat site design and deployment | Compat site |
| `.agents/docs/design/*.md` | Design specifications | Subsystem designs |
| `.agents/docs/spec/*.md` | Technical specifications | Technical specs |
| `.agents/docs/ffi/*.md` | FFI and C ABI specifications | Tier 1 ABI contract |
| `.agents/docs/support/supported.md` | Builtins and stdlib support definitions | Probe runner |
| `.agents/docs/support/compat-top100.md` | Top-100 library coverage methodology | Coverage runner |

### 2. Per-release docs (update on release cut)

| Path | Purpose | Update cadence |
| --- | --- | --- |
| `docs/getting-started/beta.md` | Prerelease installation instructions | On prerelease cut |
| `CHANGELOG.md` | User-visible change log | On release cut |
| `.agents/docs/release/release-process.md` | Release execution checklist | When release flow changes |
| `.agents/docs/release/python-version-window.md` | Host and target version windows | On Python version shifts |
| `.agents/docs/release/version-track.md` | Version mapping and SemVer rules | On version scheme shifts |

### 3. Per-nightly data (strictly JSON in compat/data/)

| Path | Purpose | Generator |
| --- | --- | --- |
| `compat/data/compat.json` | Live compat metrics and fence census | `scripts/build_compat_json.py` |
| `compat/data/ladder.json` | Adoption ladder scores across rungs | `benchmarks.scripts_corpus.ladder` |
| `compat/data/what_will_fail.json` | Corpus gaps and fence breakdown | `scripts/gen_what_will_fail.py` |
| `compat/data/support.json` | Evaluated builtin and stdlib support | `scripts/gen_support_table.py` |

## Enforcement

1. **Pull request checklist**: every pull request requires checking whether documentation was updated if API, ABI, or behavioral changes occurred (`.github/pull_request_template.md`).
2. **Release gate**: `scripts/check_doc_cadence.py` runs during release validation and CI to verify that no per-nightly metric is hard-coded in markdown files.
3. **Data isolation**: nightly automation jobs only touch `compat/data/*.json` and never open PRs touching `.md` documentation.
