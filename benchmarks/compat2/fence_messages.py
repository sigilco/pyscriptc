"""Census of fence (PS-code) messages emitted by the lowering tree.

Run: python -m benchmarks.compat2.fence_messages [--out FILE]

Walks ``src/pyscriptc/lower/*.py`` and ``src/pyscriptc/restlower/*.py`` for
``.fail(...)`` calls and records every (code, message) pair with its location.
Literal message parts are kept verbatim; interpolated or dynamic parts render
as ``{}``. The output is a sorted JSON list of ``{code, message, file, line}``
records, byte-stable across runs, so a diff between two commits shows every
fence-message edit before the flip-set probes do.

``--out`` writes the JSON plus a trailing newline. Stdout always prints a
count of sites per PS code.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOWER_DIR = ROOT / "src" / "pyscriptc" / "lower"
EXTRA_FILES = sorted((ROOT / "src" / "pyscriptc" / "restlower").glob("*.py"))


def _render(node: ast.AST) -> str:
    """Render a message expression: literal text kept, dynamic parts as {}."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else "{}"
    if isinstance(node, ast.JoinedStr):
        return "".join("{}" if isinstance(part, ast.FormattedValue) else _render(part) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left) + _render(node.right)
    return "{}"


def _render_code(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _call_arg(call: ast.Call, index: int, keyword: str) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _is_fail_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "fail"


def iter_fail_sites(path: Path) -> list[dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = path.name
    sites = []
    for node in ast.walk(tree):
        if not _is_fail_call(node):
            continue
        code_node = _call_arg(node, 1, "code")
        message_node = _call_arg(node, 2, "message")
        if code_node is None or message_node is None:
            continue
        sites.append(
            {
                "code": _render_code(code_node),
                "message": _render(message_node),
                "file": rel,
                "line": node.lineno,
            }
        )
    return sites


def census(lower_dir: Path = LOWER_DIR, extra_files: list[Path] = EXTRA_FILES) -> list[dict[str, Any]]:
    files = sorted(lower_dir.glob("*.py")) + [f for f in extra_files if f.exists()]
    sites = [site for path in files for site in iter_fail_sites(path)]
    return sorted(sites, key=lambda s: (s["code"], s["file"], s["line"], s["message"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fence_messages")
    parser.add_argument("--out", type=Path, help="write the JSON inventory here")
    args = parser.parse_args(argv)

    sites = census()
    if args.out:
        args.out.write_bytes((json.dumps(sites, indent=2, sort_keys=True) + "\n").encode())
    counts: dict[str, int] = {}
    for site in sites:
        counts[site["code"]] = counts.get(site["code"], 0) + 1
    for code in sorted(counts):
        print(f"{code}: {counts[code]}")
    print(f"total: {len(sites)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
