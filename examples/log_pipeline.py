# SPDX-License-Identifier: Apache-2.0
"""Log pipeline reference app: parse access logs CSV, aggregate, emit JSON.

Reads a CSV of access logs (timestamp,method,path,status,bytes,latency_ms),
aggregates by status class (2xx, 3xx, 4xx, 5xx) and by path, computes
percentile latencies (p50, p90, p95, p99), and emits a JSON report.
"""

import csv
import json
import sys
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
            records.append(
                LogRecord(
                    row[0],
                    row[1],
                    row[2],
                    int(row[3]),
                    int(row[4]),
                    float(row[5]),
                )
            )
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

    sc_json: str = json.dumps(status_classes, sort_keys=True)
    paths_json: str = json.dumps(paths, sort_keys=True)
    pct_json: str = json.dumps(pct)

    return (
        '{"total_records": '
        + str(total_records)
        + ', "total_bytes": '
        + str(total_bytes)
        + ', "status_classes": '
        + sc_json
        + ', "paths": '
        + paths_json
        + ', "latencies": '
        + pct_json
        + "}"
    )


def pipeline(input_path: str, output_path: str = "") -> str:
    records: list[LogRecord] = load_logs(input_path)
    report: str = process_pipeline(records)
    if len(output_path) > 0:
        with open(output_path, "w") as fh:
            fh.write(report)
    return report


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    input_path: str = sys.argv[1]
    output_path: str = ""
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    report: str = pipeline(input_path, output_path)
    print(report)
    return 0


if __name__ == "__main__":
    main()
