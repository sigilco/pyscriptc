"""Run one corpus script under CPython and record its output.

The corpus scripts read their input from a file given on argv, from the
environment, or from fixed arguments. Every script has one recorded invocation
in `invocations.json` next to it, and the runner keeps the stdout it produced so
the compiled binary can be compared against it later.

    uv run python -m benchmarks.scripts_corpus.oracle --all
    uv run python -m benchmarks.scripts_corpus.oracle --slug word_count

The oracle is CPython 3.14. The recorded stdout lives in `oracle/<slug>.txt`
and the invocation in `oracle/<slug>.json`, so a later run can tell whether the
compiler output drifted from CPython.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
ORACLE = HERE / "oracle"

# One invocation per script. The placeholder {fixtures} is the corpus fixtures
# directory, so a recorded command stays valid on any machine.
INVOCATIONS: dict[str, list[str]] = {
    "access_summary": ["{fixtures}/access.log"],
    "argparse_convert": ["1000", "--unit", "mi"],
    "argparse_echo": ["world", "--count", "2", "--upper"],
    "argparse_filter": ["{fixtures}/lines.txt", "--pattern", "b"],
    "bar_chart": ["{fixtures}/lines.txt"],
    "base64_codec": ["encode", "hello world"],
    "checksum_files": ["{fixtures}/lines.txt"],
    "column_stats": ["{fixtures}/numbers.csv"],
    "copy_into": ["{fixtures}/lines.txt", "{tmp}/copied"],
    "count_lines": ["{fixtures}/lines.txt"],
    "csv_join": ["{fixtures}/columns.csv", "{fixtures}/people.csv", "name"],
    "csv_sort": ["{fixtures}/columns.csv", "1"],
    "csv_to_json": ["{fixtures}/columns.csv"],
    "csv_totals": ["{fixtures}/columns.csv"],
    "dedupe_lines": ["{fixtures}/lines.txt"],
    "diff_lines": ["{fixtures}/lines.txt", "{fixtures}/lines2.txt"],
    "disk_usage": ["{fixtures}/lines.txt", "{fixtures}/columns.csv"],
    "env_report": ["PYSC_"],
    "find_files": ["{fixtures}/tree"],
    "grep_lines": ["b", "{fixtures}/lines.txt"],
    "head_tail": ["{fixtures}/lines.txt", "2"],
    "ini_read": ["{fixtures}/sample.ini"],
    "iso_dates": ["{fixtures}/dates.txt"],
    "json_flatten": ["{fixtures}/flat.json"],
    "json_pretty": ["{fixtures}/flat.json"],
    "json_select": ["{fixtures}/records.json", "name"],
    "json_to_csv": ["{fixtures}/records.json"],
    "log_latest": ["{fixtures}/app.log"],
    "merge_dict": ["{fixtures}/a.env", "{fixtures}/b.env"],
    "number_format": ["{fixtures}/numbers.txt"],
    "old_files": ["{fixtures}/tree", "2020-01-01T00:00:00"],
    "reverse_dict": ["{fixtures}/a.env"],
    "running_median": ["{fixtures}/numbers.txt"],
    "shell_quote": ["a b", "c'd", "safe"],
    "slugify_names": ["{fixtures}/tree"],
    "sort_lines": ["{fixtures}/lines.txt"],
    "subprocess_run": ["echo", "hello"],
    "table_render": ["{fixtures}/table.txt"],
    "tar_list": ["{fixtures}/bundle.tar"],
    "template_fill": ["{fixtures}/template.txt", "NAME=world", "VERSION=1.2"],
    "tree_print": ["{fixtures}/tree"],
    "unique_lines": ["{fixtures}/lines.txt"],
    "url_encode": ["encode", "a b&c"],
    "url_split": ["https://example.com/a/b?q=1#frag"],
    "word_count": ["{fixtures}/lines.txt", "3"],
}


def fixtures_dir() -> Path:
    return HERE / "fixtures"


def expanded(slug: str, tmp: Path) -> list[str]:
    """The recorded invocation with the placeholders filled in."""

    raw: list[str] = INVOCATIONS[slug]
    out: list[str] = []
    for arg in raw:
        out.append(arg.replace("{fixtures}", str(fixtures_dir())).replace("{tmp}", str(tmp)))
    return out


def run_script(slug: str, tmp: Path) -> dict[str, Any]:
    """Run one script under CPython and capture stdout, stderr and exit code."""

    script: Path = SCRIPTS / slug / "main.py"
    env: dict[str, str] = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(HERE.parents[1] / "src"),
        # A stable variable so env_report has something to print.
        "PYSC_CORPUS": "1",
        "PYSC_CORPUS_NAME": "scripts-corpus",
    }
    completed = subprocess.run(
        [sys.executable, "-I", str(script), *expanded(slug, tmp)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp),
        check=False,
    )
    return {
        "slug": slug,
        "argv": expanded(slug, tmp),
        "stdout": machine_free(completed.stdout),
        "stderr": completed.stderr,
        "exitCode": completed.returncode,
    }


def machine_free(text: str) -> str:
    """Rewrite checkout-specific paths in a recording to the placeholders.

    The corpus scripts echo their argv verbatim, so a recording would
    otherwise carry the absolute fixtures path of the machine that
    produced it and no other checkout could reproduce it.
    """

    return text.replace(str(fixtures_dir()), "{fixtures}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts_corpus.oracle")
    parser.add_argument("--all", action="store_true", help="run every script in the corpus")
    parser.add_argument("--slug", help="run one script")
    parser.add_argument("--out", type=Path, default=ORACLE, help="where the recordings go")
    args = parser.parse_args(argv)
    slugs: list[str] = []
    if args.all:
        slugs = sorted(path.name for path in SCRIPTS.iterdir() if path.is_dir())
    elif args.slug:
        slugs = [args.slug]
    else:
        parser.error("pass --all or --slug")
    args.out.mkdir(parents=True, exist_ok=True)
    failures: int = 0
    for slug in slugs:
        if slug not in INVOCATIONS:
            print(f"{slug}: no recorded invocation", file=sys.stderr)
            failures = failures + 1
            continue
        tmp: Path = Path(os.environ.get("TMPDIR", "/tmp")) / f"pyscriptc-corpus-{slug}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            record: dict[str, Any] = run_script(slug, tmp)
        except OSError as exc:
            print(f"{slug}: {exc}", file=sys.stderr)
            failures = failures + 1
            continue
        (args.out / f"{slug}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        (args.out / f"{slug}.txt").write_text(record["stdout"], encoding="utf-8")
        status: str = "ok" if record["exitCode"] == 0 else f"exit {record['exitCode']}"
        print(f"{slug}: {status}, {len(record['stdout'])} bytes of stdout")
        if record["exitCode"] != 0:
            failures = failures + 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
