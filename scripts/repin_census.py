"""Regenerate the per-module census pins under tests/census_pins/.

Run: uv run python scripts/repin_census.py [--src DIR]

Each shard `tests/census_pins/<module>.json` holds one
`{"<module>": <fence ceiling>}` entry, so a repin rewrites only the shards
whose measured count moved and PRs that lower fences in different modules
never share a file. Only this script writes the shards; the census test
aggregates them with `load_pins`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.compat2.census_self import DEFAULT_SRC, build_self_census

from pyscriptc.package import _discover

PINS_DIR = Path(__file__).resolve().parent.parent / "tests" / "census_pins"


def load_pins(pins_dir: Path = PINS_DIR) -> dict[str, int]:
    """Aggregate the per-module shards into `{module: fence ceiling}`."""
    pins: dict[str, int] = {}
    for shard in sorted(pins_dir.glob("*.json")):
        data = json.loads(shard.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and len(data) == 1, f"{shard}: expected one module pin"
        ((module, count),) = data.items()
        assert module == shard.stem, f"{shard}: pin key {module!r} does not match the file name"
        assert isinstance(count, int), f"{shard}: pin value must be an int"
        pins[module] = count
    return pins


def module_fences(src: Path) -> dict[str, int]:
    census = build_self_census(src)
    units, _skipped = _discover(src)
    names = [
        ".".join([src.name, *path.relative_to(src).with_suffix("").parts]).removesuffix(".__init__")
        for _name, path, _is_package in units
    ]
    assert len(names) == len(census["packages"])
    return {name: pkg["fences"] for name, pkg in zip(names, census["packages"], strict=True)}


def write_pins(pins: dict[str, int], pins_dir: Path = PINS_DIR) -> list[str]:
    """Write one shard per module; returns the names of shards that changed."""
    pins_dir.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for name, count in sorted(pins.items()):
        shard = pins_dir / f"{name}.json"
        text = json.dumps({name: count}) + "\n"
        if not shard.is_file() or shard.read_text(encoding="utf-8") != text:
            shard.write_text(text, encoding="utf-8")
            changed.append(name)
    for shard in sorted(pins_dir.glob("*.json")):
        if shard.stem not in pins:
            shard.unlink()
            changed.append(f"-{shard.stem}")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    args = parser.parse_args(argv)
    pins = module_fences(args.src.resolve())
    changed = write_pins(pins)
    print(f"wrote {PINS_DIR} ({len(pins)} modules, {sum(pins.values())} fences, {len(changed)} shard(s) changed)")
    if changed:
        print("changed shards: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
