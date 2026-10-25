# Top-100 library compatibility

This document details the evaluation methodology for pure-Python top-100 packages from PyPI.
Live status ratios and fence distributions are published nightly on the [compat site]({{PYSC_COMPAT_URL}}#pypi) and in `compat/data/support.json`.

## Evaluation methodology

The compiler measures compatibility against pure-Python libraries pinned in `benchmarks/compat/top100/pins.json` and mirrored under `benchmarks/compat/top100/src`.
Counts are live `pyscriptc coverage` runs across the package definitions.
A library only counts as fully supported when one of its own functions compiles and runs, matching CPython 3.14 differential output.

## Status categories

- 🟢 **Full (no fence)**: compiles with zero fences across all definitions.
- 🟡 **Partial**: at most half of the function and method definitions encounter a fence.
- 🔴 **Blocked**: more than half of the definitions encounter a fence.

Fences encountered during compilation point to the specific construct and diagnostic code (`PSxxxx`), documented in [supported.md](supported.md) and [What will fail](../../../docs/reference/what-will-fail.md).
Architectural refusals (Tiers T0 to T3) are detailed in [refused-features.md](../spec/refused-features.md).
Known runtime differences are cataloged in [Divergences from CPython](../../../docs/reference/divergences.md).

Data is regenerated nightly via `scripts/gen_support_table.py` into `compat/data/support.json` and rendered on the compat site.
