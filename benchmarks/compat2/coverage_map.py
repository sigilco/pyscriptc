"""Coverage map and Tier 0 selection.

    python -m benchmarks.compat2.coverage_map build PROBES [--out map.json] [--shard I/N]
    python -m benchmarks.compat2.coverage_map select PROBES [--base REF] [--map map.json]

`build` lowers every probe in this process under a module level `sys.setprofile` hook and
records which compiler modules ran. It then emits LLVM for probes that lower without a
fence and records the runtime C files that define the symbols the IR declares. The map is
built once and cached: an entry is reused while the probe is unchanged.

Each probe gets two lists:

- `touches`: what the probe ran. Tier 0 intersects this with the git diff.
- `key`: what its cache key hashes. `touches`, plus compiler modules that hold data tables
  (module level assignments) imported by a touched module, plus the CLI entry points, plus
  every runtime C file when the probe reaches clang (clang builds all of them).

Tier 0 selects the probes whose `touches` meet the diff, capped at 400. When a shared core
file changed it takes a fixed seed stratified sample of 300 across every area and category.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from benchmarks.compat2 import headroom
from benchmarks.compat2.cache import COMPILER_DIR, ROOT, compiler_hash, probe_hash
from benchmarks.compat2.loader import load_probes
from benchmarks.compat2.outcomes import Probe

HERE = Path(__file__).resolve().parent
MAP_PATH = HERE / ".cache" / "coverage-map.json"
RUNTIME = COMPILER_DIR / "runtime"

TIER0_CAP = 400
SHARED_SAMPLE = 300
SEED = 20261014
# A change here can move any probe, so selection by intersection is meaningless.
SHARED_CORE = frozenset({"ir.py", "llvm.py", "runtime/ps_rt.c", "runtime/ps_rt.h"})
# The scorer always goes through the CLI.
ENTRY = ("__init__.py", "__main__.py", "cli.py")

_DEFINITION = re.compile(r"^[A-Za-z_][\w \t\*]*?\b(ps_\w+)\s*\(", re.MULTILINE)
_DECLARE = re.compile(r"^declare\b[^@\n]*@(ps_\w+)\s*\(", re.MULTILINE)


def rel(path: str | Path) -> str | None:
    try:
        return str(Path(path).resolve().relative_to(COMPILER_DIR))
    except ValueError:
        return None


def runtime_files() -> list[str]:
    return sorted(str(p.relative_to(COMPILER_DIR)) for p in RUNTIME.glob("*") if p.suffix in {".c", ".h"})


def symbol_index() -> dict[str, str]:
    """Runtime symbol name to the C file that defines it."""

    index: dict[str, str] = {}
    for file in sorted(RUNTIME.glob("*.c")):
        text = file.read_text(encoding="utf-8", errors="replace")
        for match in _DEFINITION.finditer(text):
            index.setdefault(match.group(1), str(file.relative_to(COMPILER_DIR)))
    return index


def data_modules() -> dict[str, set[str]]:
    """Per compiler module: the sibling modules it imports that hold module level data."""

    sources = {f.name: f for f in COMPILER_DIR.glob("*.py")}
    holds_data = {name for name, f in sources.items() if _has_data(f)}
    imports: dict[str, set[str]] = {}
    for name, file in sources.items():
        found: set[str] = set()
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            else:
                continue
            for mod in mods:
                bits = mod.split(".")
                if bits[0] == "pyscriptc" and len(bits) > 1 and f"{bits[1]}.py" in holds_data:
                    found.add(f"{bits[1]}.py")
        imports[name] = found
    return imports


def _has_data(file: Path) -> bool:
    for node in ast.parse(file.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and not t.id.startswith("__") for t in targets):
                return True
    return False


class Tracer:
    """Records the compiler files whose code ran while active."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self._code: dict[object, str | None] = {}

    def _hook(self, frame: Any, event: str, arg: Any) -> None:
        if event != "call":
            return
        code = frame.f_code
        if code not in self._code:
            self._code[code] = rel(code.co_filename) if code.co_filename.startswith(str(COMPILER_DIR)) else None
        name = self._code[code]
        if name:
            self.seen.add(name)

    def __enter__(self) -> Tracer:
        self.seen = set()
        sys.setprofile(self._hook)
        return self

    def __exit__(self, *exc: object) -> None:
        sys.setprofile(None)


def trace_probe(probe: Probe, tracer: Tracer, tmp: Path, symbols: dict[str, str]) -> tuple[set[str], bool]:
    """Lower (and emit) one probe in this process. Returns (touched files, reached clang)."""

    from pyscriptc.ir import validate_module
    from pyscriptc.llvm import emit_llvm
    from pyscriptc.lower import lower_file

    src = tmp / "probe.py"
    src.write_text(probe.source, encoding="utf-8")
    runtime: set[str] = set()
    linked = False
    with tracer:
        try:
            module, _ = lower_file(str(src))
            module.pop("_notes", None)
            if probe.call == "":
                pass  # whole program: the scorer compiles and runs it, so it links too
            elif not module["fences"]:
                validate_module(module)
                text = emit_llvm(module, probe.call, list(probe.args), standalone=True)
                runtime = {symbols[s] for s in _DECLARE.findall(text) if s in symbols}
            linked = not module["fences"]
        except Exception:  # noqa: BLE001 - a build error still ran these files
            linked = False
    return set(tracer.seen) | runtime, linked


