# Real scripts corpus

The R0 rung of the adoption ladder: third-party real-world scripts, the
population a beta tester brings. The existing `scripts_corpus` is the R1
rung (original single-file CLIs written for this corpus); this directory
holds scripts that were written by other people for other reasons, measured
exactly as they are.

## Rules

- Every file is vendored from a public repository at a pinned commit, with
  the upstream licence copied into `licenses/`. `manifest.json` records the
  repository, commit, path, URL and sha256 for every script.
- No edits. A script that fails to parse under CPython 3.14 is dropped at
  selection time; everything else is measured as-is. A fence the corpus
  finds is a real gap, not a fixture bug.
- Selection targets the script profile the beta bar claims: single-file or
  small multi-file CLI and automation scripts over the builtin and stdlib
  surface (argparse, pathlib, json/csv, subprocess, os/sys, re, datetime,
  logging, shutil, urllib, dataclasses, typing, itertools/functools/
  collections). GUI toolkits, notebooks, and scripts whose imports are
  outside the target surface (requests, numpy, selenium, cryptography and
  friends) are excluded: they measure the packaging track, not the script
  grind.

## Layout

| Path | What it holds |
| --- | --- |
| `scripts/<slug>.py` | one vendored script, byte-identical to the pinned upstream file |
| `licenses/` | the upstream licence texts, one per source repository |
| `manifest.json` | the provenance record: repo, commit, path, URL, sha256 per script |
| `measure.py` | the runner: compiles every script and ranks the blockers |
| `REPORT.md` | the generated report |

## Adding a script

1. Pick a file from a repository with a permissive licence (MIT, BSD,
   Apache-2.0, PSF). Record the commit you vendored from.
2. Copy the licence text into `licenses/<owner>_<repo>-<SPDX>.txt` if that
   repository has no entry yet.
3. Add the file to `scripts/<slug>.py` and to `manifest.json` with the
   sha256 of the exact bytes you copied.
4. Run the measurement and commit the refreshed `REPORT.md`.

## Running it

```bash
uv run python -m benchmarks.real_scripts.measure
uv run python -m benchmarks.real_scripts.measure --markdown benchmarks/real_scripts/REPORT.md
uv run python -m benchmarks.real_scripts.measure --json /tmp/real_scripts.json
uv run python -m benchmarks.real_scripts.measure --only cat,folder_size
```

The report ranks blockers by scripts released per day of work, using the
size estimates in `measure.py` (`EFFORT`). Those estimates are planning
input, not measurements; when the plan or the tickets disagree with them,
fix the table.

This directory sits outside the frozen v2 gates. It never writes to
`benchmarks/compat2/` and never reads the scorer holdout.
