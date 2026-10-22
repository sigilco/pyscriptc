# Development notes

These pages are for someone changing the compiler. User-facing docs live in [`docs/`](../../docs/), the design overview is [`ARCHITECTURE.md`](ARCHITECTURE.md), and the published site is at <https://pyscriptc.pages.dev>.

Preview the internal notes with `mise run docs:internal` (a docmd dev server over this directory, no build step).

## Core notes

- [Invariants](invariants.md) — rules a change has to keep
- [Debug mode](debug.md) — `compile --debug`, `PYSC_DEBUG`, `--debug-dir` artifacts
- [Audit logging](pyscriptc-debug.md) — the JSONL audit log and its directives
- [Map](map.md) — which file owns a fence, a runtime call, or a test
- [Roadmap](roadmap.md) — language order after the stdlib queue, and where native `.so` files sit
- [Benchmark audit](benchmarks-audit.md) — what the benchmark suite measures and what it does not
- [Compat site](compat-site.md) — the data file, the builder and the deploy

## Specs

- [E1 boundary](spec/e1-boundary.md) — the E1 lowering boundary
- [E1 entrypoint detector spec](spec/e1-entrypoint-detector-spec.md) and [results](spec/e1-entrypoint-detector-results.md)
- [E2 exception model](spec/e2-exception-model.md) — the exception surface
- [IR canonicity](spec/ir-canonicity.md) — the canonical IR rules
- [Fence repro probes](spec/fence-repro-probes.md)
- [Import classifier](spec/import-classifier.md)
- [Refused features (PS4xxx)](spec/refused-features.md) — what is refused by design
- [External oracles](spec/external-oracles.md) — mypy and pyright as second opinions
- [HTTP seam design](spec/http-seam-design.md)
- [Wave C backlog](spec/wave-c-backlog.md)

## Design notes

- [Checker bridge](design/checker-bridge.md)
- [Coroutine lowering](design/coroutine-lowering.md)

## FFI

- [FFI manifest](ffi/ffi-manifest.md) — the typed extern manifest and its lanes
- [ABI strategy](ffi/abi-strategy.md)

## Benchmarks

- [Compile-rate essence method](benchmarks/compile-rate-essence-method.md)
- [Self-host baseline](benchmarks/self-host-baseline.md)
- [Cross-compile assessment](benchmarks/cross-compile-assessment.md)
- [E0 cost baseline](benchmarks/e0-cost-baseline.md)

## Release

- [Release process](release/release-process.md) — the bar, the rehearsal and the tag
- [Version track](release/version-track.md)
- [Python version window](release/python-version-window.md) — why the floor is CPython 3.14

## Generated support data

The two pages below are generated from the compiler by `scripts/gen_support_table.py` (data in `scripts/support_data.toml`); `tests/test_support_docs.py` fails when they drift. Do not hand-edit them.

- [Supported surface](support/supported.md) — builtins and standard library, probe-checked
- [Top-100 library compatibility](support/compat-top100.md) — live `coverage` runs over the pinned top-100 sources

These files are the in-repo snapshot of the data the compat site publishes.

The pipeline that feeds the site is in `scripts/build_compat_json.py` and `scripts/compat_schema.py`; it reads the same `support_data.toml` through the same `evaluate` function, so the tree keeps one definition of "supported". Its output is `compat/data/compat.json`, rebuilt by the nightly workflow and landed through a bot PR. The design is in [Compat site](compat-site.md).

The site itself is one Cloudflare Pages project at `https://pyscriptc.pages.dev`, with the compat page at `/compat` (linked from the docs through the `PYSC_COMPAT_URL` placeholder, default `https://pyscriptc.pages.dev/compat`). Until it is deployed, these two generated pages and `compat/data/compat.json` are the machine-readable status in the tree.
