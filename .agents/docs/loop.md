# Iteration loop

This file is the procedure for one compiler iteration after `gates-v2`. An agent with this file and the repo can name the target, run the checks, and keep or revert the commit. Epic order lives in `.agents/plans/2026-10-14-waves-after-scorer-v2.md`. File ownership lives in [map.md](map.md). Rules a change has to keep live in [invariants.md](invariants.md).

The v1 loop and its KPI floors are retired. Those floors were language 45/56, builtins 120/150, stdlib modules 156/194, stdlib surface 1983/2478, and top 100 10/100. A pytest increase, a packaging fence-count change, a progress-log line, a first-fence string edit, and a flat percent are not the metric.

The v1 scorer under `benchmarks/compat/` is frozen history. v2 lives in `benchmarks/compat2/`. The shared contract is `benchmarks/compat2/outcomes.py`.

Order of work for one iteration:

1. Open or create the epic file (budgets below).
2. On a clean tree, score the parent and build its census.
3. Name the ids this commit will move, using that baseline.
4. Edit, run the light checks, and make one commit.
5. Score that commit, build its census, and run the verdict.
6. Keep or drop with the table below.
7. When the budget is spent, or the family is ready earlier, run the epic-boundary judgment.

Do this in a worktree from `.agents/bin/agent-checkout <name>`.

## Primary metric

Weighted pass under the CPython 3.12 oracle. The 3.12 oracle is the target. This branch's scorer still uses `sys.executable` in `benchmarks/compat2/score.py` until A1's `--oracle` switch exists. The score and verdict commands in this file run with the interpreter this branch runs (`uv run python`).

`weighted_pass` is the sum of the score JSON `weights[id]` over rows whose `outcome` is `pass`. The verdict's `primary_delta` is the candidate sum minus the baseline sum.

| Class | Weight |
| --- | --- |
| Language probes | 1.0 |
| Builtins and stdlib functions, classes, methods, exceptions | 1.0 |
| Stdlib module import probes | 1.0 |
| Weak folds (`handle`, `recv-handle`, `exec`, `none`, `shape`) | 0.25 |
| Constants | 0.1 |
| Scripts | 3.0 per program |
| Top 100 corpus function (`top100.<project>.<qual>`) | 2.0 |
| Top 100 whole package (`top100.<project>`) | 5.0 |

`raises` probes that name a specific exception stay at weight 0 until E2, then 1.0. Weight 0 rows do not move the primary metric. These weights are frozen with `gates-v2` (probe `weight`, and `WEIGHTS` in `benchmarks/compat2/outcomes.py`). An iteration does not edit them.

Before the first keep of an epic, spot-check the baseline score JSON. One id of each class in the table must carry that weight. A mismatch means the freeze is wrong: stop the iteration and report it.

## Secondary metric

Leading indicator, recorded on every iteration and in the epic boundary report:

- Definitions unblocked: `fencedDefinitions` ids present on the baseline census and absent on the candidate census (visible corpus trees).
- Fences removed, weighted by how many definitions they block. This is the verdict's `census_delta`: sum of `blocks` on the baseline census minus the same sum on the candidate census.

## Epics and budgets

The epic file is `.agents/plans/<date>-epic-<name>.md` (date like `2026-10-14`). Create it before the first edit of the epic. It declares:

- the coverage family key (a key from `pyscriptc.coverage`, for example `module-statements`)
- the budget N
- the predicted flip list: probe and corpus ids that census and coverage say should pass once the family is lowered
- the epic-start weighted pass
- for E0, the line count of `src/pyscriptc/lower.py` at epic start

| Epic | Budget N |
| --- | --- |
| E0 structural refactor | 6 |
| E1, E2, E4, E5, E6, F | 4 to 8, the number written in the epic file |
| E3a, E3b, E3c, E3d | 4 each |
| One Wave C stdlib family | 3 |

Each committed attempt spends one, including an attempt you then revert. At N, stop editing and run the epic-boundary judgment. The first epic is E0, then E1. Later order is the waves plan, rechecked at each boundary with:

```bash
uv run python -m benchmarks.compat2.census --top 10
```

After E0, two epics may proceed only when they do not touch the same files. E0 itself is strictly serial.

## Heavy runs

A heavy run is `HEAVY=1 .agents/docker/run.sh ...`. It takes `/tmp/pyscriptc-heavy.lock` and runs one container. This file does not authorize a second heavy job beside one that is still running. Start the next only after the first container has exited. If a heavy pane shows no change for about 5 minutes, ask the user to check it for a trust prompt.

Scratch JSON below is written under `.agents/` because the container mounts the repo at `/work` and discards its own `/tmp` on exit. Do not commit scratch files. Stage paths by name. `chmod` lines keep the files writable after the container (it runs as root).

