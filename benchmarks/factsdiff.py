"""Facts-oracle sanity diff: run every FactsProvider over one sample file.

Prints a coverage/agreement table — how many expression positions each
oracle typed, and where their resolved kinds disagree at shared positions.
This is a development tool for the checker-adapters work; it is NOT part
of the compat2 census and must not ingest corpus files.

    uv run python -m benchmarks.factsdiff [file]

With no file it uses the built-in ~30-line sample.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from pyscriptc.typefacts import (
    ModuleFacts,
    MypyFactsAdapter,
    PyreflyFactsAdapter,
    PyrightFactsAdapter,
    TSPFactsAdapter,
)

SAMPLE = '''\
"""Facts-oracle sanity sample: annotations, calls, generics, overloads."""

from typing import overload


def add(a: int, b: int = 2) -> int:
    return a + b


@overload
def pick(x: int) -> int: ...
@overload
def pick(x: str) -> str: ...
def pick(x):
    return x


class Box:
    def meth(self, x: int) -> str:
        return str(x)


def outer():
    def inner(z: int) -> int:
        return z
    return inner(3)


nums: list[int] = [1, 2, 3]
total = add(nums[0])
name = "hi"
up = name.upper()
box = Box()
label = box.meth(5)
chosen = pick("a")
maybe: int | None = add(1)
'''


def _describe(facts: ModuleFacts) -> dict[str, str]:
    """position -> rendered kind for agreement comparison."""
    return {
        key: (fact.runtime_repr or fact.kind) if fact.kind not in {"union", "callable"} else fact.kind
        for key, fact in facts.expressions.items()
    }


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).resolve()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="factsdiff-"))
        path = tmp / "sample.py"
        path.write_text(SAMPLE)
    providers = [
        ("mypy", MypyFactsAdapter()),
        ("pyright", PyrightFactsAdapter()),
        ("pyrefly", PyreflyFactsAdapter()),
        ("tsp", TSPFactsAdapter()),
    ]
    results: dict[str, ModuleFacts | str] = {}
    for name, provider in providers:
        try:
            results[name] = provider.facts(path)
        except Exception as exc:  # noqa: BLE001 — diff tool reports, never aborts
            results[name] = f"FAILED: {exc}"
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    print(f"sample: {path}\n")
    header = f"{'oracle':<10} {'exprs':>6} {'callables':>10} {'overloads':>10}  status"
    print(header)
    print("-" * len(header))
    for name, _ in providers:
        facts = results[name]
        if isinstance(facts, str):
            print(f"{name:<10} {'-':>6} {'-':>10} {'-':>10}  {facts[:80]}")
        else:
            print(f"{name:<10} {len(facts.expressions):>6} {len(facts.callables):>10} {len(facts.overloads):>10}  ok")

    ok = {name: facts for name, facts in results.items() if isinstance(facts, ModuleFacts)}
    if len(ok) < 2:
        return 1

    # Coverage overlap + agreement on shared positions.
    print("\npositions typed by >=2 oracles: agreement on rendered type")
    print("-" * 70)
    descriptions = {name: _describe(facts) for name, facts in ok.items()}
    shared = set.intersection(*(set(d) for d in descriptions.values())) if descriptions else set()
    disagreements = 0
    for key in sorted(shared, key=lambda k: tuple(int(p) for p in k.split(":"))):
        renders = {name: desc[key] for name, desc in descriptions.items()}
        marker = "" if len(set(renders.values())) == 1 else "  <-- DISAGREE"
        if marker:
            disagreements += 1
        print(f"  {key:<8} " + "  ".join(f"{n}={r}" for n, r in renders.items()) + marker)
    union_all = set().union(*[set(d) for d in descriptions.values()])
    print(
        f"\n{len(shared)} shared positions, {disagreements} disagreements, "
        f"{len(union_all)} total typed positions across {len(ok)} oracles"
    )

    # Callable signature agreement.
    all_names = set().union(*[set(f.callables) | set(f.overloads) for f in ok.values()])
    if all_names:
        print("\ncallables (name -> per-oracle return kind):")
        for name in sorted(all_names):
            row = []
            for oracle, facts in ok.items():
                sig = facts.signature(name)
                variants = facts.overload_choices(name)
                if sig is not None:
                    row.append(f"{oracle}={sig.return_type.kind}")
                elif variants:
                    row.append(f"{oracle}=[{','.join(v.return_type.kind for v in variants)}]")
                else:
                    row.append(f"{oracle}=-")
            print(f"  {name:<28} " + "  ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
