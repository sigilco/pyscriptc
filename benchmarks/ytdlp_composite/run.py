#!/usr/bin/env python3
"""The yt-dlp composite benchmark (see .agents/notes/2026-10-30-ytdlp-composite.md).

Builds/measures the pyscriptc candidate and the baselines on one machine and
prints the dimension table plus the composite per baseline. Every timing is
the min of N interleaved samples after warmups; the 1-minute load average is
recorded at the start of each timing block.

Usage:
    python benchmarks/ytdlp_composite/run.py --quick   # fewer samples
    python benchmarks/ytdlp_composite/run.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The pinned local-server sample. Override with env vars for a fresh checkout.
SAMPLE_URL = os.environ.get("YTDLP_SAMPLE_URL", "http://127.0.0.1:8614/sample.mp4")
PINNED_UA = "Mozilla/5.0 (X11; Linux x86_64)"

VARIANTS = {
    "V-bundle": {
        "kind": "bundle",
        "path": os.environ.get("YTDLP_BUNDLE", "/tmp/ytbench/outbundle/yt_dlp/bin/yt_dlp"),
        "install_root": os.environ.get("YTDLP_BUNDLE_ROOT", "/tmp/ytbench/outbundle/yt_dlp"),
    },
    "V-minimal": {
        "kind": "bundle",
        "path": os.environ.get("YTDLP_MINIMAL", "/tmp/ytbench/outminimal/yt_dlp/bin/yt_dlp"),
        "install_root": os.environ.get("YTDLP_MINIMAL_ROOT", "/tmp/ytbench/outminimal/yt_dlp"),
    },
    "V-host": {
        "kind": "host",
        "path": os.environ.get("YTDLP_HOST", "/tmp/ytbench/outhost/yt_dlp"),
        "install_root": None,
    },
    "B1": {
        "kind": "venv",
        "path": os.environ.get("YTDLP_B1", "/tmp/ytbench/b1venv/bin/yt-dlp"),
        "install_root": os.environ.get("YTDLP_B1_ROOT", "/tmp/ytbench/b1venv"),
    },
    "B2": {
        "kind": "onefile",
        "path": os.environ.get("YTDLP_B2", "/tmp/ytbench/dl/yt-dlp_linux"),
        "install_root": None,
    },
    "B2z": {
        "kind": "zipapp",
        "path": os.environ.get("YTDLP_B2Z", "/tmp/ytbench/dl/yt-dlp.zipapp"),
        "install_root": None,
    },
    "B3": {
        "kind": "oracle",
        "path": os.environ.get(
            "YTDLP_B3", "/opt/mise/installs/python/3.14.9/bin/python3.14"
        ),
        "install_root": None,
    },
}

TIMED_DIMS = ("version_s", "extractors_s", "extract_s", "download_s", "rss_mb")
BOOL_DIMS = ("needs_python", "relocatable", "clean_container")
SIZE_DIMS = ("files_n", "install_bytes", "artifact_bytes")
ALL_DIMS = TIMED_DIMS + BOOL_DIMS + SIZE_DIMS


def loadavg() -> float:
    return os.getloadavg()[0]


ORACLE_PYTHONPATH = os.environ.get("YTDLP_ORACLE_PATH", "/tmp/ytbench/src")


def child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = ORACLE_PYTHONPATH + ":" + env.get("PYTHONPATH", "")
    return env


def run_once(argv: list[str], cwd: Path | None = None) -> tuple[float, int]:
    start = time.monotonic()
    proc = subprocess.run(
        argv, cwd=cwd, env=child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return time.monotonic() - start, proc.returncode


def run_once_rss(argv: list[str], cwd: Path | None = None) -> tuple[float, int, float]:
    """Wall time and peak RSS of one child, measured with a helper process.

    GNU time is not installed on this machine, so the helper spawns the
    child, waits, and reports RUSAGE_CHILDREN's ru_maxrss (KiB on Linux).
    """

    helper = str(ROOT / "_rss.py")
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, helper, "--", *argv],
        cwd=cwd,
        env=child_env(),
        stdout=subprocess.PIPE,
        text=True,
    )
    wall = time.monotonic() - start
    try:
        rss_kb = float(proc.stdout.strip())
    except ValueError:
        rss_kb = 0.0
    return wall, proc.returncode, rss_kb / 1024.0


def oracle_argv() -> list[str]:
    return [VARIANTS["B3"]["path"], "-m", "yt_dlp"]


def variant_argv(name: str, extra: list[str]) -> list[str]:
    v = VARIANTS[name]
    if v["kind"] == "oracle":
        return [*oracle_argv(), *extra]
    return [v["path"], *extra]


def time_variant(
    name: str, args: list[str], samples: int, cwd: Path | None = None
) -> dict:
    argv = variant_argv(name, args)
    for _ in range(2):
        run_once(argv, cwd=cwd)
    walls = []
    for _ in range(samples):
        wall, _rc = run_once(argv, cwd=cwd)
        walls.append(wall)
    return {
        "min_s": min(walls),
        "median_s": statistics.median(walls),
        "samples": samples,
        "loadavg1": loadavg(),
    }


def measure(name: str, samples: int, dl_samples: int, workdir: Path) -> dict:
    v = VARIANTS[name]
    out: dict[str, object] = {"kind": v["kind"]}
    out["version_s"] = time_variant(name, ["--version"], samples)
    out["extractors_s"] = time_variant(name, ["--list-extractors"], samples)
    out["extract_s"] = time_variant(
        name,
        ["-j", "--no-warnings", "--user-agent", PINNED_UA, SAMPLE_URL],
        samples,
    )
    dl_dir = workdir / f"dl-{name}"
    dl_dir.mkdir(exist_ok=True)
    out["download_s"] = time_variant(
        name,
        [
            "-o",
            str(dl_dir / "out.%(ext)s"),
            "--no-warnings",
            "--user-agent",
            PINNED_UA,
            SAMPLE_URL,
        ],
        dl_samples,
        cwd=dl_dir,
    )
    argv = variant_argv(
        name, ["-j", "--no-warnings", "--user-agent", PINNED_UA, SAMPLE_URL]
    )
    for _ in range(2):
        run_once_rss(argv)
    rss = []
    for _ in range(samples):
        wall, _rc, mb = run_once_rss(argv)
        rss.append(mb)
    out["rss_mb"] = {
        "min_mb": min(rss),
        "median_mb": statistics.median(rss),
        "samples": samples,
        "loadavg1": loadavg(),
    }
    # Distribution facts.
    path = Path(v["path"])
    out["artifact_bytes"] = path.stat().st_size
    if v["kind"] == "venv":
        root = Path(v["install_root"])
        out["files_n"] = sum(1 for p in root.rglob("*") if p.is_file())
        out["install_bytes"] = sum(
            p.stat().st_size for p in root.rglob("*") if p.is_file()
        )
        out["needs_python"] = True
        out["relocatable"] = False
        out["clean_container"] = False
    elif v["kind"] == "bundle":
        root = Path(v["install_root"])
        out["files_n"] = sum(1 for p in root.rglob("*") if p.is_file())
        out["install_bytes"] = sum(
            p.stat().st_size for p in root.rglob("*") if p.is_file()
        )
        out["needs_python"] = False
        out["relocatable"] = True
        out["clean_container"] = None  # filled by the container check
    elif v["kind"] == "host":
        out["files_n"] = 1
        out["install_bytes"] = path.stat().st_size
        out["needs_python"] = True
        out["relocatable"] = False
        out["clean_container"] = False
    elif v["kind"] == "onefile":
        out["files_n"] = 1
        out["install_bytes"] = path.stat().st_size
        out["needs_python"] = False
        out["relocatable"] = True
        out["clean_container"] = None
    elif v["kind"] == "zipapp":
        out["files_n"] = 1
        out["install_bytes"] = path.stat().st_size
        out["needs_python"] = True
        out["relocatable"] = True
        out["clean_container"] = False
        tree = Path(os.environ.get("YTDLP_B2Z_TREE", "/tmp/ytbench/dl/zipapp-tree"))
        if tree.is_dir():
            # the installed form of the zipapp is its unpacked tree
            out["install_bytes"] = sum(
                q.stat().st_size for q in tree.rglob("*") if q.is_file()
            )
    else:  # oracle
        out["files_n"] = None
        out["install_bytes"] = None
        out["needs_python"] = True
        out["relocatable"] = None
        out["clean_container"] = None
    return out


def ratio(candidate: dict, baseline: dict, dim: str) -> float:
    if dim in TIMED_DIMS:
        key = "min_s" if "min_s" in candidate.get(dim, {}) else "min_mb"
        return candidate[dim][key] / baseline[dim][key]
    if dim == "files_n":
        # A single file is the best case and a tree counts its files, so the
        # comparison is on log2: 1 file against 153 files is 7.3x, not 153x.
        c = max(int(candidate[dim] or 1), 1)
        b = max(int(baseline[dim] or 1), 1)
        return math.log2(c + 1) / math.log2(b + 1)
    if dim in SIZE_DIMS:
        if candidate[dim] is None or baseline[dim] is None:
            return 1.0
        return candidate[dim] / baseline[dim]
    # yes/no: 1.0 same, 0.5 better (candidate False baseline True), 2.0 worse.
    c, b = candidate[dim], baseline[dim]
    if c is None or b is None:
        return 1.0
    if c == b:
        return 1.0
    # better means "no" (no python needed, clean container, relocatable)
    return 0.5 if not c else 2.0


def composite(candidate: dict, baseline: dict) -> tuple[float, dict]:
    ratios = {d: ratio(candidate, baseline, d) for d in ALL_DIMS}
    geo = math.exp(sum(math.log(r) for r in ratios.values()) / len(ratios))
    return geo, ratios


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--only", help="comma list of variants to measure")
    args = ap.parse_args()
    samples = 3 if args.quick else 7
    dl_samples = 2 if args.quick else 5
    names = args.only.split(",") if args.only else ["V-bundle", "V-minimal", "V-host", "B1", "B2", "B2z", "B3"]
    workdir = Path(tempfile.mkdtemp(prefix="ytdlp-composite-"))
    results = {}
    for name in names:
        print(f"measuring {name} ...", flush=True)
        results[name] = measure(name, samples, dl_samples, workdir)
    print(f"load average at end: {loadavg():.2f}")
    table = {}
    for cand in ("V-bundle", "V-minimal", "V-host"):
        if cand in results and "B1" in results:
            table[f"{cand}/B1"] = composite(results[cand], results["B1"])
        if cand in results and "B2" in results:
            table[f"{cand}/B2"] = composite(results[cand], results["B2"])
    payload = {"samples": samples, "loadavg_end": loadavg(), "results": results, "composite": {k: {"geo": v[0], "ratios": v[1]} for k, v in table.items()}}
    text = json.dumps(payload, indent=2, default=str)
    if args.json:
        args.json.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