Write the baseline score with the interpreter this branch runs. Do this on the clean parent, before the edit:

```bash
HEAVY=1 .agents/docker/run.sh sh -c 'uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out .agents/scratch-iter-baseline.json && chmod a+rw .agents/scratch-iter-baseline.json'
```

One process, default shard `0/1`, probes in `benchmarks/compat2/probes/*.jsonl` plus the visible corpus (`--corpus`). Do not pass a holdout switch. Do not point the scorer at `benchmarks/compat2/continuity/`. If the scorer exits 2 because rows are capped, stop. That is a freeze problem, and this iteration does not edit the scorer to clear it. The full run is the Tier 1 score.

The command prints `weighted pass <number>` and writes JSON with `rows` (`id`, `outcome`, `evidence`, `code`), `weights`, and `compile_s` (sum of per-probe compile time). Keep and boundary judgments use this full Tier 1 command.

### Census file

The verdict reads `{"fences": [{"id": "PS2002:module-statements", "blocks": 4}, ...]}`. `blocks` is how many definitions that fence blocks. `census_delta` is the drop in the sum of `blocks`. Extra keys are for the family-fence rule and the unblocked count; the verdict ignores them.

Save this as `.agents/scratch-census.py`. Do not commit it.

```python
"""Usage (repo root): uv run python .agents/scratch-census.py SCORE.json OUT.json"""

import json
import sys
from collections import Counter
from pathlib import Path

from pyscriptc.coverage import build_report

score_path, out_path = sys.argv[1:]
score = json.loads(Path(score_path).read_text())
pins_path = Path("benchmarks/compat2/corpus/pins.json")
pins = json.loads(pins_path.read_text())["packages"]
blocks: Counter[str] = Counter()
family_fences: Counter[str] = Counter()
fenced: list[str] = []

for project, pin in sorted(pins.items()):
    target = (pins_path.parent / str(pin["tree"])).resolve()
    if not target.exists():
        raise SystemExit(f"missing corpus tree for {project}: {target}")
    report = build_report(target)
    fences = report["fences"]
    for entry in fences:
        family_fences[entry["family"]] += 1
    for index in report["moduleFences"]:
        fence = fences[index]
        blocks[f"{fence['code']}:{fence['family']}"] += 1
    for definition in report["definitions"]:
        if definition["fences"]:
            fenced.append(f"{project}:{definition['file']}:{definition['name']}")
        seen: set[str] = set()
        for index in definition["fences"]:
            fence = fences[index]
            key = f"{fence['code']}:{fence['family']}"
            if key in seen:
                continue
            seen.add(key)
            blocks[key] += 1

for row in score["rows"]:
    if row["outcome"] == "fence" and row.get("code"):
        blocks[f"{row['code']}:{row['id']}"] += 1

payload = {
    "fences": [{"id": fid, "blocks": n} for fid, n in sorted(blocks.items())],
    "familyFences": dict(sorted(family_fences.items())),
    "fencedDefinitions": sorted(fenced),
}
Path(out_path).write_text(json.dumps(payload) + "\n", encoding="utf-8")
print(f"fences {len(payload['fences'])} block-weight {sum(blocks.values())} fenced-definitions {len(fenced)}")
```

Run it on the host after the score container has exited (coverage lowers in-process and does not take the heavy lock):

```bash
uv run python .agents/scratch-census.py .agents/scratch-iter-baseline.json .agents/scratch-census-before.json
```

`familyFences[<family>]` is the family fence count: coverage fence entries of that family on the visible pinned trees, including module-level fences. `familyFences` counts those coverage entries. Probe rows are added only under `blocks`.

At epic start, copy the pair and keep the copies until the boundary judgment:

```bash
cp .agents/scratch-iter-baseline.json .agents/scratch-epic-<name>-baseline.json
cp .agents/scratch-census-before.json .agents/scratch-epic-<name>-census.json
```

Record the printed weighted pass in the epic file. Later iterations score a fresh pair on the current parent and leave the epic-start copies in place.

## Before you edit

The baseline score and census above already exist. Start from that clean parent.

Write these down in the epic file before changing the compiler. If you cannot name one, stop. Do not edit the compiler, and do not commit compiler changes.

1. Epic id and the epic file path.
2. Family key and budget remaining (N minus attempts already spent).
3. The probe ids, corpus ids, or fence family this commit will move, and the current outcome of each id in the baseline score (`fence`).
4. The pytest file that covers the edit.

Read `benchmarks/compat2/failed.jsonl` if it exists. Do not start an epic whose `epic` value is already in that file. Inside an epic, do not retry a behavior listed under that epic file's `dropped` heading.

