# SPDX-License-Identifier: Apache-2.0
"""The pure-dependency IR cache (issue #416).

A pure dependency's lowered IR is a function of its wheel, the compiler and
the target, so a second compile of the same program re-lowers nothing the
first one already lowered. The store sits next to the wheel cache, under
`$PYSC_CACHE_DIR/depir/<wheel sha256>/<compiler key>/`, where the compiler
key is the compiler digest (the version plus a digest of the compiler's own
code and runtime, the `runcache` identity) joined with the target triple. A
compiler change or a different target lands in a different directory, so a
changed compiler never serves stale IR, and a new wheel version has a new
sha256 and misses.

The wheel key alone is not the whole input of a module's lowering: the
lowerer also reads the export table (the other modules' signatures), the
exception-code counter the units before it advanced, the program's import
graph (init injection) and the lowering policy (config, the island lane, the
lane report). So each entry also carries a context record and the export
entries the module read, with the values it saw, and a hit needs all of them
to hold; anything that differs is a miss, never a stale hit. The same
dependency set under the same program (the second compile) hits; a program
that changes what a dependency reads re-lowers it.

Entries are written to a temp file and renamed into place, so concurrent
compiles never see a partial entry. `PYSC_NO_DEP_IR_CACHE=1` bypasses the
cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from pyscriptc.log import emit_event

SCHEMA = 1
STATS_FILE = "stats.json"
_SHA_CHARS = frozenset("0123456789abcdef")
_WHOLE_TABLE = "*"

Reads = dict[tuple[str, str | None], Any]


def enabled() -> bool:
    return os.environ.get("PYSC_NO_DEP_IR_CACHE", "") not in ("1", "true", "yes")


def cache_dir_for(base: Path) -> Path:
    """The IR store beside a deps cache whose parent is `base`."""
    return base / "depir"


def target_triple(target: str | None) -> str:
    """The target the IR is for: the explicit `--target`, else the host's."""
    if target:
        return target
    return f"{platform.machine().lower()}-{sys.platform}"


def compiler_key(triple: str) -> str:
    """The compiler digest and the target triple, one directory name."""
    from pyscriptc import runcache

    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in triple)
    return f"{runcache.compiler_digest()[:20]}-{safe}"


def wheel_sha(path: Path) -> tuple[str, Path] | None:
    """The sha256 of the unpacked wheel a source file lives in, with its root.

    `deps.materialise` writes `<root>/.complete` (sha256 first) into each
    unpacked wheel; the nearest ancestor holding it identifies the wheel.
    """
    for parent in path.resolve().parents:
        marker = parent / ".complete"
        if marker.is_file():
            try:
                first = marker.read_text(encoding="utf-8").splitlines()[0].strip()
            except (OSError, IndexError):
                return None
            if len(first) == 64 and set(first) <= _SHA_CHARS:
                return first, parent
            return None
    return None


def base_of(wheel_root: Path) -> Path:
    """The cache root a wheel root `<base>/deps/src/<sha256>` lives under."""
    if wheel_root.parent.name == "src" and wheel_root.parent.parent.name == "deps":
        return wheel_root.parent.parent.parent
    from pyscriptc import runcache

    return runcache.cache_root()


