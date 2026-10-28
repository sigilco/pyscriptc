"""Generate the key tables in docs/reference/config.md from src/pyscriptc/config.py.

The accepted keys, their types, their allowed values and the import lanes all
come from the config module, so the page never drifts from the code. The prose
around the tables is hand written and stays outside the generated markers.

Run: python scripts/gen_config_docs.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyscriptc.config import _CHOICES, _KEYS, DEFAULT_LANE, LANES  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "reference" / "config.md"

BEGIN = "<!-- gen:keys -->"
END = "<!-- /gen:keys -->"

# What each key does. Written here because the module holds types, not prose.
DESCRIPTIONS: dict[str, str] = {
    "entry": "entry point function or module stem, used by project mode",
    "src": "module root, used by project mode",
    "checker": "type checker run before compiling",
    "driver": "C toolchain driver",
    "target": "cross-compilation triple for `zig cc`",
    "fail-on": "exit code rule for `pyscriptc coverage`",
    "stub-path": "extra stub roots, relative to this file",
    "ffi": "extern manifests, relative to this file",
    "imports": "import lane per top-level module name",
    "fallback": "fallback mode settings: `definitions`, or the tier `cpython`",
    "island": "embedded CPython settings: `embed` and `stdlib`",
}

LANE_DESCRIPTIONS: dict[str, str] = {
    "compile": "compile from source, and stop with a lane hint when it fails",
    "replace": "a curated native replacement covers this import",
    "extern": "bound through the C ABI, with an `ffi.toml` manifest",
    "island": "run through embedded CPython; the binary needs libpython",
    "refuse": "refuse the compile with a message naming the lane",
}

TYPE_NAMES = {
    str: "string",
    bool: "boolean",
    int: "integer",
    dict: "table",
    list: "list",
}


def _type_names(types: tuple[type, ...]) -> str:
    return " or ".join(f"`{TYPE_NAMES.get(t, t.__name__)}`" for t in types)


def render_keys() -> str:
    lines = ["| Key | Type | Values | What it does |", "| --- | ---- | ------ | ----------- |"]
    for key, types in _KEYS.items():
        choices = _CHOICES.get(key)
        values = " or ".join(choices) if choices else ""
        lines.append(f"| `{key}` | {_type_names(types)} | {values} | {DESCRIPTIONS.get(key, '')} |")
    return "\n".join(lines)


def render_lanes() -> str:
    lines = ["| Lane | What it does |", "| ---- | ------------ |"]
    for lane in LANES:
        lines.append(f"| `{lane}` | {LANE_DESCRIPTIONS.get(lane, '')} |")
    lines.append("")
    lines.append(f"Without a matching entry the lane is `{DEFAULT_LANE}`.")
    return "\n".join(lines)


def render() -> str:
    text = DEFAULT_OUT.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{DEFAULT_OUT} is missing the {BEGIN} / {END} markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    body = render_keys() + "\n\n<!-- gen:lanes -->\n" + render_lanes() + "\n<!-- /gen:lanes -->"
    return f"{head}{BEGIN}\n{body}\n{END}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit nonzero when the page is out of date")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path (default docs/reference/config.md)")
    args = ap.parse_args()

    content = render()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != content:
            print(f"{args.out} is out of date. Run: uv run python scripts/gen_config_docs.py", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