## Light checks

Run these from the worktree root while editing. They do not take the heavy lock:

```bash
uv run ruff check
uv run ruff format --check
uv run pytest tests/test_<focused>.py
```

Name the focused pytest in step 4 above. Run the narrowest file or node that fails if this edit is wrong.

## Edit, then score the candidate

After the light checks pass, make one conventional commit for the iteration. No `Co-authored-by` trailer. Then score that commit. Wait until the baseline container has exited. Same probe set, with the interpreter this branch runs:

```bash
HEAVY=1 .agents/docker/run.sh sh -c 'uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out .agents/scratch-iter-candidate.json && chmod a+rw .agents/scratch-iter-candidate.json'
```

Build the candidate census after that container exits:

```bash
uv run python .agents/scratch-census.py .agents/scratch-iter-candidate.json .agents/scratch-census-after.json
```

Definitions unblocked are the baseline `fencedDefinitions` entries missing from the candidate list. Record that count, both family fence counts, and both weighted-pass numbers in the epic file.

## Verdict

Run the verdict inside the heavy container. It shells out to `python -m benchmarks.baseline.run --check` for the perf gate. Leave `--skip-perf` off. Default compile budget is 1.25 (candidate `compile_s` may be at most 1.25 times the baseline). E0 passes `--compile-budget 1.0` so any rise in `compile_s` fails.

```bash
HEAVY=1 .agents/docker/run.sh sh -c 'uv run python -m benchmarks.compat2.verdict --baseline .agents/scratch-iter-baseline.json --candidate .agents/scratch-iter-candidate.json --census-before .agents/scratch-census-before.json --census-after .agents/scratch-census-after.json --compile-budget 1.25 > .agents/scratch-verdict.json; status=$?; chmod a+rw .agents/scratch-verdict.json; exit $status'
```

For E0, change `--compile-budget 1.25` in that command to `--compile-budget 1.0`.

Exit 0 and `"verdict": "PASS"` means keep is allowed, subject to the epic rules below. Exit 1 means FAIL. E0 is the exception in its section: a lone `progress` finding is not a drop there. The JSON has `verdict`, `primary_delta`, `census_delta`, and `findings` (`severity`, `area`, `expected`, `observed`, `fix`).

The verdict applies these in order. The first four are hard gates: any finding fails the verdict before primary or census can save it. Perf is only a gate. It never adds score.

1. Soundness ratchet. A row whose outcome becomes `wrong-output` or `crash`, and whose baseline outcome was neither, is a failure (`soundness:<id>`).
2. Pass ratchet. An id that was `pass` and is no longer `pass` is a failure (`pass-ratchet:<id>`), even when another id starts passing.
3. Perf gate. `benchmarks.baseline.run --check` must pass. Time growth against the reference past 2.0, or RSS growth past 1.25, fails the gate (`benchmarks/baseline/compare.py`).
4. Compile-time budget. `compile_s` within `--compile-budget` times the baseline (default 1.25).
5. Primary delta. Weighted pass of the candidate minus the baseline.
6. Census. Drop in total `blocks`.

When the hard gates are clean and both `primary_delta` and `census_delta` are less than or equal to zero, the verdict FAILs with area `progress`.

## Keep or drop

| When | Keep only when | Otherwise |
| --- | --- | --- |
| Mid-epic iteration, every epic except E0 | Verdict is `PASS`, and `familyFences` for the epic family is strictly smaller than the baseline count | Drop |
| Epic boundary, every epic except E0 | Verdict of the epic tip against the epic-start score is `PASS`, `primary_delta` against that start score is greater than 0, and at least 50% of the predicted flip list ids have outcome `pass` in the tip score | Revert the epic |
| E0 iteration | See the E0 section | Drop |
| E0 boundary | See the E0 section | Revert the epic |

Drop means revert the iteration commit:

```bash
git revert --no-edit <iteration-sha>
```

After the revert, commit a `dropped` bullet on the epic file: the behavior name and why it failed. If `git revert` stops on a conflict, stop and ask the user. Delete the candidate scratch files. The baseline scratch stays, because the parent did not change.

An empty predicted flip list fails the boundary. An id missing from the tip score counts as not passing. Hit rate is the number of listed ids with outcome `pass` divided by the length of the list. Write the rate, both metrics, and `census_delta` into the epic file.

## E0

E0 is a structural refactor: split `src/pyscriptc/lower.py` into a `lower/` package, then a desugar pass. Move commits are pure moves so history stays bisectable. A behavior change is its own commit inside the same budget of 6.

