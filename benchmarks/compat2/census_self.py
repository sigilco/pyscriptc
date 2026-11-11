"""Parse-only fence census for the compiler package (self-host S1/S2).

Runs `pyscriptc coverage` on `src/pyscriptc` as one directory compile (relative
imports resolve), then attributes fences to each importable module. No LLVM or
link step.

Run: python -m benchmarks.compat2.census_self [--src DIR] [--json OUT] [--top N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmarks.compat2.census import json_payload, rank_families, render
from pyscriptc.coverage import build_report
from pyscriptc.package import _discover

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "src" / "pyscriptc"
DEFAULT_JSON = ROOT / "data" / "benchmarks" / "census-self.json"


def _families(fences: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    roots: dict[str, int] = {}
    derived: dict[str, int] = {}
    for fence in fences:
        bucket = derived if fence["derived"] else roots
        bucket[fence["family"]] = bucket.get(fence["family"], 0) + 1
    return (
        dict(sorted(roots.items(), key=lambda kv: (-kv[1], kv[0]))),
        dict(sorted(derived.items(), key=lambda kv: (-kv[1], kv[0]))),
    )


def _lines_of_code(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        total += len(path.read_text(encoding="utf-8").splitlines())
    return total


def module_entry(
    name: str,
    fences: list[dict[str, Any]],
    definitions: int,
    fenced_definitions: int,
) -> dict[str, Any]:
    roots, derived = _families(fences)
    return {
        "package": name,
        "fences": len(fences),
        "definitions": definitions,
        "nativeDefinitions": max(0, definitions - fenced_definitions),
        "fencedDefinitions": fenced_definitions,
        "rootFamilies": roots,
        "derivedFamilies": derived,
    }


CACHE_VERSION = "1"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def _source_hash(paths: list[Path]) -> str:
    """A hash over the census inputs: every scanned source file's bytes,
    the module layout (a rename changes the keys even at equal bytes) and
    the cache schema version. A hit is a recompute byte for byte."""
    digest = hashlib.sha256()
    digest.update(CACHE_VERSION.encode())
    for path in paths:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _cached_census(src: Path, paths: list[Path], key: str) -> dict[str, Any] | None:
    path = CACHE_DIR / f"census-self-{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("mode") != "parse-only":
        return None
    required = {"src", "lines", "modules", "skipped", "fences", "definitions", "packages", "clean", "ranking"}
    if not required <= data.keys():
        return None
    return data


def _store_census(census: dict[str, Any], key: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"census-self-{key}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(census), encoding="utf-8")
    tmp.replace(path)


def build_self_census(src: Path, *, use_cache: bool = True) -> dict[str, Any]:
    src = src.resolve()
    units, skipped = _discover(src)
    paths = [path for _, path, _ in units]
    key = _source_hash(paths)
    if use_cache:
        cached = _cached_census(src, paths, key)
        if cached is not None:
            return cached
    report = build_report(src)

    fences_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fence in report["fences"]:
        fences_by_file[fence["file"]].append(fence)

    def_count: dict[str, int] = defaultdict(int)
    fenced_def_count: dict[str, int] = defaultdict(int)
    for definition in report["definitions"]:
        def_count[definition["file"]] += 1
        if definition["fences"]:
            fenced_def_count[definition["file"]] += 1

    packages: list[dict[str, Any]] = []
    for _module_name, path, _is_package in units:
        file_key = str(path.resolve())
        if path.name == "__init__.py" and path.parent == src:
            label = src.name
        else:
            label = path.stem
        packages.append(
            module_entry(
                label,
                fences_by_file.get(file_key, []),
                def_count.get(file_key, 0),
                fenced_def_count.get(file_key, 0),
            )
        )

    clean = [p["package"] for p in packages if p["fences"] == 0]
    try:
        label = src.relative_to(ROOT).as_posix()
    except ValueError:
        label = str(src)
    total_fences = report["summary"]["fences"]
    census = {
        "mode": "parse-only",
        "src": label,
        "lines": _lines_of_code(paths),
        "modules": len(units),
        "skipped": skipped,
        "fences": total_fences,
        "definitions": report["summary"]["definitions"],
        "nativeDefinitions": report["summary"].get(
            "nativeDefinitions",
            max(0, report["summary"]["definitions"] - report["summary"]["fencedDefinitions"]),
        ),
        "fencedDefinitions": report["summary"]["fencedDefinitions"],
        "packages": packages,
        "clean": clean,
        "ranking": rank_families(packages),
    }
    if use_cache:
        _store_census(census, key)
    return census


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="census_self")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--json", type=Path, dest="json_out", default=None)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--no-cache", action="store_true", help="recompute even when a cached census exists")
    args = parser.parse_args(argv)
    census = build_self_census(args.src, use_cache=not args.no_cache)
    json_out = args.json_out
    if json_out is not None:
        payload = json_payload(census, json_out)
        payload["command"] = f"uv run python -m benchmarks.compat2.census_self --json {json_out.as_posix()}"
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(render(census, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
