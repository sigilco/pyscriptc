# Test Strategy: Complexity Guards and Adversarial Testing

Date: 2026-10-25. Scope: Test strategy for pyscriptc typed subset compiler, stabilizing CI scaling flakiness (#388), and evaluating adversarial testing strategies across PR, Nightly, and release gates.

## 1. Root Cause Analysis: Flaking in `test_data_scaling.py::test_data_operation_stays_linear[list_append]`

The test `test_data_operation_stays_linear[list_append]` flakes consistently on CI shard 1 (macos-14 runner under `macos-safe` shard 1/3). Observed failure logs record `times` dictionaries such as `{4000: 2.57, 16000: 2.23}`, `{4000: 2.21, 16000: 2.29}`, and `{4000: 2.7, 16000: 2.8}`.

### 1.1 The Failing Assertion Is the Floor, Not the Exponent

In `tests/test_data_scaling.py`, the test specifies `FLOOR_MS = 3.0` and executes:
```python
assert times[size] > FLOOR_MS or times[size * 4] > FLOOR_MS, times
assert exponent < bound, f"{name}: exponent {exponent:.2f} >= {bound} (times {times})"
```
In every observed failure, both `times[4000]` and `times[16000]` are strictly below `3.0 ms`. Line 150 (`times[size] > FLOOR_MS or times[size * 4] > FLOOR_MS`) evaluates to `False or False`, raising an immediate `AssertionError: times`. The exponent assertion on line 151 is never reached.

### 1.2 Timer Measurement and Process Lifecycle Noise

The timer function `_timed(binary: Path)` measures `elapsed = (time.perf_counter() - start) * 1000.0` outside `subprocess.run([str(binary)])`. This records the full operating system process lifecycle: `posix_spawn`, dynamic linker resolution (`dyld` on macOS), runtime initialization (`ps_init`, `ps_argv_init`), stdout pipe creation, process teardown, and Python's `waitpid`.

Empirical local measurements on Apple Silicon (CPython 3.14, 25 samples per configuration) demonstrate that process lifecycle overhead completely dwarfs the workload execution time:
- Empty compiled binary (`def bench(n): return n`) spawn baseline: min 3.55 ms, median 3.93 ms, p90 4.12 ms.
- `list_append` at N=4000: min 3.52 ms, median 3.76 ms, p90 4.28 ms (net median over baseline: -0.17 ms).
- `list_append` at N=16000: min 3.70 ms, median 3.87 ms, p90 4.21 ms (net median over baseline: -0.06 ms).
- `list_append` at N=50000: min 4.22 ms, median 4.60 ms, p90 4.90 ms (net median: +0.66 ms).
- `list_append` at N=100000: min 4.83 ms, median 5.24 ms, p90 6.56 ms (net median: +1.30 ms).
- `list_append` at N=200000: min 6.61 ms, median 7.03 ms, p90 7.63 ms (net median: +3.10 ms).

In compiled native code, appending 4000 integers to a pre-sized or dynamically resized contiguous array executes in approximately 10 to 20 microseconds of CPU time. At N=16000, execution takes 40 to 80 microseconds. The workload represents less than 2% of the measured wall-clock duration; the remaining 98% is operating system spawn latency. On an unloaded macOS runner core in CI, process spawn finishes in 2.2 to 2.8 ms, immediately tripping the arbitrary 3.0 ms floor.

### 1.3 Sample Count and Aggregation Flaw

The test executes 3 samples per binary and selects `best = min(best, elapsed)`. Taking the minimum of a tiny sample filters specifically for the trial with the lowest OS scheduling delay. When a runner core experiences an idle window, `min()` reliably drops below 3.0 ms. Furthermore, taking ratios of two near-constant spawn times means that if N=4000 experiences 2.2 ms and N=16000 experiences 2.8 ms, the calculated exponent becomes `math.log(2.8 / 2.2) / math.log(4) = 0.17`, measuring spawn noise rather than algorithmic scaling.

### 1.4 GC and Allocator Effects

Pyscriptc contains no garbage collector. Runtime memory management in `src/pyscriptc/runtime/ps_rt.c` uses direct libc `malloc`, `realloc`, and `free`. `ps_seq_push` doubles capacity (`cap = cap == 0 ? 4 : s->cap * 2`). At N=4000, exactly 11 reallocations occur; at N=16000, exactly 13 reallocations occur. Total allocated payload is 32 KB at N=4000 and 128 KB at N=16000. Both sizes fit entirely within CPU L1/L2 caches and incur zero page faults after initial stack and heap segment setup.

### 1.5 Git History and Calibration Gaps

Git log reveals that `tests/test_data_scaling.py` was introduced in commit `5860541ecdadfa6c94b40d776dec607aa881ff0b` (PR #464, issue #388) alongside the map and set index overhauls. The dictionary and set test cases (`dict_grow`, `set_grow`, `dict_lookup`) were parameterized at N=20000 and 4N=80000 because map operations carry hashing and collision handling overhead that pushes wall time past 6-12 ms. `list_append` was added as a control but set to N=4000 without prior runner calibration, leaving it vulnerable to sub-floor execution on fast cores.

## 2. Breaking Wall-Clock Exponent Assertions into Non-Flaky Checks

Wall-clock assertions in shared multi-tenant CI runners suffer from core frequency throttling, background hypervisor steals, and process creation jitter. Four architectures address this:

### 2.1 Deterministic Operation Counting (Runtime Counters)

The most rigorous guard for algorithmic complexity is deterministic operation counting. The runtime can expose atomic or thread-local counters under a test configuration (e.g., `-DPYSC_TEST_COUNTERS=1`):
- `ps_counter_allocs`: Total count of heap allocations.
- `ps_counter_reallocs`: Count of reallocation resizing events.
- `ps_counter_bytes_allocated`: Peak and cumulative memory bytes requested.
- `ps_counter_seq_pushes`: Logical push operations.

For `list_append`, an amortized O(1) dynamic array implementation must satisfy exact deterministic invariants:
- At N=4000: `ps_counter_reallocs == 11` (capacities 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096).
- At N=16000: `ps_counter_reallocs == 13` (capacities up to 16384).
- Allocation ratio invariant: `reallocs(4N) - reallocs(N) == 2` for geometric doubling.
- If an accidental O(N^2) reallocation bug is introduced (e.g., allocating `len + 1` each push), `reallocs` explodes from 13 to 16,000, failing the assertion deterministically in under 1 millisecond.

### 2.2 Instruction Counts (Valgrind / Perf)

Instruction counting via `valgrind --tool=callgrind` or `perf stat -e instructions` produces deterministic retired instruction counts regardless of CPU clock rate:
- Limitations: Valgrind adds a 20x to 50x execution slowdown, does not run natively on macOS Apple Silicon runners, and is not available in macOS CI shards. `perf stat` requires PMU access and `sysctl kernel.perf_event_paranoid <= 1`, which is blocked inside standard GitHub Actions container sandboxes.
- Verdict: Instruction counting is a valuable offline diagnostic tool for regression triage on Linux developer workstations, but cannot serve as a portable cross-platform PR gate.

### 2.3 Hardened Statistical Layer for Wall-Clock Scaling

Where wall-clock timing is retained in integration tests, three sound statistical principles must be applied:
1. Workload sizing: N must be chosen such that native execution time is at least 3x to 5x higher than process startup latency (N >= 50,000 for `list_append`, yielding 4N=200,000 with > 7 ms execution).
2. Central tendency: Replace `min()` with the median of 5 runs. The minimum selects for abnormal low-latency spawn anomalies; the median filters both low-end spawn blips and high-end context switch spikes.
3. Floor relaxation: If both runs are fast, the floor check must verify whether the larger run reached measurable headroom (`times[size * 4] > FLOOR_MS`), or adjust `FLOOR_MS` dynamically based on the measured spawn baseline.

### 2.4 Dividing CI Responsibilities: Fast PR Guards vs. Nightly Gate

Issues #388, #390, and #391 describe a three-tier performance and complexity verification architecture:
- Tier 1 (PR CI): Fast complexity guards in `tests/test_data_scaling.py`. Run calibrated workloads with median-of-5 timing or deterministic counters, bounding the exponent to catch O(N^2) regressions without blocking PRs on sub-millisecond noise.
- Tier 2 (Nightly Baseline Gate, #390): `benchmarks/baseline/run --check` runs on dedicated Ubuntu-24.04 Linux instances, testing RSS ratios and pyscriptc/CPython time ratios against `benchmarks/baseline/reference.json` with a 2.0x regression limit.
- Tier 3 (Nightly Scaling Benchmarks, #391): `benchmarks/scaling/run.py` runs full multi-size curves across numeric and data workloads, flagging `SUPERLINEAR` regressions to the `nightly-failure` issue tracker without gatekeeping PR merges.

## 3. Adversarial Assessment of Testing Strategies

To prevent compiler miscompilations, security regressions, and performance defects within the typed subset (where island is the floor and native is the ceiling), we assess seven adversarial testing strategies.

### 3.1 Property-Based Differential Testing (Hypothesis)

- Description: Differential execution of typed Python code snippets between compiled pyscriptc native binaries and CPython 3.14 oracle, generating random numeric boundaries, UTF-8 strings, and nested sequence operations.
- Expected Bug Classes Caught: Integer overflow/truncation in 64-bit conversions, unicode slicing/indexing boundary errors in `ps_str` (codepoint vs byte offset differences), empty slice edge cases, negative indexing wrap-around bugs, off-by-one errors in sequence iteration, string format specifier handling.
- Cost in CI Minutes and Memory: Low. With `max_examples=25` or `30` per property, a targeted test file runs in 8 to 15 seconds. Memory footprint is under 80 MB, well within the 8 GB laptop and CI limits.
- Where It Plugs In: `tests/test_differential_hypothesis.py` in the standard pytest unit suite (runs in `unit` and `macos-safe` shards).
- Verdict: **Adopt Now (Pilot)**. Highest bug discovery value per CI minute. Safe, small, and requires zero modifications to frozen compat2 gates.

### 3.2 Fuzzing the Parser and Lowerer (Must-Fence Corpus)

- Description: Fuzzing AST parsing and lowering in `src/pyscriptc/compiler/` using random grammar-directed syntax generation (e.g., via Atheris or Grammarinator) to detect uncaught exceptions, infinite loops, and compiler crashes.
- Expected Bug Classes Caught: Unhandled AST syntax nodes raising raw `NotImplementedError` or unhandled exceptions instead of proper compiler diagnostic fences, recursive lowering stack overflows, compiler panics on malformed type annotations.
- Cost in CI Minutes and Memory: High. Continuous fuzzing requires dedicated CPU hours and high memory churn. Running an active fuzzer inside 15-minute CI shards is infeasible.
- Where It Plugs In: A seed regression test suite (`tests/test_fuzz_regressions.py`) in CI that validates a fixed corpus of tricky edge cases; long-running fuzzing runs as an external continuous or nightly job.
- Verdict: **Pilot (Corpus Only)**. Adopt a checked-in regression corpus in CI; defer active fuzz execution to out-of-band infrastructure.

### 3.3 Table-Driven Differential Tests (Call-Table and Stdlib Surface)

- Description: Systematic matrix testing of all registered signatures in `src/pyscriptc/compiler/calltab.py` against standard library functions (`math`, `os`, `shutil`, `sys`, `pathlib`).
- Expected Bug Classes Caught: Signature drift between compiler type signatures and CPython 3.14 stdlib implementations, parameter conversion type errors, incorrect return type unboxing.
- Cost in CI Minutes and Memory: Very low. Compilation and execution of parameterized table rows completes in under 10 seconds with negligible memory footprint (< 50 MB).
- Where It Plugs In: `tests/test_stdlib_call_table.py`.
- Verdict: **Adopt Now**. Highly deterministic, catches stdlib contract divergence before user-facing failures occur.

### 3.4 Metamorphic Testing

- Description: Applying semantics-preserving program transformations (e.g., renaming local identifiers, reordering independent statements, introducing dead variables) and asserting byte-identical or functionally identical output between compiled variants.
- Expected Bug Classes Caught: Lowerer symbol table pollution, register allocation bugs in LLVM emission, variable shadowing defects in nested blocks, invalid dead code elimination in `src/pyscriptc/emit/`.
- Cost in CI Minutes and Memory: Moderate. Compiling 2-3 variants per program doubles or triples compilation count (adds 30-45 seconds in CI).
- Where It Plugs In: `tests/test_metamorphic.py` targeting expression and statement lowering.
- Verdict: **Defer (v0.8.0)**. Pyscriptc relies heavily on standard LLVM optimization passes; lowerer correctness is currently better validated via direct differential testing against CPython.

### 3.5 Mutation Testing of Soundness-Critical Modules

- Description: Automated fault injection (using tools like `mutmut`) modifying operators and conditional checks in core compiler modules (`restlower.py`, `typefacts.py`, `checker_adapters.py`) to verify test suite fault detection capability.
- Expected Bug Classes Caught: Gaps in test assertion strictness, dead branches in type inference, untested exception paths.
- Cost in CI Minutes and Memory: Prohibitive in CI. Running hundreds of mutation trials requires hours of compute and exhausts CI timeouts.
- Where It Plugs In: Offline developer auditing script (`scripts/audit_mutations.py`).
- Verdict: **Reject for CI; Defer for Offline Audits**. Unsuitable for pull request or nightly integration.

### 3.6 Sanitizer Coverage Expansion (ASan / UBSan / TSan)

- Description: Expanding address, undefined-behavior, and thread sanitizer compilation across broader runtime test suites.
- Expected Bug Classes Caught: Use-after-free, buffer overruns in `ps_grow` and `ps_buf`, unaligned pointer access in `ps_seq` and `ps_map`, signed integer overflow, data races in multithreaded runtime scripts (`src/pyscriptc/runtime/ps_thread.c`).
- Existing State and Gaps: CI currently runs `tsan-smoke` and `sanitize-smoke` on Ubuntu with clang for isolated thread and memory scripts. However, full test suites are not executed under ASan/UBSan, and macOS runners have no sanitizer coverage.
- Cost in CI Minutes and Memory: 2x to 3x execution slowdown, 2x memory overhead during linking and execution.
- Where It Plugs In: Dedicated nightly sanitizer matrix job on Linux.
- Verdict: **Defer (v0.8.0)**. Keep smoke checks in PR CI; schedule comprehensive sanitizer suite for nightly workflows.

### 3.7 Golden-IR Tests (IR_HASHES)

- Description: Golden hash verification of emitted LLVM IR strings across compiler stages.
- Expected Bug Classes Caught: Unintended changes to emitted LLVM IR structure.
- Existing State: `probes/IR_HASHES.json` already exists and is locked under frozen v2 gates (tag `gates-v2`, 2026-10-16). AGENTS.md mandates that changes require recorded architectural decisions and byte-identical verification on Linux; regenerating hashes on macOS is strictly prohibited.
- Verdict: **Reject (Do Not Expand)**. Existing golden gates must remain strictly frozen. Expanding golden IR tests outside frozen gates creates high maintenance overhead without semantic guarantees.

## 4. Summary Matrix of Testing Strategies

| Strategy | Target Bug Classes | CI Runtime / Memory Cost | Target Shard / Workflow | Verdict | Reason |
|---|---|---|---|---|---|
| Property-Based (Hypothesis) | Numeric overflow, string slicing, sequence edge cases | +10-15s / < 100 MB | PR `unit` & `macos-safe` | **Adopt Now (Pilot)** | High defect yield, low cost, fully deterministic with fixed seed. |
| Table-Driven Call-Table | Stdlib signature mismatch, type unboxing errors | +5-10s / < 50 MB | PR `unit` | **Adopt Now** | Eliminates stdlib contract drift against CPython 3.14. |
| Hardened Scaling Guards | Accidental O(N^2) data algorithm regressions | Net neutral (< 1s) / < 50 MB | PR `macos-safe` | **Adopt Now** | Eliminates list_append CI flake while preserving complexity assertion. |
| Must-Fence Fuzzing Corpus | Lowerer panics, uncaught AST syntax exceptions | +15-20s / < 100 MB | PR `unit` (corpus only) | **Pilot** | Checked-in seed corpus in PR; continuous fuzzing in external job. |
| Metamorphic Testing | AST lowerer symbol pollution, shadowing bugs | +30-45s / ~150 MB | Nightly / PR | **Defer (v0.8.0)** | High compile-time cost; differential testing is more urgent. |
| Sanitizer Expansion | Memory safety, UB, thread races in runtime C | 2-3x job duration / 2x RAM | Nightly Linux | **Defer (v0.8.0)** | Too heavy for PR CI; ideal for dedicated nightly runs. |
| Mutation Testing | Weak test assertions, untested compiler branches | > 60 min / High | Offline developer audit | **Reject for CI** | Unviable in CI shards; useful only for periodic local audits. |
| Golden-IR Expansion | Accidental IR changes | Zero runtime cost | Gate probes | **Reject** | Frozen gates policy (AGENTS.md); fragile across toolchains. |

## 5. Phase 2 Implementation Plan

Following the small and safe principle:
1. **Fix `list_append` flake in `tests/test_data_scaling.py`**:
   - Update `list_append` workload size from N=4000 to N=50000 (4N=200,000), matching the robust scale of dictionary and set operations.
   - Upgrade `_timed` to collect 5 samples and take the median value, filtering both OS spawn anomalies and scheduler spikes.
   - Guard the floor check so that fast runners do not trigger false-positive assertions when execution finishes cleanly.
   - Verify 20 consecutive passes locally under `pytest -n 2`.
2. **Implement Hypothesis Differential Pilot (`tests/test_differential_hypothesis.py`)**:
   - Test core integer and string operations (addition, multiplication, bitwise shifts, string slicing, concatenation, indexing) lowered to native binary versus CPython 3.14 oracle.
   - Enforce bounded execution (`max_examples=25`), fixed deterministic seed, and timeout=60 on compiled binary runs.
   - Add `hypothesis` to `[dependency-groups] dev` in `pyproject.toml`.
3. **Follow-Up Issues (Milestone v0.8.0)**:
   - File follow-up for table-driven stdlib call-table test matrix.
   - File follow-up for continuous must-fence fuzzer and nightly sanitizer test expansion.
