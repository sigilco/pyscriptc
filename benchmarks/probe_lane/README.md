# The differential probe lane

The lane compiles a corpus of small programs with the current compiler, runs each one next to the pinned CPython 3.14 oracle, and byte-compares stdout, stderr and the exit code. Any difference is a candidate divergence for the divergence ledger.

It exists because every silent divergence the 2026-10-24 audit found was found by a human reading output. None was found by a probe, by the scorer, by the census or by the soundness ratchet. The repo has a strong refusal discipline and no divergence discipline. This lane is the producer the ledger consumes.

## The two inputs

The semantic probes under `probes/`: one construct each. `differential/` holds the probe the AGENTS.md differential probe rule requires for a new lowering, `divergences/` holds the probe each numbered divergence pins.

The visible corpus under `benchmarks/compat2/corpus/`: the script programs under `scripts/` that print something, and the pinned package trees under `src/` that only have to compile. The scorer holdout under `corpus/holdout/` is never read: the lane has no code path that reaches it.

## Why the bytes are untrimmed

The compat2 scorer compares whitespace-stripped stdout (`benchmarks/compat2/score.py:_judge` compares `ran.stdout.strip()` to the oracle value). That comparison cannot see a trailing space, a missing newline, a NUL truncation or an escape-sequence difference at all. The lane compares the raw bytes, so it sees them.

## Verdicts

`MATCH` is the only clean outcome: the two sides agree on stdout, stderr and the exit code.

`KNOWN` is a divergence that is already written down. For a probe under `probes/divergences/` the difference is expected, so the lane compares the observed bytes against the recorded row in `docs/reference/divergence-ledger.json` instead of against the other side. A row that still matches its recording is `KNOWN`, and a row that moved is `DIFF`.

`DIFF` names the first differing byte, and it is the finding the ledger consumes.

`FENCED` means the compiler refused the program. A construct that does not compile is outside the parity contract, so it is not a divergence. `ERROR` means the lane could not run the comparison at all.

## The ledger comparison

A ledger row records the bytes both sides produced when the row was written, and the lane reproduces that observation rather than a similar one. Two details make the difference between "the divergence moved" and "the lane measured something else".

A row whose recorded pyscriptc side is a compile stage is reproduced with the type checker on, because the ledger generator compiles that way and a compile refusal is the pinned observation.

A row is observed with both sides in the same working directory, the CPython side first, the way the ledger generator observes it. A probe that creates a file leaves it behind, so the second side sees what the first side left; row D47 is exactly that, a `FileExistsError` the second side hit. The ordinary lane comparison runs each side in its own directory, where a difference is a difference in behavior and not in what the first side left on disk.

A row that pins extra oracle arguments runs the oracle with them. The assert probe pins `-O`, so CPython skips the assert and the recorded bytes say so.

## What the lane never does

It never blocks a pull request. It exits 0 on every run, including a run that found a difference, and the nightly job that runs it is `continue-on-error` and opens no `nightly-failure` issue.

It never writes to a frozen compat2 gate file. It imports nothing from `benchmarks/compat2`: the oracle resolver is local, because `benchmarks/compat2/score.py` carries 3.14-only syntax that a 3.11 or 3.12 host cannot import, while the lane has to run on every host the compiler supports.

It never reads the scorer holdout.

## Running it

```
uv run python -m benchmarks.probe_lane.lane
uv run python -m benchmarks.probe_lane.lane --json-out out.json --summary "$GITHUB_STEP_SUMMARY"
uv run python -m benchmarks.probe_lane.lane --lane probes --only json_bytes
```

The JSON report names every program, its compile status, its verdict and the first byte difference, or says that it matched. The markdown summary carries the counts and the list of the differing programs.

The nightly job is `probe-lane` in `.github/workflows/nightly.yml`. It publishes the JSON report as the `nightly-probe-lane-<run id>` artifact.
