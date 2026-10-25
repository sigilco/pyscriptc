# Dogfood assessment: 8 sibling project scripts for v0.7.0 weekly gate

Date: 2026-10-25. Scope: Dogfood session evaluating 8 real CLI tools, automation scripts, and data munging utilities from sibling projects under `~/Documents/prjcts/_own/`.

Goal: find what stops the owner from using pyscriptc weekly on real code (v0.7.0 gate).

## Selected scripts

1. `fx/scripts/binary_size.py` (312 lines): Binary size checker comparing targets against baseline and thresholds (argparse, hashlib, json, pathlib).
2. `brioso/scripts/validate.py` (409 lines): Repository validation script checking files, assets, and project configurations (argparse, json, os, re, shutil).
3. `brioso/scripts/check-deploy-secrets.py` (64 lines): Deployment secret validator checking environment keys and templates (os, sys, pathlib, yaml).
4. `maki/scripts/tool_token_analysis.py` (424 lines): Tool token analyzer aggregating agent logs and metrics (json, sys, collections, pathlib, typing).
5. `tinyharness/practice/parse_tickets.py` (87 lines): Ticket text and CSV parsing utility (csv, re, pathlib, sys).
6. `agentplugins-caveman/plugins/caveman/skills/caveman-compress/scripts/cli.py` (85 lines): Prompt compression CLI utility (argparse, sys, pathlib).
7. `HTLM/data/pipeline/split.py` (59 lines): Dataset splitting script for training/test splits (json, random, sys, pathlib).
8. `wowplay-app/tools/launch-diagnostics/classify.py` (117 lines): Diagnostic log classification utility (argparse, json, re, sys, pathlib).

## Methodology

For each script:
1. Run `uv run pyscriptc assess PATH --json` to inspect lowering status and root causes.
2. Run `pyscriptc run` or `compile` on it.
3. Run `pyscriptc verify` where it compiles.
4. Test island route if native compilation halts at fences.
5. Cluster root cause blockers across scripts and file or comment on GitHub issues in milestone 4 (v0.7.0).
