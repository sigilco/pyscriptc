"""Compare a baseline run with the committed reference metric set.

Absolute milliseconds and RSS belong to the machine that recorded them.
The check uses pyscriptc/CPython ratios, which travel better: a failure
means this tree got slower or heavier relative to CPython than the reference.
"""

from __future__ import annotations

from typing import Any

TIME_RATIO_LIMIT = 2.0
RSS_RATIO_LIMIT = 1.25


def compare_reports(
    current: dict[str, Any],
    reference: dict[str, Any],
    *,
    time_limit: float = TIME_RATIO_LIMIT,
    rss_limit: float = RSS_RATIO_LIMIT,
) -> list[str]:
    failures: list[str] = []
    reference_rows = {row["name"]: row for row in reference["workloads"]}
    current_rows = {row["name"]: row for row in current["workloads"]}
    for name, ref in reference_rows.items():
        row = current_rows.get(name)
        if row is None:
            if _selected(current, name):
                failures.append(f"{name}: missing from this run")
            continue
        if row.get("status") != "ok":
            detail = row.get("detail") or row.get("status")
            failures.append(f"{name}: {detail}")
            continue
        failures.extend(_ratio_failure(name, "time", row["time_ratio"], ref["time_ratio"], time_limit))
        failures.extend(_ratio_failure(name, "rss", row["rss_ratio"], ref["rss_ratio"], rss_limit))
    if _full_suite(current, reference_rows):
        failures.extend(
            _ratio_failure(
                "geomean",
                "time",
                current.get("geomean_time_ratio"),
                reference.get("geomean_time_ratio"),
                time_limit,
            )
        )
        failures.extend(
            _ratio_failure(
                "geomean",
                "rss",
                current.get("geomean_rss_ratio"),
                reference.get("geomean_rss_ratio"),
                rss_limit,
            )
        )
        current_groups = current.get("group_geomeans") or {}
        for name, ref_group in (reference.get("group_geomeans") or {}).items():
            got = current_groups.get(name)
            if not isinstance(got, dict):
                failures.append(f"{name}: group geomean missing")
                continue
            failures.extend(
                _ratio_failure(
                    f"{name} geomean",
                    "time",
                    got.get("geomean_time_ratio"),
                    ref_group.get("geomean_time_ratio"),
                    time_limit,
                )
            )
            failures.extend(
                _ratio_failure(
                    f"{name} geomean",
                    "rss",
                    got.get("geomean_rss_ratio"),
                    ref_group.get("geomean_rss_ratio"),
                    rss_limit,
                )
            )
    return failures


def _selected(current: dict[str, Any], name: str) -> bool:
    names = current.get("selected")
    return names is None or name in names


def _full_suite(current: dict[str, Any], reference_rows: dict[str, Any]) -> bool:
    names = current.get("selected")
    if names is None:
        return True
    return set(reference_rows) <= set(names)


def _ratio_failure(name: str, metric: str, current: Any, reference: Any, limit: float) -> list[str]:
    if not isinstance(current, (int, float)) or not isinstance(reference, (int, float)):
        return [f"{name}: {metric} ratio missing"]
    if reference <= 0 or current <= 0:
        return [f"{name}: reference {metric} ratio is {reference}"]
    growth = current / reference
    if growth > limit:
        speed = 1.0 / current
        ref_speed = 1.0 / reference
        floor = ref_speed / limit
        return [
            f"{name}: {metric} {speed:.1f}x CPython is below {floor:.1f}x "
            f"(reference {ref_speed:.1f}x CPython, limit {limit:.2f}x)"
        ]
    return []
