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

## Per-script results

| Script | Lines | Category | Lowers | First Blocker | Root Cause Class | Island Route Viable | Island Route Worked End to End |
|---|---|---|---|---|---|---|---|
| `fx/scripts/binary_size.py` | 312 | CLI tool / automation | partly (3/13 native, 10 blocked, 55 fences) | Preflight exit 2 (object bad-argument-type for int), PS1104 `dict[str, object]`, PS2003 `hashlib.sha256()`, PS2001 `Path.open` | `dynamic-annotation` / `type-mismatch` / `field-lookup` | No (only `collections.abc` routes; 52 script fences stay blocked) | No (exit 2 preflight, exit 3 compile halt) |
| `brioso/scripts/validate.py` | 409 | CLI / repo validator | partly (1/9 native, 8 blocked, 53 fences) | PS2002 `concurrent.futures`, PS2001 `datetime.timezone`, PS1106 bare `dict`, PS1001 unannotated `ROOT = Path(...)`, PS2003 `subprocess.run(cwd=...)` | `untyped-import` / `stdlib-partial` / `unlowered-type-name` / `keyword-args` | No (only `concurrent` routes; 52 script fences stay blocked) | No (exit 3 compile halt on PS2001/PS1106) |
| `brioso/scripts/check-deploy-secrets.py` | 64 | CLI / secret validator | refused (0/1 native, 1 blocked, 13 fences) | PS1001 `yaml.safe_load needs the result annotation`, PS1001 `REPO_ROOT needs an annotation before use` | `local-inference` / `annotation-other` | No (yaml import routes, but dynamic return value requires explicit annotation) | No (exit 3 compile halt on PS1001) |
| `maki/scripts/tool_token_analysis.py` | 424 | Data munging / metrics | refused (0/17 native, 17 blocked, 32 fences) | PS1001 `parameter text needs an annotation`, `load_sessions needs a return annotation` | `local-inference` / `unannotated-function` | No (imports are 100% native; blockers are unannotated user function signatures) | No (exit 3 compile halt on PS1001) |
| `tinyharness/practice/parse_tickets.py` | 87 | Data munging / parser | refused (0/3 native, 3 blocked, 5 fences) | PS1004 `parse_row can fall off the end and return None`, PS2003 `csv.reader(delimiter=...)`, `enumerate(start=...)` | `fall-off-end` / `keyword-args` / `construct-other` | No (imports are 100% native; blockers are control-flow return and stdlib keyword arguments) | No (exit 3 compile halt on PS1004/PS2003) |
| `agentplugins-caveman/.../scripts/cli.py` | 85 | CLI tool | partly (1/2 native, 1 blocked, 14 fences) | PS2002 `relative import is not lowered`, PS2001 `sys.stdout is not in lowered subset`, PS2002 `formatting Path is not lowered` | `relative-import` / `stdlib:sys` / `construct-other` | No (relative intra-package imports cannot route to island; Path formatting stays blocked) | No (exit 3 compile halt on PS2002) |
| `HTLM/data/pipeline/split.py` | 59 | Data pipeline | refused (0/5 native, 5 blocked, 13 fences) | PS1104 `Any is not a value type in the IR`, PS1101 `union types are not in the IR` (`str \| Path`), PS2003 `argparse.ArgumentParser(description=...)` | `dynamic-annotation` / `union-types` / `keyword-args` | No (routing records import leaves Any, unions, and keyword arguments blocked) | No (exit 3 compile halt on PS1104/PS1101) |
| `wowplay-app/tools/.../classify.py` | 117 | CLI log classifier | refused (0/3 native, 3 blocked, 11 fences) | PS1001 `REPO_ROOT needs an annotation before use`, PS1106 bare `dict`, preflight exit 2 (`str has no attribute search`) | `local-inference` / `unlowered-type-name` / `type-preflight` | No (imports are 100% native; blockers are module globals and bare container annotations) | No (exit 3 compile halt on PS1106) |

## Top 5 blocker clusters and issues

1. Module-level unannotated variable assignments (PS1001 / PS2002): Commented on open issue [#502](https://github.com/sigilco/pyscriptc/issues/502).
2. `argparse.Namespace` dynamic attribute reads (PS2001): Commented on open issue [#510](https://github.com/sigilco/pyscriptc/issues/510) and PR [#565](https://github.com/sigilco/pyscriptc/pull/565).
3. Standard library call-table keyword arguments for `csv.reader(delimiter=)`, `enumerate(start=)`, `subprocess.run(cwd=)` (PS2003): Filed issue [#595](https://github.com/sigilco/pyscriptc/issues/595) in milestone 4 (v0.7.0).
4. F-string formatting and string interpolation for `Path`, `Exception`, and `Optional` types (PS2002): Filed issue [#596](https://github.com/sigilco/pyscriptc/issues/596) in milestone 4 (v0.7.0).
5. Island fallback floor failure on unannotated call returns and intra-script constructs (PS1001 / PS2002): Filed issue [#597](https://github.com/sigilco/pyscriptc/issues/597) in milestone 4 (v0.7.0).

Also commented on open issue [#483](https://github.com/sigilco/pyscriptc/issues/483) for bare container type annotations (`dict` / `list`).
