# MIR flip scorer parity receipt

Date: 2026-11-07. Branch: `work/scorer-receipt`. Scored tree: `079d733e`, `origin/master` at the start of the run. Epic: #817. Milestone: v0.9.0. Scope: visible compat2 corpus only, through `scripts/compat_mir_flip.py` with its default `benchmarks/compat2/probes --corpus` input; the closed evaluation set was not read or referenced.

Ran `uv run python scripts/compat_mir_flip.py --out-dir /tmp/fr-shard-I -- --shard I/8` for I=0..7 with at most two shards in parallel on the mise Python 3.14.9 venv and ziglang 0.17.0. The script pinned `PYSC_MIR` per lane and forced `--no-cache`; every shard exited 0 and wrote both lane shard JSONs. `benchmarks.compat2.score.merge_results` merged the eight direct and eight MIR shard files into `/tmp/fr-merged/direct.json` and `/tmp/fr-merged/mir.json`, and `scripts/compat_no_flip.py` compared those merged files.

Per-shard end-to-end wall times with direct and MIR lane scorer wall times were: shard 0 244s (direct 126.613s, MIR 115.071s, 1725 probes), shard 1 230s (119.391s, 108.253s, 1725), shard 2 237s (108.958s, 124.864s, 1725), shard 3 255s (122.612s, 130.086s, 1725), shard 4 237s (119.323s, 114.981s, 1725), shard 5 237s (120.268s, 114.158s, 1725), shard 6 276s (132.708s, 140.058s, 1724), and shard 7 257s (129.600s, 124.800s, 1724). The eight-shard run took 999s end to end.

The merged comparison covered 13,798 probes on each lane with oracle `2/cpython-3.14.9`. Direct and MIR both reported 1,648 passes, weighted pass 944.0 of 9,786.7 (9.65%), and identical outcome counts: 10,730 fence, 1,409 excluded, 9 build-error and 2 wrong-output.

The comparison reported 0 outcome flips in either direction, with empty `a_to_b` and `b_to_a` lists and no missing rows. There are no flip probe ids and no root causes to investigate; in particular `lang.Constant.none.loop`, the MIR build-error flip from the earlier dry run, now passes on both lanes. No MIR-lane bug was found, so no minimal repro is owed.

Gate item 2, scorer parity, now has a full visible-corpus receipt: 0 outcome flips in both directions on 13,798 probes.
