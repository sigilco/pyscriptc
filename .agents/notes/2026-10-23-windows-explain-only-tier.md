# Decision: Windows stays explain/coverage-only until the interest gate fires

Date: 2026-10-23. Owner: agreed 2026-10-23. Scope: the Windows support tier.

## The decision

Windows support is frozen at the **explain/coverage tier**: a Windows user can validate that their workload *can* compile (`explain`, `coverage`, and cross-compilation to a Windows target from a macOS or Linux host via zig), but cannot compile-and-run a native program on Windows.

The tier is only extended when the interest gate fires, measured on telemetry:

- **Primary gate**: Windows share of non-CI `compile.finished` events reaches **5%** over a trailing month.
- **Leading indicator**: Windows `explain` invocations exceed **10% of sessions** (users who try the compiler and bounce off the missing native tier). Pull the port forward when this fires, even if the 5% is not yet reached.

Tickets filed with the gate: #419 (the `win.explain` counter that makes the leading indicator measurable), #420 (a named diagnostic instead of MinGW header noise). The gate itself is recorded in #285.

## Why

Extending the tier is the L-tier work: the Windows semantics pass (#286) across the shims from #249, the emitted-binary smoke (#284), the real release smoke (#288), the arm64 decision (#289) and the wheel statement (#290). That is not a maintenance cost, it is a per-feature cost: every new runtime module would need a Windows path.

The demand does not justify that yet, on the numbers:

| Package | Windows share of downloads | macOS share |
|---|---|---|
| pytest | 1.9% | 1.3% |
| ruff | 1.8% | 1.9% |
| black | 1.6% | 1.4% |
| poetry | 1.3% | 0.9% |
| pre-commit | 1.1% | 1.4% |
| rich | 2.1% | 1.7% |

(pypistats, 30 days, mirrors excluded. The 95% "Linux" is CI, not people; interactive-tool reality is higher, roughly 30% Windows among Python developers per the Stack Overflow survey, which is why the gate is set at 5% of *engaged compiler users* rather than at the download share.)

Comparable GitHub projects behave the same way: those that ship Windows binaries from day one (uv, ruff, deno) ship artifacts that do not compile POSIX C, so their Windows cost is a build matrix, not a runtime port. Projects whose output is native-compiled treat Windows as a tiered target and gate the deeper work.

## What this decision costs

- The maintenance bill at this tier is about 1 to 2 engineer-days per quarter, dominated by Nuitka/MinGW64 breakage on a CPython or Nuitka point release, plus small upkeep of the Windows branches in `packaging/build.sh` and `packaging/smoke.sh`.
- The Windows CI test shard (#285) stays optional until the subset is green for a week, then becomes required. Windows runners bill at 2x Linux minutes.
- The release pipeline keeps the Windows build required and the smoke explain-only, as today.

## What reopens the decision

Any of: the 5% telemetry gate, the 10% explain leading indicator, a named external contributor offering to maintain a Windows port, or a user request attached to a paying use case. When it fires, pull #284, #286, #287, #288, #289 and #290 into the active milestone together: a partial port that stops at the runtime semantics pass would leave the tier half-claimed.
