# Iteration loop

One iteration is one compiler change, scored by scorer v2 and kept or dropped by the verdict. This file is the whole procedure. The wave plan is `.agents/plans/2026-10-14-waves-after-scorer-v2.md`; the shared contract is `benchmarks/compat2/outcomes.py`. The v1 loop and its floors are retired (see Frozen v1).

A run counts only when the primary metric rises, or, inside an epic, when the epic family's fence count strictly drops. Green tests, a reworded fence, a refactor, and a flat percent are not movement. E0 is the one exception (Epics).

## Metrics

Primary: weighted pass under the CPython 3.12 oracle. `score.py` sums each row's weight over rows whose outcome is `pass`.

| rows | weight |
| --- | --- |
| language nodes, builtins and stdlib functions, classes, methods, exceptions, module imports | 1.0 |
| weak folds (`handle`, `recv-handle`, `exec`, `none`, `shape`) | 0.25 |
| constants | 0.1 |
| `raises` with a valid argument list | 0 until E2 lands, then 1.0 |
| scripts | 3.0 per program |
| top100 | 2.0 per corpus function, 5.0 per whole package |

Mention probes score 0 and stay in the ledger backlog: 1,212 rows whose receiver cannot be built, plus the roughly 1,591 `raises` probes where every candidate argument already failed under CPython. A receiver factory or an argument factory turns a batch of them into real probes inside the epic that needs them.

Secondary: definitions unblocked. In the census JSON it is `definitions - fencedDefinitions` summed over packages. The verdict's census delta adds fences removed weighted by the definitions they block: reduce the census to one entry per family with `blocks` set to that family's root fence total across `packages`, and the delta is the drop in the total.

Outcomes are the typed set in `outcomes.py`: `pass`, `fence`, `wrong-output`, `crash`, `timeout`, `build-error`, `excluded`. `wrong-output` and `crash` are soundness failures, worse than a fence, and they trip the hard gate.

## Scorer

```
uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --out results.json
```

Add `--corpus` to include the scripts and top100 corpus; the full primary metric needs it. Heavy runs go through `HEAVY=1 .agents/docker/run.sh`, one at a time. `--shard I/N` splits a run and the shards merge into one result file.

The headroom check runs first. While any denominator row is capped the scorer refuses to start (exit 2); `--headroom` lists the capped rows. Mention probes are reachable at weight 0 and only leave the headroom denominator. Never edit a probe to clear the gate; fix the denominator.

`--tier 0 --base HEAD` scores only what a diff can move. `coverage_map.py build` records, once and cached, which compiler files each probe touches; `select` intersects those sets with the git diff, capped at 400 probes. When a shared core file changes (`ir.py`, `llvm.py`, `runtime/ps_rt.c`, `runtime/ps_rt.h`), intersection means nothing, so Tier 0 takes a fixed seed stratified sample of 300 across every area and category. Tier 1 (the default) scores everything. Tier 0 gives a fast signal; a keep decision is made on Tier 1.

Results cache under `benchmarks/compat2/.cache/`. The key is the probe hash, the oracle version, and the hash of the compiler files on the probe's coverage map path (the whole tree when there is no map entry), so re-scoring an unchanged compiler is nearly free and a stale pass is impossible. `--no-cache` skips the cache; holdout runs never use it.

Tier targets live in `benchmarks/compat2/TIMINGS.md`: Tier 0 under 30 s, Tier 1 under 3 min with a warm cache. A much slower run means a cold cache or a bundling regression, not progress.

## One iteration

Artifacts live in `/tmp` and the epic file. Nothing under `benchmarks/` is hand edited, except appends to `benchmarks/compat2/failed.jsonl`.

