# Compile-time receipt: MIR default vs PYSC_MIR=0 vs the v0.7.0 reference

Date: 2026-11-07. Branch: `work/v6a` at `origin/master` (MIR lane is the default since the flip, `MIR_DEFAULT = True`, #1067). Epic: #817. Milestone: v0.9.0. Vertical: V6.1 of `.agents/plans/2026-11-07-post-flip-vertical-plan.md`. Docs only: no source changed, the scorer holdout was never touched.

## Method

Two full runs of `uv run python -m benchmarks.baseline.run --json` on this macOS arm64 host (CPython 3.14.9, matching the recorded reference): once on the MIR default and once with `PYSC_MIR=0` (the escape hatch forcing the direct emitter, `src/pyscriptc/miremit.py`). The v0.7.0 column is `benchmarks/baseline/reference-macos-arm64.json`, recorded 2026-10-24 on this host slot. `compile_ms` is one shot per workload per run: the wall time of `python -m pyscriptc compile --emit bin`, including interpreter startup. Rows marked `*` are flagged single-shot outliers discussed below; the machine ran at load average about 3 during both runs.

## compile_ms per workload

| workload | v0.7.0 ref | PYSC_MIR=0 | MIR default | MIR - direct |
| --- | ---: | ---: | ---: | ---: |
| sum-squares * | 3590 | 847 | 4192 | +3345 |
| floor-sum | 1185 | 784 | 932 | +148 |
| horner | 1246 | 789 | 811 | +22 |
| repeat-len | 1207 | 884 | 818 | -66 |
| math-loop | 1259 | 939 | 788 | -151 |
| re-scan | 1147 | 821 | 789 | -32 |
| json-roundtrip | 1132 | 774 | 780 | +6 |
| url-parse | 1138 | 814 | 803 | -11 |
| b64-roundtrip | 1152 | 779 | 799 | +21 |
| sha256-digest | 1150 | 779 | 810 | +31 |
| struct-pack | 1152 | 769 | 779 | +10 |
| date-add | 1145 | 801 | 820 | +19 |
| time-format | 1162 | 781 | 798 | +18 |
| deque-ring | 1140 | 785 | 775 | -10 |
| reduce-sum | 1134 | 772 | 774 | +2 |
| chain-repeat | 1113 | 785 | 783 | -3 |
| operator-arith * | 1311 | 777 | 2602 | +1825 |
| suppress-loop | 1129 | 779 | 783 | +5 |
| path-join | 1135 | 795 | 799 | +4 |
| fnmatch-filter | 1144 | 779 | 779 | +0 |
| os-files | 1149 | 799 | 792 | -7 |
| pathlib-read | 1170 | 794 | 772 | -22 |
| stat-mode | 1209 | 788 | 806 | +19 |
| warn-loop | 1143 | 772 | 801 | +29 |
| stringio-loop | 1130 | 778 | 777 | -2 |
| sys-const | 1145 | 787 | 777 | -10 |
| ssl-const | 1151 | 793 | 787 | -6 |
| ctypes-sizeof | 1213 | 811 | 815 | +5 |
| builtins-loop | 1152 | 816 | 784 | -32 |
| script-slice | 1151 | 794 | 794 | -1 |
| socket-host * | 1200 | 818 | 1547 | +729 |
| cleandoc | 1168 | 765 | 786 | +20 |
| threads | 1133 | 768 | 778 | +10 |
| log-info | 1355 | 878 | 918 | +40 |
| echo-out | 1138 | 850 | 791 | -59 |
| median | 1151 | 788 | 792 | +4 |

`*` rows: `sum-squares` (MIR run) carried the cold runtime-object cache for this tree (43 units built once, rtcache `miss=43`-class cost); warm recompiles land near 800 ms like every other workload. `operator-arith` (MIR run) re-measures at 870 ms wall warm. `socket-host` (MIR run) re-measures near 800 ms wall warm; its island link adds only about 40 ms over a native link. Excluding the flagged rows the medians are ref 1150, direct 787, MIR 790.

## Reading

The MIR default costs nothing measurable at compile time: median 792 ms vs 788 ms on the direct emitter, a +4 ms delta inside single-shot noise, and the stage table below shows the MIR phases themselves at about 4 ms. Both lanes sit about 31 percent under the v0.7.0 reference on the same host (792 vs 1151 median). The gain since v0.7.0 is not the flip; it is the work that landed between the tag and now on the shared path, mainly `perf(cli): stat-based cache key and lazy compiler imports` (#580, 2026-10-24, same day the reference was recorded) and general lowering-path cleanup. The receipt for the release notes: flipping to MIR did not make compiles slower, and compiles are about a third faster than at v0.7.0.

## Stage profile, `--debug=stages`

Two workloads profiled on the MIR default: `floor-sum` (kernel group) and `socket-host` (island group, links libpython). Numbers are steady-state medians over repeated warm compiles; stage `total` excludes the interpreter and CLI startup of roughly 170 to 190 ms that separates it from the suite's `compile_ms` wall.

| stage (ms) | floor-sum | socket-host |
| --- | ---: | ---: |
| parse | 0.1 | 0.1 |
| lower | 1.3 | 1.4 |
| check (pyrefly preflight) | 300 | 305 |
| validate | 0.1 | 0.1 |
| emit_llvm | 3.1 | 2.2 |
| cc_compile (.ll -> .o) | 75 | 74 |
| cc_link | 105 | 110 |
| link residual (rtcache keying) | 125 | 130 |
| total | 610 | 680 |

The dominant stages are `check` and `link`, at roughly 300 ms each of a ~610 ms stage total; `lower` plus `emit_llvm` (the MIR lane proper) is under 5 ms combined. Inside `check`, about 160 ms is `_patched_typeshed` doing `shutil.copytree` of the 751-file, 4.7 MB vendored typeshed into a fresh tempdir plus the builtins patch, on every compile; the rest is the pyrefly subprocess itself. Inside `link`, the residual over `cc_compile` + `cc_link` is about 130 ms warm, dominated by the per-compile `cc --version` compiler-identity probe in `rtcache.key_for` plus hashing the 43 runtime objects; the residual spikes above 1 s under parallel load because it is subprocess- and stat-bound. `socket-host` costs about 40 to 70 ms more on `cc_link` for libpython.

## Ranked cheap wins

1. Cache the patched typeshed content-addressed next to the rt objects (`~/.cache/pyscriptc/typeshed-<digest>`), rebuilt only when the vendored tree or the patch anchors change: about 160 ms per compile, the single largest removable cost, same pattern `rtcache.py` already uses.
2. Persist `compiler_identity` in the cache dir keyed by resolved binary path plus mtime instead of running `cc --version` every compile: about 30 to 130 ms inside the link residual, a small patch in `rtcache.py`.
3. Overlap `check` with `emit_llvm` and the link prep for non-fallback compiles: pyrefly needs only the source file, not the module, so the subprocess can run concurrently and join before the verdict is reported; hides about 140 ms on a warm cache. The boxed `plane_map` path keeps the serial order.
4. Write `ir.json` only when asked (`--debug-dir`, `--emit ir`, or the V6.4 dump-format decision): today `json.dumps(module, indent=2)` runs unconditionally per compile; trivial on a 39-node kernel, real money on selfhost-size modules.
5. Audit the remaining ~170 ms of CLI startup and teardown with `python -X importtime -m pyscriptc compile`: #580 already took lazy imports partway; the residue is argparse wiring, deferred compiler imports and process exit. Medium effort, caps the fixed floor every compile pays.

Artifacts: suite runs at `/tmp/v6a-mir.json` and `/tmp/v6a-direct.json`, stage probes under `/tmp/v6a-*`.
