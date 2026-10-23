#!/usr/bin/env python3
"""The network receipt: loopback HTTP clients, compiled against CPython.

Part 1 is the script-based client: one source file
(`workloads/client.py`) runs as a compiled binary and as CPython against a
local `http.server`, over sequential GET/POST and threaded GET/POST. The
runner checks the two sides print the same request and byte counts, then
compares wall time. The client also reports the in-process elapsed time on
stderr, which is the per-request CPU proxy: process startup is excluded,
only the request loop is inside the measurement.

Part 2 is sustained server throughput. `http.server` is still a fence in the
compiler (PS2001, server issues #227-230), so the baseline rows time
CPython's `http.server` under a fixed load from a CPython client
(`workloads/load_client.py`) and record requests/s plus p50/p99 latency.
The compiled server row is printed as pending; `--compiled-server PATH`
slots a future server binary into the same row shape without touching the
runner again.

  uv run python -m benchmarks.net.run
  uv run python -m benchmarks.net.run --runs=3 --json=net.json

Opt-in: nothing in CI or `benchmarks.baseline.run --check` reads this
package. Port selection is the kernel's (bind port 0); every connection
stays on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CLIENT = HERE / "workloads" / "client.py"
LOAD_CLIENT = HERE / "workloads" / "load_client.py"

# Fixed load for part 1. One run of the full matrix stays well under a
# minute: the sequential counts dominate, at a few ms per loopback request
# on the slower side.
CLIENT_MODES: list[tuple[str, int, int]] = [
    ("seq-get", 300, 1),
    ("seq-post", 200, 1),
    ("thread-get", 400, 4),
    ("thread-post", 200, 4),
]

# Fixed load for part 2: request count and concurrency per server variant.
SERVER_LOAD_GET = 600
SERVER_LOAD_POST = 300
SERVER_WORKERS = 4


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _compile(source: Path, out_dir: Path) -> Path:
    binary = out_dir / source.stem
    done = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(source), "--emit", "bin", "-o", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 or not binary.exists():
        raise SystemExit(f"compile failed for {source.name}:\n{done.stdout}{done.stderr}")
    return binary


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _split_stderr(stderr: str) -> float:
    """The client's own elapsed seconds, from its stderr."""
    for line in stderr.splitlines():
        if line.startswith("elapsed_s="):
            return float(line.split("=", 1)[1])
    return 0.0


def _time_side(command: list[str]) -> tuple[float, float, str]:
    """Wall seconds outside, in-process seconds from the client, stdout."""
    started = time.perf_counter()
    done = _run(command)
    wall = time.perf_counter() - started
    if done.returncode != 0:
        raise SystemExit(f"{command[0]} exited {done.returncode}: {done.stderr.strip()[:400]}")
    return wall, _split_stderr(done.stderr), done.stdout.strip()


def _client_row(
    binary: Path,
    cpython: list[str],
    url: str,
    mode: str,
    count: int,
    workers: int,
    runs: int,
) -> dict[str, Any]:
    """One client mode, compiled against CPython, medians over `runs`."""
    args = [url, mode, str(count), str(workers)]
    row: dict[str, Any] = {"mode": mode, "requests": count, "workers": workers, "samples": runs}
    native_command = [str(binary), *args]
    cpython_command = [*cpython, str(CLIENT), *args]
    reference = _time_side(cpython_command)
    probe = _time_side(native_command)
    if probe[2] != reference[2]:
        row["status"] = "mismatch"
        row["detail"] = f"compiled {probe[2]!r} vs cpython {reference[2]!r}"
        return row
    native_wall: list[float] = []
    native_inproc: list[float] = []
    cpython_wall: list[float] = []
    cpython_inproc: list[float] = []
    for _ in range(runs):
        one = _time_side(native_command)
        if one[2] != reference[2]:
            row["status"] = "mismatch"
            return row
        native_wall.append(one[0])
        native_inproc.append(one[1])
        one = _time_side(cpython_command)
        if one[2] != reference[2]:
            row["status"] = "mismatch"
            return row
        cpython_wall.append(one[0])
        cpython_inproc.append(one[1])
    native_wall_ms = _median(native_wall) * 1000.0
    cpython_wall_ms = _median(cpython_wall) * 1000.0
    native_inproc_s = _median(native_inproc)
    cpython_inproc_s = _median(cpython_inproc)
    row.update(
        {
            "status": "ok",
            "native_wall_ms": round(native_wall_ms, 1),
            "cpython_wall_ms": round(cpython_wall_ms, 1),
            "time_ratio": native_wall_ms / max(cpython_wall_ms, 1e-9),
            "native_inproc_ms": round(native_inproc_s * 1000.0, 1),
            "cpython_inproc_ms": round(cpython_inproc_s * 1000.0, 1),
            "native_per_request_cpu_ms": round(native_inproc_s / count * 1000.0, 3),
            "cpython_per_request_cpu_ms": round(cpython_inproc_s / count * 1000.0, 3),
            "native_rps": round(count / max(native_inproc_s, 1e-9)),
            "cpython_rps": round(count / max(cpython_inproc_s, 1e-9)),
        }
    )
    return row


