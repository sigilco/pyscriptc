# Nightly gates clock for issue #830

Issue #830 records the decision to retire IR_HASHES.json and the dict-shape canonical rules in favor of the streaming TIR gate (TIR_HASHES.json) and the stage-2 fixed-point lane. The retirement criteria approved in roadmap decision 2026-11-02 require two calendar weeks of green gate runs on master, or one real nondeterminism caught.

The clock starts today on 2026-11-04 with the introduction of the nightly-gates workflow (.github/workflows/nightly-gates.yml). The workflow triggers automatically every day at 03:17 UTC via cron and can also be triggered manually via workflow_dispatch on master.

Accrual requires 14 consecutive green nightly runs on master. Both the TIR gate (probes/TIR_HASHES.json via tirscore) and the stage-2 fixpoint lane (benchmarks.compat2.fixpoint) must report success for a run to be considered green. Any red run, whether from hash divergence or module fixpoint nondeterminism, resets the consecutive counter back to zero.

Every run records its outcome to the GitHub Actions job summary and uploads an artifact named nightly-gate-result. The artifact contains exactly one line with four space-separated fields: run date (UTC YYYY-MM-DD), commit SHA, TIR gate outcome (success or failure), and fixpoint outcome (success or failure).

Inspect recent runs and audit the clock with the GitHub CLI using gh run list --workflow nightly-gates.yml. To view or download the gate receipt for a specific run, use gh run download <run-id> --name nightly-gate-result and examine the nightly-gate-result.txt file. Once 14 consecutive green runs have accrued without interruption, the owner decision on #830 can be finalized to retire IR_HASHES.json.