def build(probes: list[Probe], path: Path = MAP_PATH, *, refresh: bool = False) -> dict[str, Any]:
    """Trace every probe not already in the map, then write the map atomically."""

    current = load(path) or {"probes": {}}
    entries: dict[str, dict[str, Any]] = {} if refresh else dict(current["probes"])
    symbols = symbol_index()
    data = data_modules()
    all_runtime = runtime_files()
    tracer = Tracer()
    todo = [p for p in probes if entries.get(p.id, {}).get("hash") != probe_hash(p)]
    with tempfile.TemporaryDirectory(prefix="compat2-cov-") as tmp:
        # warm the lazily imported compiler modules so their import time is not charged to a probe
        if todo:
            trace_probe(todo[0], Tracer(), Path(tmp), symbols)
        for probe in todo:
            touched, linked = trace_probe(probe, tracer, Path(tmp), symbols)
            key = set(touched) | set(ENTRY)
            for name in touched:
                key |= data.get(name, set())
            if linked:
                key |= set(all_runtime)
            entries[probe.id] = {
                "hash": probe_hash(probe),
                "touches": sorted(touched),
                "key": sorted(key),
            }
    result = {"compiler_hash": compiler_hash(), "probes": entries}
    write(path, result)
    return result


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: Path = MAP_PATH) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "probes" in data else None


def key_map(data: dict[str, Any], probes: Iterable[Probe] | None = None) -> dict[str, list[str]]:
    """The `{probe id: files}` form the cache hashes into keys.

    With `probes`, an entry traced from a different probe source is dropped, so that probe
    falls back to the whole compiler tree instead of a stale path.
    """

    entries = data["probes"]
    if probes is None:
        return {pid: entry["key"] for pid, entry in entries.items()}
    return {p.id: entries[p.id]["key"] for p in probes if p.id in entries and entries[p.id]["hash"] == probe_hash(p)}


def changed_files(base: str = "HEAD") -> set[str]:
    """Compiler files (relative to src/pyscriptc) changed against `base`, including the work tree."""

    out = subprocess.run(
        ["git", "diff", "--name-only", base, "--", "src/pyscriptc"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "src/pyscriptc"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    prefix = "src/pyscriptc/"
    return {line[len(prefix) :] for line in (out + untracked).splitlines() if line.startswith(prefix)}


def stratified(probes: list[Probe], size: int, seed: int = SEED) -> list[Probe]:
    """A fixed seed sample with at least one probe per (area, category) while `size` allows."""

    if len(probes) <= size:
        return list(probes)
    strata: dict[tuple[str, str], list[Probe]] = defaultdict(list)
    for probe in sorted(probes, key=lambda p: p.id):
        strata[(probe.area, probe.category)].append(probe)
    rng = random.Random(seed)
    keys = sorted(strata)
    for key in keys:
        rng.shuffle(strata[key])
    quota = {k: min(1, len(strata[k])) for k in keys}
    if sum(quota.values()) > size:
        keys = keys[:size]
        return [strata[k][0] for k in keys]
    spare = size - sum(quota.values())
    room = {k: len(strata[k]) - quota[k] for k in keys}
    total = sum(room.values())
    shares = {k: spare * room[k] / total for k in keys}
    for k in keys:
        quota[k] += int(shares[k])
    left = size - sum(quota.values())
    for k in sorted(keys, key=lambda k: (-(shares[k] - int(shares[k])), k))[:left]:
        quota[k] += 1
    chosen = [p for k in keys for p in strata[k][: quota[k]]]
    return sorted(chosen, key=lambda p: p.id)


def select(
    probes: list[Probe], data: dict[str, Any] | None, changed: set[str], *, cap: int = TIER0_CAP
) -> tuple[list[Probe], str]:
    """Tier 0: the probes a diff can move. Returns (probes, why)."""

    if not changed:
        return [], "no compiler file changed"
    shared = sorted(changed & SHARED_CORE)
    if shared:
        return stratified(probes, SHARED_SAMPLE), f"shared core changed ({', '.join(shared)}): stratified sample"
    entries = data["probes"] if data else {}
    hit = [p for p in probes if p.id not in entries or changed & set(entries[p.id]["touches"])]
    why = f"{len(hit)} probes touch the diff"
    if len(hit) > cap:
        return stratified(hit, cap), f"{why}, capped at {cap}"
    return hit, why


def tier0(probes: Iterable[Probe], base: str = "HEAD", path: Path = MAP_PATH) -> tuple[list[Probe], str]:
    """Headroom first: a capped row means the denominator is broken, so Tier 0 refuses."""

    probes = list(probes)
    bad = headroom.capped(probes)
    if bad:
        raise SystemExit(f"refusing Tier 0: {len(bad)} capped rows (see --headroom), first {bad[0].id}")
    return select(probes, load(path), changed_files(base))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="trace probes and write the map")
    b.add_argument("probes")
    b.add_argument("--out", default=str(MAP_PATH))
    b.add_argument("--refresh", action="store_true", help="retrace even unchanged probes")
    s = sub.add_parser("select", help="print the Tier 0 selection")
    s.add_argument("probes")
    s.add_argument("--map", default=str(MAP_PATH))
    s.add_argument("--base", default="HEAD")
    args = parser.parse_args(argv)
    probes = list(load_probes(args.probes))
    if args.cmd == "build":
        data = build(probes, Path(args.out), refresh=args.refresh)
        print(f"coverage map: {len(data['probes'])} probes -> {args.out}")
        return 0
    chosen, why = tier0(probes, args.base, Path(args.map))
    print(f"tier 0: {len(chosen)} probes ({why})", file=sys.stderr)
    for probe in chosen:
        print(probe.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
