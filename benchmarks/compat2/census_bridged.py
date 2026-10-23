"""Bridged fence census: which inference fences retire when the checker bridge runs.

Run: python -m benchmarks.compat2.census_bridged [--src DIR] [--bridge mypy] [--json OUT] [--top N]

The tool compiles every corpus package twice, once plain and once with
`--bridge <provider>`, and reports the difference per family: the fences
that retired, the ones that stay, and any that the bridge must not have
invented (a family that grows under a bridge is a bridge bug, since the
bridge only answers, it never forces).

The census is evidence for the adoption ladder's default-on question
(issue #258), not a gate: nothing here feeds the frozen v2 scorer, and the
fence counts come from the same `pyscriptc coverage` report the plain
census uses.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.compat2.census import DEFAULT_SRC, json_payload
from pyscriptc.coverage import build_report
from pyscriptc.factsbridge import BridgeProvider

ROOT = Path(__file__).resolve().parents[2]
INFERENCE_FAMILIES = ("param-inference", "local-inference", "return-inference", "uninitialized-field")


def bridged_package_census(path: Path, provider: str) -> dict[str, Any]:
    """The coverage report of one package or file lowered through the bridge."""
    report = build_report(path, bridge=BridgeProvider(provider, strict=False))
    families = Counter(fence["family"] for fence in report["fences"])
    return {
        "package": path.name,
        "fences": report["summary"]["fences"],
        "rootFences": report["summary"]["rootFences"],
        "families": dict(sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def delta(plain: dict[str, Any], bridged: dict[str, Any]) -> dict[str, Any]:
    """Per-package fence delta between the plain and the bridged compile.

    `retired` counts the fences a family lost under the bridge, `grew` the
    fences it gained. The bridge only answers, it never forces, so a family
    that grows is either a shift (the same site now fences under another
    family, because the inference fence in front of it retired) or a bridge
    bug. The net fence delta separates the two: a shift keeps the total
    flat, a bug raises it.
    """
    plain_families = Counter(plain.get("families", {}))
    bridged_families = Counter(bridged["families"])
    lost = plain_families - bridged_families
    gained = bridged_families - plain_families
    return {
        "package": plain["package"],
        "fences": {"plain": plain["fences"], "bridged": bridged["fences"]},
        "retired": {family: count for family, count in sorted(lost.items())},
        "grew": {family: count for family, count in sorted(gained.items())},
        "net": bridged["fences"] - plain["fences"],
    }


def build_bridged_census(src: Path, provider: str) -> dict[str, Any]:
    dirs = sorted(path for path in src.iterdir() if path.is_dir())
    files = sorted(path for path in src.iterdir() if path.is_file() and path.suffix == ".py")
    targets = [(path.name, path) for path in dirs] + [(path.stem, path) for path in files]
    packages: list[dict[str, Any]] = []
    retired_total: Counter[str] = Counter()
    plain_total: Counter[str] = Counter()
    bridged_total: Counter[str] = Counter()
    for name, path in targets:
        plain = build_report(path)
        plain_summary = {
            "package": name,
            "fences": plain["summary"]["fences"],
            "families": dict(Counter(fence["family"] for fence in plain["fences"])),
        }
        bridged = bridged_package_census(path, provider)
        entry = delta(plain_summary, bridged)
        packages.append(entry)
        plain_total.update(plain_summary["families"])
        bridged_total.update(bridged["families"])
        for family, count in entry["retired"].items():
            retired_total[family] += count
        for family, count in entry["grew"].items():
            retired_total[family] -= count
    grew = sorted(p["package"] for p in packages if p["grew"] and p["net"] > 0)
    shifted = sorted(p["package"] for p in packages if p["grew"] and p["net"] <= 0)

    def totals(family: str) -> dict[str, int]:
        plain_count = plain_total.get(family, 0)
        bridged_count = bridged_total.get(family, 0)
        retired = retired_total.get(family, 0)
        if family in INFERENCE_FAMILIES:
            # An inference fence that moved to another family (a return fence
            # an unproven bridged type turns into a type-mismatch) is still
            # retired: report the plain-to-bridged drop of the family itself.
            retired = max(retired, plain_count - bridged_count)
        return {"plain": plain_count, "bridged": bridged_count, "retired": retired}

    return {
        "src": str(src),
        "bridge": provider,
        "packages": packages,
        "fencesByFamily": {family: totals(family) for family in sorted(set(plain_total) | set(bridged_total))},
        "inferenceFamilies": {family: totals(family) for family in INFERENCE_FAMILIES},
        "grew": grew,
        "shifted": shifted,
    }


def render(census: dict[str, Any]) -> str:
    lines = [
        f"Bridged census ({census['bridge']}): {census['src']}",
        "",
        "| package | fences plain | fences bridged | retired families |",
        "| --- | --- | --- | --- |",
    ]
    for pkg in census["packages"]:
        retired = ", ".join(f"{family} x{count}" for family, count in pkg["retired"].items())
        lines.append(f"| {pkg['package']} | {pkg['fences']['plain']} | {pkg['fences']['bridged']} | {retired or '-'} |")
    lines += ["", "Inference families (plain fences vs bridged vs retired):", ""]
    for family, entry in census["inferenceFamilies"].items():
        lines.append(f"- {family}: {entry['plain']} plain, {entry['bridged']} bridged, {entry['retired']} retired")
    if census["grew"]:
        lines += [
            "",
            "Packages whose fence count GREW under the bridge (a bridge bug, the bridge only answers):",
            *census["grew"],
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="census-bridged")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--bridge", default="mypy", choices=("mypy", "pyrefly"))
    parser.add_argument("--json", type=Path, dest="json_out")
    args = parser.parse_args(argv)
    census = build_bridged_census(args.src, args.bridge)
    if args.json_out:
        payload = json_payload(census, args.json_out)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(render(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
