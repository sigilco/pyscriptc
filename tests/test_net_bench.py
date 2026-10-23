"""The net receipt is wired: the workload compiles, matches CPython, and the runner parses."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from benchmarks.net.run import _split_stderr
from benchmarks.net.server import Server

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "benchmarks" / "net" / "workloads" / "client.py"


def test_server_binds_an_ephemeral_port_and_stops() -> None:
    server = Server(threaded=False)
    server.start()
    try:
        assert server.port > 0
        body = urllib_get(f"http://127.0.0.1:{server.port}/probe")
        assert body == b"net-benchmark-ok\n"
    finally:
        server.stop()


def test_client_compiles_and_matches_cpython(tmp_path: Path) -> None:
    with Server(threaded=False) as server:
        url = f"http://127.0.0.1:{server.port}/bench"
        binary = tmp_path / "client"
        compiled = subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", str(CLIENT), "--emit", "bin", "-o", str(binary)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert compiled.returncode == 0, compiled.stderr
        args = [url, "seq-get", "10", "1"]
        native = subprocess.run([str(binary), *args], capture_output=True, text=True, check=False)
        cpython = subprocess.run([sys.executable, str(CLIENT), *args], capture_output=True, text=True, check=False)
        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout.startswith("requests=10")
        assert _split_stderr(native.stderr) > 0
        assert _split_stderr(cpython.stderr) > 0


def test_load_client_reports_every_latency(tmp_path: Path) -> None:
    load_client = ROOT / "benchmarks" / "net" / "workloads" / "load_client.py"
    with Server(threaded=True) as server:
        url = f"http://127.0.0.1:{server.port}/bench"
        done = subprocess.run(
            [sys.executable, str(load_client), url, "get", "40", "4"],
            capture_output=True,
            text=True,
            check=False,
        )
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == "requests=40"
    latencies = [float(line.split("=", 1)[1]) for line in lines if line.startswith("latency_s=")]
    assert len(latencies) == 40
    assert _split_stderr(done.stderr) > 0


def urllib_get(url: str) -> bytes:
    import urllib.request

    with urllib.request.urlopen(url) as resp:
        return resp.read()
