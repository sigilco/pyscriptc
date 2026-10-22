"""Per-definition generator census over the pinned top100 sources.

Run: python -m benchmarks.compat2.census_defs [--src DIR] [--json OUT]

The v0.6.0 generator receipts are stated per definition (issue #165 and the
P1 exit criteria in `.agents/plans/2026-10-21-async-threads-design.md`):
how many of the corpus's generator functions and `@contextmanager`
definitions still carry a generator fence, and how many lower clean. The
package census (`benchmarks.compat2.census`) counts fences by family and
cannot answer this: generator fences hide inside `construct-other` and a
generator can be blocked by an unrelated annotation fence.

A definition is a generator when its own body (nested defs excluded)
contains a `yield` or `yield from`, or when a sync `@contextmanager`
decorator names it. It "keeps a generator fence" when one of the generator
lowering messages sits on one of its lines:

- `yield is not lowered` and `yield from is not lowered` (the pass never ran)
- `bare yield is not lowered yet`
- `yield as an expression (send) is not lowered yet`
- `yield inside try or with is not lowered yet`
- `return with a value in a generator is not lowered yet`
- `a generator is annotated Iterator[T], Iterable[T] or Generator[T, None, None]`
- `a bare or starred parameter form in a resumable is not lowered yet`
- `a nested def or class in a resumable is not lowered yet`
- `a lambda in a resumable is not lowered yet`
- `a resumable whose locals a closure captures is not lowered yet`
- `a generic resumable is not lowered yet`
- `Generator[T, S, R] with send or return types is not lowered yet`
- `a suspension inside try or with is not lowered yet`
- `return inside try or with in a resumable is not lowered yet`

Everything else blocking the definition (an unlowered annotation, a name
resolution miss) is another family's fence and does not count here.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyscriptc.package import lower_directory

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "benchmarks" / "compat" / "top100" / "src"

GENERATOR_FENCE = re.compile(
    r"|".join(
        (
            r"^yield (?:from )?is not lowered$",
            r"^bare yield is not lowered yet$",
            r"^yield as an expression \(send\) is not lowered yet$",
            r"^yield inside try or with is not lowered yet$",
            r"^return with a value in a generator is not lowered yet$",
            r"^a generator is annotated Iterator\[T\], Iterable\[T\] or Generator\[T, None, None\]$",
            r"^a nested def or class in a resumable is not lowered yet$",
            r"^return inside try or with in a resumable is not lowered yet$",
            r"^a suspension inside try or with is not lowered yet$",
            r"^a generic resumable is not lowered yet$",
            r"^Generator\[T, S, R\] with send or return types is not lowered yet$",
            r"^a resumable whose locals a closure captures is not lowered yet$",
            r"^a lambda in a resumable is not lowered yet$",
            r"^bare yield in a resumable is not lowered yet$",
        )
    )
)


def _definitions(tree: ast.AST, prefix: str = "") -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((f"{prefix}{child.name}", child))
            found.extend(_definitions(child, f"{prefix}{child.name}."))
        elif isinstance(child, ast.ClassDef):
            found.extend(_definitions(child, f"{prefix}{child.name}."))
        else:
            found.extend(_definitions(child, prefix))
    return found


def _own_yields(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Yield | ast.YieldFrom]:
    nested: set[int] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub is not fn:
            for node in ast.walk(sub):
                if isinstance(node, (ast.Yield, ast.YieldFrom)):
                    nested.add(id(node))
    return [node for node in ast.walk(fn) if isinstance(node, (ast.Yield, ast.YieldFrom)) and id(node) not in nested]


def _is_contextmanager(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if isinstance(fn, ast.AsyncFunctionDef):
        return False
    for deco in fn.decorator_list:
        if isinstance(deco, ast.Attribute) and deco.attr == "contextmanager":
            return True
        if isinstance(deco, ast.Name) and deco.id == "contextmanager":
            return True
    return False


def package_defs(src: Path) -> dict[str, Any]:
    """Per-package generator and contextmanager definition counts."""

    packages: list[dict[str, Any]] = []
    for pkg in sorted(p for p in src.iterdir() if p.is_dir()):
        try:
            module, _ = lower_directory(pkg)
        except Exception as exc:  # noqa: BLE001 - the census reports and continues
            packages.append({"package": pkg.name, "error": repr(exc)})
            continue
        fences = [f for f in module["fences"] if f.get("span")]
        by_line: dict[tuple[str, int], list[str]] = {}
        for fence in fences:
            key = (fence["span"]["file"], fence["span"]["line"])
            by_line.setdefault(key, []).append(fence["message"])
        generators = kept = 0
        managers = kept_cm = 0
        kept_messages: dict[str, int] = {}
        for path in sorted(pkg.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for _name, fn in _definitions(tree):
                is_gen = bool(_own_yields(fn))
                is_cm = _is_contextmanager(fn)
                if not is_gen and not is_cm:
                    continue
                messages: list[str] = []
                for line in range(fn.lineno, (fn.end_lineno or fn.lineno) + 1):
                    messages.extend(by_line.get((str(path), line), ()))
                gen_fences = [m for m in messages if GENERATOR_FENCE.match(m)]
                if is_gen:
                    generators += 1
                    if gen_fences:
                        kept += 1
                        for m in gen_fences:
                            kept_messages[m] = kept_messages.get(m, 0) + 1
                if is_cm:
                    managers += 1
                    if gen_fences:
                        kept_cm += 1
        packages.append(
            {
                "package": pkg.name,
                "generators": generators,
                "generatorsFenced": kept,
                "contextmanagers": managers,
                "contextmanagersFenced": kept_cm,
                "fences": [m for m in kept_messages for _ in range(kept_messages[m])],
            }
        )
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "command": "python -m benchmarks.compat2.census_defs",
        "src": str(src),
        "packages": packages,
        "generators": sum(p.get("generators", 0) for p in packages),
        "generatorsFenced": sum(p.get("generatorsFenced", 0) for p in packages),
        "contextmanagers": sum(p.get("contextmanagers", 0) for p in packages),
        "contextmanagersFenced": sum(p.get("contextmanagersFenced", 0) for p in packages),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    report = package_defs(args.src)
    gens_clean = report["generators"] - report["generatorsFenced"]
    cm_clean = report["contextmanagers"] - report["contextmanagersFenced"]
    summary = (
        f"generators: {gens_clean}/{report['generators']} clean, "
        f"contextmanagers: {cm_clean}/{report['contextmanagers']} clean"
    )
    print(summary)
    for pkg in report["packages"]:
        if pkg.get("error"):
            print(f"  {pkg['package']}: ERROR {pkg['error']}")
            continue
        if pkg["generatorsFenced"] or pkg["contextmanagersFenced"]:
            print(
                f"  {pkg['package']}: {pkg['generators'] - pkg['generatorsFenced']}/{pkg['generators']} gens, "
                f"{pkg['contextmanagers'] - pkg['contextmanagersFenced']}/{pkg['contextmanagers']} cm clean"
            )
    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