def _server_row(
    cpython: list[str],
    url_get: str,
    url_post: str,
    runs: int,
    compiled_server: Path | None,
) -> dict[str, Any]:
    """One http.server variant under the fixed-load CPython client."""
    row: dict[str, Any] = {
        "server": "cpython http.server",
        "workers": SERVER_WORKERS,
        "load": {"get": SERVER_LOAD_GET, "post": SERVER_LOAD_POST},
    }
    if compiled_server is not None:
        row["compiled_server"] = str(compiled_server)
    else:
        row["compiled_status"] = "pending"
        row["compiled_detail"] = "http.server is a PS2001 fence; server lowering tracks #227-230"
    get_load: list[float] = []
    post_load: list[float] = []
    for _ in range(runs):
        get_load.append(_load_once(cpython, url_get, "get", SERVER_LOAD_GET, SERVER_WORKERS))
        post_load.append(_load_once(cpython, url_post, "post", SERVER_LOAD_POST, SERVER_WORKERS))
    row["get_rps"] = round(SERVER_LOAD_GET / _median(get_load), 1)
    row["post_rps"] = round(SERVER_LOAD_POST / _median(post_load), 1)
    row["get_wall_ms"] = round(_median(get_load) * 1000.0, 1)
    row["post_wall_ms"] = round(_median(post_load) * 1000.0, 1)
    latencies = _load_latencies(cpython, url_get, "get", SERVER_LOAD_GET, SERVER_WORKERS)
    row["get_p50_ms"] = round(_percentile(latencies, 0.50) * 1000.0, 3)
    row["get_p99_ms"] = round(_percentile(latencies, 0.99) * 1000.0, 3)
    return row


def _load_once(cpython: list[str], url: str, mode: str, count: int, workers: int) -> float:
    done = _run([*cpython, str(LOAD_CLIENT), url, mode, str(count), str(workers), "--quiet"])
    if done.returncode != 0:
        raise SystemExit(f"load client failed: {done.stderr.strip()[:400]}")
    return _split_stderr(done.stderr)


def _load_latencies(cpython: list[str], url: str, mode: str, count: int, workers: int) -> list[float]:
    done = _run([*cpython, str(LOAD_CLIENT), url, mode, str(count), str(workers)])
    if done.returncode != 0:
        raise SystemExit(f"load client failed: {done.stderr.strip()[:400]}")
    latencies: list[float] = []
    for line in done.stdout.splitlines():
        if line.startswith("latency_s="):
            latencies.append(float(line.split("=", 1)[1]))
    if len(latencies) != count:
        raise SystemExit(f"load client reported {len(latencies)} of {count} latencies")
    return latencies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Network receipt: loopback HTTP client and server.")
    parser.add_argument("--runs", type=int, default=3, help="interleaved rounds per side")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    parser.add_argument(
        "--compiled-server",
        dest="compiled_server",
        help="path to a compiled http.server binary; without it the compiled row reads pending",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2
    compiled_server = Path(args.compiled_server) if args.compiled_server else None

    started_at = time.perf_counter()
    cpython = [sys.executable]
    with tempfile.TemporaryDirectory(prefix="pyscriptc-net-") as tmp:
        binary = _compile(CLIENT, Path(tmp))
        rows: list[dict[str, Any]] = []
        report: dict[str, Any] = {
            "schema": 1,
            "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cpython": sys.version.split()[0],
            "runs": args.runs,
        }
        from benchmarks.net.server import Server

        for threaded in (False, True):
            variant: dict[str, Any] = {"server_kind": "threaded" if threaded else "single"}
            with Server(threaded=threaded) as server:
                url = f"http://127.0.0.1:{server.port}/bench"
                variant["client"] = [
                    _client_row(binary, cpython, url, mode, count, workers, args.runs)
                    for mode, count, workers in CLIENT_MODES
                ]
                if not threaded:
                    # The single-threaded server serves one request at a
                    # time, so its sustained row is sequential load.
                    url_get, url_post = url, url
                    variant["server"] = _server_row(cpython, url_get, url_post, args.runs, compiled_server)
                else:
                    url_get, url_post = url, url
                    variant["server"] = _server_row(cpython, url_get, url_post, args.runs, compiled_server)
            rows.append(variant)
            _print_variant(variant)
        report["variants"] = rows
    report["elapsed_s"] = round(time.perf_counter() - started_at, 1)
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"measured in {report['elapsed_s']} s")
    return 0


def _print_variant(variant: dict[str, Any]) -> None:
    kind = variant["server_kind"]
    print(f"== http.server ({kind}) ==")
    for row in variant["client"]:
        if row["status"] != "ok":
            print(f"  {row['mode']:<12} {row['status']}: {row.get('detail', '')}")
            continue
        print(
            f"  {row['mode']:<12} "
            f"native {row['native_wall_ms']:>7.1f} ms  cpython {row['cpython_wall_ms']:>7.1f} ms  "
            f"cpu/req {row['native_per_request_cpu_ms']:.3f} / {row['cpython_per_request_cpu_ms']:.3f} ms  "
            f"rps {row['native_rps']} / {row['cpython_rps']}"
        )
    server = variant["server"]
    pending = server.get("compiled_status") == "pending"
    tag = " (compiled server: pending, #227-230)" if pending else ""
    print(
        f"  server GET  {server['get_rps']:.0f} req/s  "
        f"p50 {server['get_p50_ms']:.2f} ms  p99 {server['get_p99_ms']:.2f} ms{tag}"
    )
    print(f"  server POST {server['post_rps']:.0f} req/s")


if __name__ == "__main__":
    raise SystemExit(main())
