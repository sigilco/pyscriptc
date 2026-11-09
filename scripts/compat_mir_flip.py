#!/usr/bin/env python3
"""Receipt-3: MIR emit-lane flip receipt for the v0.9.0 default flip (epic #817).

Runs the visible compat2 corpus twice through `benchmarks.compat2.score` —
once with `PYSC_MIR=0` (the direct emitter, today's default) and once with
`PYSC_MIR=1` (the MIR lane) — then feeds the two result JSONs through
`scripts/compat_no_flip.compare` / `render_markdown` and reports every
outcome flip in both directions.

    uv run python scripts/compat_mir_flip.py
    uv run python scripts/compat_mir_flip.py --out-dir receipts/fr3 --json
    uv run python scripts/compat_mir_flip.py --out-dir /tmp/fr3-smoke -- --shard 0/64

Cache correctness: the scorer cache key is (probe hash, oracle version,
compiler file hash) — it does NOT include `PYSC_MIR` (see
`benchmarks/compat2/cache.py:cache_key`). Both lanes therefore always run
with `--no-cache`; without it the second lane would read the first lane's
cached rows and the receipt would silently compare a lane with itself.
Shard merging (`score.py --shard I/N`) only merges result JSON files; it
never touches the cache, so `--no-cache` covers every path. A sharded lane
reads the `<out>.shardIofN.json` file `score.py` writes for that shard.

Env: each scorer subprocess gets a clean lane env (`PYSC_MIR` pinned to
the lane, `PYTHONHASHSEED=0`, `src/` on `PYTHONPATH`, same pattern as
`scripts/runtime_ab.py:lane_env`). The scorer shells out to
`python -m pyscriptc compile` inheriting its environment
(`score.py:_build`), and `pyscriptc.miremit.mir_opt_in` reads `PYSC_MIR`
from `os.environ` in that compile process, so the lane propagates.

Corpus: the default invocation mirrors the nightly —
`benchmarks/compat2/probes --corpus`, the visible split only. `--holdout`
is refused: this tool is a generator-side receipt and never opens the
closed split.

Extra `score.py` arguments pass through verbatim after a `--` separator
(`--shard I/N`, `--tier`, `--workers`, `--timeout`, `--coverage-map`,
`--target-version`, etc.). The separator is required: without it argparse
cannot tell a score.py flag's value from this tool's `probes` positional.
Managed flags (`--out`, `--corpus`, `--holdout`, `--headroom`) are refused
so the two lanes always score the same probe set into distinct files.

Runtime: the full visible corpus is ~14k probes scored twice (two native
compiles plus a CPython oracle run each) — expect hours, not minutes.
This is a release-time receipt; there is no sampling. For a smoke run
use a shard, e.g. `-- --shard 0/64` scores ~1/64 of the corpus per lane.

Exit status: 0 on success regardless of how many outcomes flipped
(report-only, no CI gate); 2 on tool errors (scorer failure, missing or
unreadable result file).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    # invoked as `python scripts/compat_mir_flip.py`: put the repo root on
    # sys.path so `scripts.compat_no_flip` resolves like it does under pytest
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.compat2.score import shard_path
from scripts.compat_no_flip import compare, load_results, render_markdown

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
DEFAULT_PROBES = Path("benchmarks/compat2/probes")
SCORE_MODULE = "benchmarks.compat2.score"
LANES = (("direct", False), ("mir", True))

# score.py flags this tool owns or must never see. `--holdout` opens the
# closed split; `--headroom` writes no rows; `--out`/`--corpus` would let
# the lanes diverge.
_BLOCKED_PREFIXES = ("--holdout", "--headroom", "--out", "--corpus")


def lane_env(mir: bool) -> dict[str, str]:
    """Scorer environment for one lane. Direct pins `PYSC_MIR=0` so a parent opt-in cannot leak."""
    env = os.environ.copy()
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONHASHSEED"] = "0"
    env["PYSC_MIR"] = "1" if mir else "0"
    return env


def score_command(probes: str, out: Path, extra: list[str], corpus: bool) -> list[str]:
    """The `python -m benchmarks.compat2.score` argv for one lane.

    `--no-cache` is unconditional: the cache key does not include
    `PYSC_MIR`, so a cached lane would replay the other lane's rows.
    """
    cmd = [sys.executable, "-m", SCORE_MODULE, probes, "--no-cache", "--out", str(out)]
    if corpus:
        cmd.append("--corpus")
    cmd.extend(extra)
    return cmd


def check_passthrough(extra: list[str]) -> None:
    """Refuse score.py flags that would break the two-lane receipt."""
    for arg in extra:
        for blocked in _BLOCKED_PREFIXES:
            if arg == blocked or arg.startswith(blocked + "="):
                raise ValueError(f"{arg!r} is not allowed here; this tool owns the corpus, out path and cache mode")


def _shard(extra: list[str]) -> tuple[int, int] | None:
    """The last `--shard I/N` passthrough value, or None when absent/malformed."""
    value: str | None = None
    for index, arg in enumerate(extra):
        if arg == "--shard" and index + 1 < len(extra):
            value = extra[index + 1]
        elif arg.startswith("--shard="):
            value = arg.split("=", 1)[1]
    if value is None:
        return None
    try:
        left, right = value.split("/", 1)
        return int(left), int(right)
    except ValueError:
        return None


def result_path(out: Path, extra: list[str]) -> Path:
    """The file `score.py` writes for this invocation.

    Sharded runs write `<out>.shardIofN.json` and only merge into `<out>`
    once every shard has landed, so a one-shard receipt must read the shard
    file itself.
    """
    shard = _shard(extra)
    if shard is None or shard[1] <= 1:
        return out
    return shard_path(out, shard[0], shard[1])


def run_lane(name: str, mir: bool, probes: str, out: Path, extra: list[str], corpus: bool) -> Path:
    """Run the scorer for one lane; return the result file it wrote."""
    cmd = score_command(probes, out, extra, corpus)
    proc = subprocess.run(cmd, cwd=ROOT, env=lane_env(mir), capture_output=True, text=True)
    if proc.stdout:
        sys.stderr.write(f"[{name}] {proc.stdout}")
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()
        raise RuntimeError(f"{name} lane scorer failed (exit {proc.returncode}): {tail[-1] if tail else 'no stderr'}")
    result = result_path(out, extra)
    if not result.is_file():
        raise RuntimeError(f"{name} lane scorer wrote no result at {result}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score the visible compat2 corpus on both emit lanes (PYSC_MIR=0/1) and report outcome flips."
    )
    parser.add_argument(
        "probes",
        nargs="?",
        default=str(DEFAULT_PROBES),
        help="probe JSONL file or directory (default: benchmarks/compat2/probes)",
    )
    parser.add_argument(
        "--no-corpus",
        action="store_true",
        help="skip --corpus (probe files only; default mirrors the nightly: probes dir plus the visible corpus)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="directory for the two score JSONs (default: a fresh temp dir; paths are printed to stderr)",
    )
    parser.add_argument("--json", action="store_true", help="print the compare report as JSON instead of markdown")
    parser.add_argument("--json-out", type=Path, help="write the compare report JSON here")
    parser.add_argument("--md-out", type=Path, help="write the markdown table here")
    return parser


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first `--`: tool args before, score.py args after."""
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    tool_argv, extra = split_passthrough(list(sys.argv[1:] if argv is None else argv))
    try:
        args = parser.parse_args(tool_argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        check_passthrough(extra)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="compat-mir-flip-") as tmp:
            out_dir = args.out_dir if args.out_dir is not None else Path(tmp)
            out_dir.mkdir(parents=True, exist_ok=True)
            score_files: dict[str, Path] = {}
            for name, mir in LANES:
                out = out_dir / f"{name}.json"
                print(f"scoring {name} lane (PYSC_MIR={'1' if mir else '0'}) -> {out}", file=sys.stderr)
                score_files[name] = run_lane(name, mir, args.probes, out, extra, corpus=not args.no_corpus)
            direct = load_results(score_files["direct"])
            mir = load_results(score_files["mir"])
            report = compare(direct, mir)
            table = render_markdown(report)
            json_out = args.json_out
            md_out = args.md_out
            if json_out is not None:
                json_out.parent.mkdir(parents=True, exist_ok=True)
                json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if md_out is not None:
                md_out.parent.mkdir(parents=True, exist_ok=True)
                md_out.write_text(table, encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        sys.stdout.write(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
