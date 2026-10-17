# E1 boundary report

Milestone 5 close-out for epic E1 (entry-level un-fencing). All numbers are
read-only runs on master at `81adb47` (post-C25 regen) unless noted.

## Flip hit rate

`uv run python benchmarks/compat2/flipcheck.py /tmp/predicted.json /tmp/achieved-c25.txt`

C12's predicted-flip list (records with `predicted_flip` on the merged
detector): 2 scripts.

- Hits: `scripts.fib_recursive`, `scripts.word_freq` — 2/2, hit rate 1.0
  (threshold 0.8). Verified directly: all 20 corpus scripts were compiled on
  master; exactly these two go FENCED to OK.
- Misses: none.
- Surprises: `builtins.print`, `builtins.list.append`, `lang.Expr.function`,
  `lang.Expr.method` — the four sanctioned E1 flips, already landed before C12
  recorded its predictions. Plus 453 stdlib-constant probes (ctypes, errno, os,
  socket, ssl, stat, sys, time) whose hashes moved with the oracle stamp
  `2/cpython-3.14.7` -> `2/cpython-3.14.8` on the regenerating interpreter.
  All expected; nothing unexplained.

Note: corpus scripts are not hashed probes, so the achieved input combined the
irscore CHANGED list with the two script ids verified by direct compilation.

## Score before/after

`uv run python -m benchmarks.compat2.score --corpus --out <f> --workers 8`
on tag `gates-v2` (pre-E1, `68fb928`) and master.

- gates-v2: 106 probes, pass 3 (`top100.certifi`, `top100.trove-classifiers`,
  `top100.tzdata`), weighted pass 15.0.
- master: 106 probes, pass 5 (same three plus `scripts.fib_recursive`,
  `scripts.word_freq`), weighted pass 21.0.

Script verdicts at weight 3.0: 0/20 -> 2/20. Delta +6.0 weighted.

## Boxed ledger

`uv run python benchmarks/compat2/boxed_cost.py --window-commits 10`

- window 0 (`15e607c..81adb47`): code 1.3%, churn 0.0%, bugs 0.0% (0/1), tax 0.0%
- window 1 (`ce2b088..efd0ba8`): code 1.3%, churn 0.0%, bugs 0.0% (0/0), tax 0.0%
- window 2 (`a9e7139..4243f72`): code 1.3%, churn 0.0%, bugs 0.0% (0/0), tax 0.0%

drop_flag: false. Every AGENTS.md threshold is clear: churn and bugs flat at
zero in all windows, tax at zero, boxed code share stable at ~1.3%.

## Suite health

- `uv run pytest -q tests -m "not linux_container"` on final master:
  357 passed, 7 skipped.
- `uv run python -m benchmarks.baseline.run --check`: within reference
  (`benchmarks/baseline/reference.json`).
- irscore self-check after the C25 regen: `baseline matches` (oracle
  `2/cpython-3.14.8`).
- Self census: 22 modules, 663 fences, 12,608 lines, all within pinned bounds.
- GitHub Actions CI is currently not running on PRs (account billing), so the
  numbers above are the local gate equivalents from the C27 runbook.

## Fixes folded in during the boundary

- `test_fences_stay_closed` kept a stale `text[::-1]` fence case after the
  negative-step merge (#46).
- `format_global` emitted duplicate `@ps.fmt.*` globals for same-name
  different-data formats; clang rejected the redefinition and `--run`
  compiles failed (`test_spike` red on master). Fixed by symbol-level dedupe
  with `.N` suffixes (#47).

## Container suite

TODO (user lane): the `linux_container`-marked tests (clang assembly, spike
binaries, the census container run) need a Linux container host. This box ran
the `test_spike` clang tests natively and they pass after #47, but the
container-marked suite as a whole was not run here (171 deselected).