1. Name the ids this iteration moves: from the epic's predicted flip list, the ledger backlog (`uv run python -m benchmarks.compat2.ledger results.json`), or the census ranking (`uv run python -m benchmarks.compat2.census --json /tmp/census.json`). If no id can be named, stop before touching the compiler.
2. Score the base and take the census: `uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out /tmp/base.json`, then the census again into `/tmp/census-before.json`. The cache makes the base run cheap.
3. Make the compiler change. `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and `uv run python -m benchmarks.baseline.run --check` stay green (`.agents/docs/invariants.md`).
4. Score the candidate into `/tmp/cand.json`: Tier 0 for a first signal, Tier 1 before deciding. Take the census into `/tmp/census-after.json`.
5. Verdict: `uv run python -m benchmarks.compat2.verdict --baseline /tmp/base.json --candidate /tmp/cand.json --census-before /tmp/census-before.json --census-after /tmp/census-after.json --ledger <ledger.json>`. Exit 0 keeps, exit 1 reverts. The hard gates are the soundness ratchet (no new `wrong-output` or `crash`), the pass ratchet (no `pass` flips), the perf gate (`benchmarks.baseline.run --check`), and the 1.25x compile budget. The progress check wants the primary or the secondary up. One ledger file per epic, passed again on every iteration.
6. Inside an epic, a keep also needs the epic family's fence count strictly lower than the last iteration: `rootFamilies[<family>]` summed over `packages` in the census JSON. The primary metric may stay flat mid epic; it may not fall.
7. A drop reverts the compiler edit and the iteration commit, and records the attempt in the epic file. Do not retry a finding the verdict ledger has closed.

## Epics

An epic file `.agents/plans/<date>-epic-<name>.md` declares the family, the budget (E0 6, stdlib module families 3, other epics 4 to 8, each E3 sub epic 4), and the predicted flip list: the probe and corpus ids that census and coverage say should pass once the family is lowered. Write it before the first commit of the epic.

Mid epic, keep by the iteration rules above. At the boundary the epic is kept when the primary metric has risen and at least 50% of the predicted flip list passes. Otherwise revert the whole epic and append one line to `benchmarks/compat2/failed.jsonl` with `epic` and `outcome` fields:

```
{"epic": "e3a-optional", "outcome": "reverted at budget 4: 1 of 5 predicted flips, primary flat"}
```

The boundary report records the primary and secondary metrics, the flip hit rate, the holdout aggregate, and the boxed ledger. E0 is judged differently: zero pass flips across Tier 1, no compile time rise, and the largest lowering file down at least 60% in lines.

The lowering core is touched by one track at a time; parallel epics never share a file.

## Boxed fallback

`uv run python benchmarks/compat2/boxed_cost.py --window-commits 10` at every epic boundary and in every iteration report. It reads git history for the boxed tier (`src/pyscriptc/boxed.py`, `src/pyscriptc/runtime/ps_island*`, and the `boxed:begin` to `boxed:end` regions): code share, churn share, bug share, iteration tax. Record the latest values next to Latest measurement in `AGENTS.md`.

Raise the drop flag when any holds:

- churn share above 20% or bug share above 25% in each of two consecutive windows
- iteration tax above 15% in the newest window
- boxed-only reach flat or falling for three windows while churn share plus bug share rose

A raised flag stops boxed feature work and goes to the user with the numbers.

## Frozen v1

`benchmarks/compat/` is frozen history. Its five numerators and their floors, its snapshots, and its `failed.jsonl` are not targets and not inputs. Do not run `python -m benchmarks.compat.score` and do not edit anything under `benchmarks/compat/`. v2 reuses two things: the pinned top100 sources at `benchmarks/compat/top100/src` (the census input) and the weight 0 continuity probes at `benchmarks/compat2/continuity/v1equiv.jsonl`, kept outside `probes/` so a directory load never counts them.

## Stop conditions

Stop and ask the user when any holds:

- the holdout aggregate drops while the visible primary metric rises
- the verdict ledger reports an oscillation: a finding that reappears after it was closed ends the run
- two iterations in a row cannot name an id to move
- an epic hits its budget without meeting the boundary conditions (revert first, then ask)
- a heavy run shows no output for about 5 minutes (a trust prompt may be pending)

## Holdout

The holdout is closed to generator agents. Never read it, never ask for it, never work around the guards: loader gate, aggregate only output, container mask, deny rules, PreToolUse hook, search ignore, sparse worktrees (see `benchmarks/compat2/corpus/README.md`). Agent worktrees come from `.agents/bin/agent-checkout <name>`, not plain `git worktree add`.

The evaluator alone scores the holdout, at epic boundaries, aggregate numbers only:

```
PYSCRIPTC_EVALUATOR=1 HEAVY=1 .agents/docker/run.sh uv run python -m benchmarks.compat2.score --holdout --out /tmp/holdout.json
```

A holdout drop while the visible score rises stops the loop.
