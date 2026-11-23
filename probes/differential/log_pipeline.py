# SPDX-License-Identifier: Apache-2.0
# Issue #498: the log pipeline reference app (examples/log_pipeline.py).
# A CSV of access logs is parsed through csv.reader into dataclass records,
# aggregated by status class and by path, reduced to percentile latencies and
# written as a JSON report. A temp file carries the file-handle lane, so the
# two runs compare the same bytes: the report text, the key order and the
# float formatting of the percentiles.
import csv
import json
import os
import tempfile
from dataclasses import dataclass


@dataclass
class LogRecord:
    timestamp: str
    method: str
    path: str
    status: int
    bytes_count: int
    latency_ms: float


def load_logs(path: str) -> list[LogRecord]:
    records: list[LogRecord] = []
    with open(path) as fh:
        for row in csv.reader(fh):
            if len(row) < 6:
                continue
            if row[0] == "timestamp":
                continue
            records.append(LogRecord(row[0], row[1], row[2], int(row[3]), int(row[4]), float(row[5])))
    return records


def calc_percentile(sorted_values: list[float], q: float) -> float:
    n: int = len(sorted_values)
    if n == 0:
        return 0.0
    pos: float = q * float(n - 1)
    low: int = int(pos)
    high: int = low + 1
    if high >= n:
        high = n - 1
    frac: float = pos - float(low)
    return round(sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac, 2)


def process_pipeline(records: list[LogRecord]) -> str:
    total_records: int = len(records)
    total_bytes: int = 0
    status_classes: dict[str, int] = {}
    paths: dict[str, int] = {}
    latencies: list[float] = []
    i: int = 0
    while i < total_records:
        rec: LogRecord = records[i]
        total_bytes = total_bytes + rec.bytes_count
        latencies.append(rec.latency_ms)
        sc: str = str(rec.status // 100) + "xx"
        status_classes[sc] = status_classes.get(sc, 0) + 1
        paths[rec.path] = paths.get(rec.path, 0) + 1
        i = i + 1
    latencies.sort()
    pct: dict[str, float] = {}
    pct["p50"] = calc_percentile(latencies, 0.50)
    pct["p90"] = calc_percentile(latencies, 0.90)
    pct["p95"] = calc_percentile(latencies, 0.95)
    pct["p99"] = calc_percentile(latencies, 0.99)
    return (
        '{"total_records": '
        + str(total_records)
        + ', "total_bytes": '
        + str(total_bytes)
        + ', "status_classes": '
        + json.dumps(status_classes, sort_keys=True)
        + ', "paths": '
        + json.dumps(paths, sort_keys=True)
        + ', "latencies": '
        + json.dumps(pct)
        + "}"
    )


def build_csv(n: int) -> str:
    # A deterministic body: every status class, a quoted path that carries a
    # comma, a short row the loader skips and a header row it recognises.
    methods: list[str] = ["GET", "POST", "PUT", "DELETE"]
    paths: list[str] = ["/", "/api/v1/users", '"/search,q=1"', "/static/app.js"]
    statuses: list[int] = [200, 201, 301, 404, 500, 503, 204, 403]
    lines: list[str] = ["timestamp,method,path,status,bytes,latency_ms"]
    k: int = 0
    while k < n:
        ms: float = float((k * 37) % 997) / 7.0 + 0.5
        lines.append(
            "2026-11-23T10:00:"
            + str(10 + k % 50)
            + "Z,"
            + methods[k % 4]
            + ","
            + paths[(k * 3) % 4]
            + ","
            + str(statuses[(k * 5) % 8])
            + ","
            + str(100 + (k * 7919) % 50000)
            + ","
            + str(ms)
        )
        if k % 25 == 0:
            lines.append("short,row")
        k = k + 1
    return "\n".join(lines) + "\n"


def run(n: int) -> None:
    fd, src = tempfile.mkstemp()
    os.close(fd)
    out = src + ".json"
    with open(src, "w") as fh:
        fh.write(build_csv(n))
    records: list[LogRecord] = load_logs(src)
    report: str = process_pipeline(records)
    with open(out, "w") as fh:
        fh.write(report)
    with open(out) as fh:
        print(fh.read() == report)
    print(report)
    os.remove(src)
    os.remove(out)


run(400)
run(1)
run(0)
