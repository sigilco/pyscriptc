# C7 sweep: calibration rerun on the 3.12 corpus

Verdict: full-width calibration passed; every category cell has at least 10 sampled rows; headroom is clean with 0 capped rows.

## Method

Ran `python -m benchmarks.compat2.probes.calibrate --workers 5 --out /tmp/c7/CALIBRATION.json --cache /tmp/c7/cal.cache.jsonl` under the 3.12 oracle interpreter (cpython-3.12.15, `PYSC_ORACLE_DIR=~/oracles`), off-container on Linux, branched off 823654f. Output went to /tmp so `probes/CALIBRATION.json`, `TRIAGE.md` and `TIMINGS.md` were never written.

## Results

14925 probes classified in about 19 minutes of wall time: pass=2328 fence=11442 mention=1153 build-error=1 wrong-output=1.

All 13 category cells hold at least 26 sampled rows, well over the 10-row floor: builtins/class 26, builtins/constant 38, builtins/exception 69, builtins/function 77, builtins/method 221, language/language 440, modules/module 192, surface/class 733, surface/constant 4689, surface/exception 164, surface/function 2989, surface/method 5103, surface/module 184. No stop-and-report flag.

Three finer outcome-kind splits sit under 10 rows but are not category cells and carry no exit criterion: builtins/class/raises=1, builtins/method/raises=2, surface/exception/mention=5. Flagging for awareness only.

`python -m benchmarks.compat2.headroom benchmarks/compat2/probes` exits 0: 13692/13692 weighted rows reachable, 0 capped.

## Delta vs committed CALIBRATION.json

The committed calibration was generated under a 3.14.8 interpreter on a different probe set (15518 rows); this rerun is the first full-width pass under the pinned 3.12 oracle on the current corpus (14925 rows). The totals shift is mostly mechanical from the corpus change: pass 2660 to 2328, fence 11644 to 11442, mention 1212 to 1153.

v1 continuity counters moved with the oracle pin: v1_equivalent_not_passing 0 to 163, v1_lowered_v2_probe_not_passing 190 to 357, v1_lowered stable at 4. Expected direction under a different oracle version, but worth a maintainer look before re-weighting.

## Guard compliance

No writes under benchmarks/compat2/probes/, no frozen-list edits, no merges or tags. Report-only.
