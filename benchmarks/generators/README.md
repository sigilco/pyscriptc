# Generator performance receipt

The generator receipt from the async and threads design (P1): a generator
consumed by a `for` loop, measured against the hand-written `while` loop that
computes the same total.

The two programs are separate files, because the comparison is between two
compiled binaries. Both print the same total, and the runner refuses to
report a ratio when their stdout disagrees.

| Program | What it does |
| --- | --- |
| `generator.py` | a generator yields a hundred million integers, the `for` loop sums them |
| `while_loop.py` | the same total, computed by a `while` loop |

```bash
uv run python -m benchmarks.generators.run
uv run python -m benchmarks.generators.run --runs=5 --json=generators.json
```

The runner compiles both programs, checks the stdouts match, then times
`--runs` interleaved rounds and compares the medians. The gate is in the env
block below, overridable with `PYSC_GENERATOR_GATE` for experiments.

## Where the receipt stands (2026-10-23, macOS arm64)

About 19x over ten^8 items: the while loop runs at roughly 0.4 ns per
iteration, the generator loop at roughly 8 ns. The gap decomposes exactly:

- about 6.4 ns per iteration is `ps_try_call` itself: the `sigsetjmp` save,
  the jump-slot bookkeeping and the indirect call into the outline. A
  microbenchmark of the same outline called directly instead of through
  `ps_try_call` runs 5x faster.
- the rest is the frame copy and the guard compare in the generator's
  `__next__`.

The try is there because `__next__` signals completion by raising
StopIteration (CPython's protocol), and `_for_dunder` drains the iterator by
catching it. The exception machinery per iteration is the price of the
protocol.

## What would close the gap, and what it needs

A generator frame method that reports "one value ready" or "done" as a
return value, with the value in a frame field, lets `_for_dunder` drop the
per-iteration try for generator frames. The public `__next__` keeps raising
StopIteration, so the protocol a program sees is unchanged. The measured
shape of that change is a microbenchmark: the poll loop costs about 1.6 ns
per iteration against 8 ns for the try loop, which is inside the 2x gate
with room to spare.

It is not done here because it changes the generator frame's method set, so
every probe that lowers a generator frame gets a new canonical IR hash
(`lang.Yield.function` and `lang.Yield.method` among them). `probes/
IR_HASHES.json` is a frozen gate: regenerating it needs a recorded decision
first and the A/B byte-identical discipline (run `irscore` twice, `cmp` the
outputs).