def encode_reads(reads: Reads) -> list[list[Any]] | None:
    """The reads as a JSON list, `None` when they cannot be cached.

    A table-wide read saw the whole export table, so a unit that did one is
    not worth caching: any other module changing would invalidate it.
    """
    rows: list[list[Any]] = []
    for (module, member), value in sorted(reads.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        if module == _WHOLE_TABLE:
            return None
        rows.append([module, member, value])
    return rows


def decode_reads(rows: list[list[Any]]) -> Reads:
    reads: Reads = {}
    for module, member, value in rows:
        reads[(module, member)] = value
    return reads


def digest_of(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def policy_digest(config: Any, lanes: Any, resource_root: str, ffi: list[Any] | None) -> str | None:
    """The lowering policy of this compile, or None when it cannot be keyed.

    An `--ffi` manifest changes how imports lower and has no stable content
    key here, so a compile that carries one bypasses the cache.
    """
    if ffi:
        return None
    from pyscriptc import boxed, stdlib_queue

    fields: dict[str, Any] = {}
    if config is not None:
        fields = {k: v for k, v in vars(config).items() if k != "origin"}
    return digest_of(
        {
            "config": fields,
            "lanes": lanes or {},
            "resource": resource_root,
            "island": [
                sorted(stdlib_queue._ISLAND_LANES),
                stdlib_queue._ISLAND_DEFAULT,
                sorted(stdlib_queue._NATIVE_PINNED),
            ],
            "defs": boxed.definitions_tier(),
        }
    )


def roots_digest(roots: Any) -> str:
    """The module roots and the top-level names each owns (import resolution input)."""
    rows: list[Any] = []
    for root in getattr(roots, "roots", ()):
        names: list[str] = []
        try:
            for child in root.path.iterdir():
                if child.suffix == ".py" or child.is_dir():
                    names.append(child.name)
        except OSError:
            pass
        rows.append([root.name, root.kind, sorted(names)])
    return digest_of(rows)


class DepIrCache:
    """One compile's view of the persistent store."""

    def __init__(
        self,
        base: Path,
        triple: str,
        shas: dict[str, str],
        common: dict[str, Any],
    ) -> None:
        self.base = base
        self.root = cache_dir_for(base)
        self.triple = triple
        self.key = compiler_key(triple)
        # unit name -> wheel sha256, for the units that live in a wheel.
        self.shas = shas
        self.common = dict(common)
        self.common["wheels"] = digest_of(sorted(set(shas.values())))
        self.common["triple"] = triple
        self.wheels: dict[str, dict[str, int]] = {}
        # units whose catalog result came from the store this compile.
        self.cat_hits: set[str] = set()
        self._swept: set[str] = set()
        self.finished = False

    # ---------------------------------------------------------- entries

    def cacheable(self, unit: str) -> bool:
        return unit in self.shas

    def _path(self, sha: str, kind: str, unit: str) -> Path:
        return self.root / sha / self.key / f"{kind}-{unit}.json"

    def _bump(self, unit: str, field: str) -> None:
        if unit not in self.shas:
            return
        sha = self.shas[unit]
        found = self.wheels.get(sha)
        if found is None:
            found = {"hits": 0, "misses": 0, "stored": 0}
            self.wheels[sha] = found
        found[field] += 1

    def load(self, kind: str, unit: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """The stored payload of one unit when its context matches, else None."""
        if unit not in self.shas:
            return None
        path = self._path(self.shas[unit], kind, unit)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if record.get("schema") != SCHEMA or record.get("ctx") != {**self.common, **ctx}:
            return None
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else None

    def store(self, kind: str, unit: str, ctx: dict[str, Any], payload: dict[str, Any]) -> bool:
        """Write one entry atomically; False when it is not cacheable or unwritable."""
        if unit not in self.shas:
            return False
        sha = self.shas[unit]
        path = self._path(sha, kind, unit)
        record = {"schema": SCHEMA, "unit": unit, "sha256": sha, "ctx": {**self.common, **ctx}, "payload": payload}
        tmp: str | None = None
        try:
            # One dump is the snapshot (the caller keeps mutating its module)
            # and the round-trip check: a payload JSON would change (a tuple,
            # an int key) is not cached, so a hit is always equal to a miss.
            text = json.dumps(record)
            if json.loads(text)["payload"] != payload:
                return False
            self._sweep(sha)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".entry-", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)
            return False
        return True

    def _sweep(self, sha: str) -> None:
        """Drop the wheel's entries of other compiler keys (an older compiler or target)."""
        if sha in self._swept:
            return
        self._swept.add(sha)
        wheel_dir = self.root / sha
        try:
            children = list(wheel_dir.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and child.name != self.key and child.name.split("-", 1)[0] != self.key.split("-", 1)[0]:
                # a different compiler digest: never served again.
                shutil.rmtree(child, ignore_errors=True)

    # ---------------------------------------------------------- tally

    def hit(self, unit: str) -> None:
        self._bump(unit, "hits")

    def miss(self, unit: str) -> None:
        self._bump(unit, "misses")

    def stored(self, unit: str) -> None:
        self._bump(unit, "stored")

    def finish(self) -> dict[str, dict[str, Any]]:
        """Emit the compile's audit events and fold the tally into the store's stats.

        Returns the per-wheel summary (also the audit event fields).
        """
        if self.finished:
            return {}
        self.finished = True
        provides: dict[str, set[str]] = {}
        for unit, sha in self.shas.items():
            provides.setdefault(sha, set()).add(unit.split(".", 1)[0])
        summary: dict[str, dict[str, Any]] = {}
        total_hits = 0
        total_misses = 0
        for sha, tally in sorted(self.wheels.items()):
            fields: dict[str, Any] = {
                "sha256": sha,
                "imports": sorted(provides.get(sha, ())),
                "state": "hit" if tally["hits"] and not tally["misses"] else "miss",
                "hits": tally["hits"],
                "misses": tally["misses"],
                "stored": tally["stored"],
                "key": self.key,
                "triple": self.triple,
            }
            summary[sha] = fields
            emit_event("info", "deps.ir_cache", fields)
            total_hits += tally["hits"]
            total_misses += tally["misses"]
        if summary:
            _bump_stats(self.root, total_hits, total_misses)
        return summary


def _bump_stats(root: Path, hits: int, misses: int) -> None:
    path = root / STATS_FILE
    current = _read_stats(root)
    current["hits"] += hits
    current["misses"] += misses
    current["compiles"] += 1
    tmp: str | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".stats-", dir=root)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle)
        os.replace(tmp, path)
    except OSError:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def _read_stats(root: Path) -> dict[str, int]:
    out = {"hits": 0, "misses": 0, "compiles": 0}
    try:
        data = json.loads((root / STATS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for key in out:
            value = data.get(key)
            if isinstance(value, int):
                out[key] = value
    return out


def report(base: Path, shas: list[str], target: str | None = None) -> dict[str, Any]:
    """The cache's size, hit rate and per-wheel state (the `deps --json` block).

    `base` is the cache root that holds `deps/` and `depir/`. A wheel's state
    is `hit` when the store holds IR for it under the running compiler's key
    and the target (the next compile of it re-lowers nothing), else `miss`.
    """
    root = cache_dir_for(base)
    triple = target_triple(target)
    key = compiler_key(triple)
    entries = 0
    size = 0
    if root.is_dir():
        for path in root.rglob("*.json"):
            if path.name == STATS_FILE:
                size += _size(path)
                continue
            entries += 1
            size += _size(path)
    stats = _read_stats(root)
    lookups = stats["hits"] + stats["misses"]
    wheels: dict[str, dict[str, Any]] = {}
    for sha in shas:
        count = len(list((root / sha / key).glob("ir-*.json"))) if (root / sha / key).is_dir() else 0
        wheels[sha] = {"state": "hit" if count else "miss", "modules": count}
    return {
        "enabled": enabled(),
        "dir": str(root),
        "key": key,
        "triple": triple,
        "entries": entries,
        "bytes": size,
        "hits": stats["hits"],
        "misses": stats["misses"],
        "compiles": stats["compiles"],
        "hitRate": round(stats["hits"] / lookups, 4) if lookups else None,
        "wheels": wheels,
    }


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def annotate(deps_report: dict[str, Any], base: Path) -> None:
    """Add the cache block and each pure row's cached state to a deps report.

    The block is `irCache` (size, hit rate, the compiler key and target); a
    pure distribution's row gains `ir_cache` (`hit` or `miss`) and
    `ir_modules` (the entries the store holds for its wheel).
    """
    rows: list[dict[str, Any]] = deps_report["packages"]
    shas = [row["sha256"] for row in rows if row["lane"] == "pure" and row["sha256"]]
    block = report(base, shas)
    deps_report["irCache"] = block
    wheels = block["wheels"]
    for row in rows:
        found = wheels.get(row["sha256"]) if row["lane"] == "pure" and row["sha256"] else None
        if found is not None:
            row["ir_cache"] = found["state"]
            row["ir_modules"] = found["modules"]


def summary_lines(deps_report: dict[str, Any]) -> list[str]:
    """The text report's cache lines: totals, then one line per pure wheel."""
    block = deps_report.get("irCache")
    if not block:
        return []
    rate = block["hitRate"]
    rate_text = (
        "no compiles yet"
        if rate is None
        else f"hit rate {rate * 100:.0f}% ({block['hits']} hits, {block['misses']} misses)"
    )
    state = "enabled" if block["enabled"] else "disabled (PYSC_NO_DEP_IR_CACHE)"
    lines = [f"ir cache: {state}, {block['entries']} entries, {block['bytes']} bytes, {rate_text}"]
    for row in deps_report["packages"]:
        if "ir_cache" in row:
            lines.append(f"  {row['name']} {row['version']}: {row['ir_cache']} ({row['ir_modules']} module(s) cached)")
    return lines