E0 does not use the family-fence rule, the primary-rise rule, or the 50% flip rule. A pure move has `primary_delta` 0 and `census_delta` 0, so the verdict's only finding is often `progress` and the exit code is 1. That single finding is expected. Any other finding is a drop.

Keep an E0 iteration when all of these hold:

- no `soundness:` finding and no `pass-ratchet:` finding on the full Tier 1 score (zero pass flips)
- no `perf` finding
- `compile_s` did not rise (`--compile-budget 1.0`, no `compile-time` finding)
- the only finding, if any, is area `progress`

At the E0 boundary, those same gates hold, and the largest lowering file is at most 40% of the epic-start line count of `src/pyscriptc/lower.py` (a drop of at least 60%). After the split, that file is the largest `src/pyscriptc/lower/*.py`. Record both line counts in the epic file. Also record a slot for the median diff size of the next three feature commits (`git diff --shortstat` insertions plus deletions). Fill that slot when those three commits exist. It is the cost baseline for later features. Closing E0 does not wait on them.

If the boundary gates or the 60% drop fail, revert the epic.

## Reverting an epic

Revert the epic range so the tree matches the parent of the first epic commit. `<parent-sha>` is that parent and is not included. Git reverts the range from the tip backward:

```bash
git revert --no-edit <parent-sha>..<epic-tip-sha>
```

If `git revert` stops on a conflict, stop and ask the user.

Then append one JSON object as a single line to `benchmarks/compat2/failed.jsonl` and commit that line. Required fields are `epic` and `outcome`. Include `why` so the next agent can see the reason. Do not rewrite earlier lines.

```json
{"epic": "E1", "outcome": "reverted", "why": "primary delta +0.0 and 4/20 of the flip list passed"}
```

## Epic boundary report

Score the epic tip with the candidate command if that pair is not already the tip. Judge it against the epic-start copies. For every epic except E0, use `--compile-budget 1.25`. E0 uses `--compile-budget 1.0` and the E0 section.

```bash
HEAVY=1 .agents/docker/run.sh sh -c 'uv run python -m benchmarks.compat2.verdict --baseline .agents/scratch-epic-<name>-baseline.json --candidate .agents/scratch-iter-candidate.json --census-before .agents/scratch-epic-<name>-census.json --census-after .agents/scratch-census-after.json --compile-budget 1.25 > .agents/scratch-verdict-boundary.json; status=$?; chmod a+rw .agents/scratch-verdict-boundary.json; exit $status'
```

Hit rate, from the tip score and the predicted flip list in the epic file:

```bash
uv run python -c 'import json,sys; ids=sys.argv[1:]; rows={r["id"]: r["outcome"] for r in json.load(open(".agents/scratch-iter-candidate.json"))["rows"]}; hit=sum(rows.get(i)=="pass" for i in ids); print(f"{hit}/{len(ids)}")' <id> <id>
```

The boundary report in the epic file shows the primary metric and `primary_delta` against the epic start, the secondary metric (definitions unblocked and `census_delta`), the flip-list hit rate, the holdout aggregate the evaluator returned, and the boxed-cost ledger below.

The evaluator scores the held-out corpus at the boundary. You do not run that score. You record the aggregate they give you. If that aggregate drops while the visible primary rose, stop the loop and ask the user.

## Holdout

Never read `benchmarks/compat2/corpus/holdout/`. Do not ask for it, do not pass a switch that scores it, and do not work around the guards (hook, deny rules, container mask, sparse worktree). If a command is blocked for mentioning the holdout, rephrase the command or stop. Details for humans are in `benchmarks/compat2/corpus/README.md`. Create every agent worktree with `.agents/bin/agent-checkout <name>`.

## Boxed fallback, at the epic boundary

Measure and record the result in the boundary report, and update the latest-measurement line in `AGENTS.md`:

```bash
uv run python benchmarks/compat2/boxed_cost.py --window-commits 10 [--reach reach.json] [--probe-times times.json]
```

The report gives, per window, code share, churn share, bug share (`fix` commits), and iteration tax (commits that touch boxed code without naming boxed, island, or fallback in the subject). Apply the drop-flag rules in `AGENTS.md`. When the flag is raised, stop adding boxed features and ask the user whether to drop the tier.

## Frozen files

Do not edit any of these, and do not edit past v1 snapshots to move a number:

- `benchmarks/compat/score.py`
- `benchmarks/compat/denominators.json`
- `benchmarks/compat/delta.py`
- `tests/test_compat.py`
- the top-100 pins (`benchmarks/compat/top100/`)
- `gates-v1` (the subset stamp)
- `IR_VERSION`

An iteration also does not edit `benchmarks/compat2/verdict.py`, the weight table, or the probe generator.
