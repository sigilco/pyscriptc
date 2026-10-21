"""Facts-oracle sanity diff: run every FactsProvider over one sample file.

Prints a coverage/agreement table — how many expression positions each
oracle typed, and where their resolved kinds disagree at shared positions.
This is a development tool for the checker-adapters work; it is NOT part
of the compat2 census and must not ingest corpus files.

    uv run python -m benchmarks.factsdiff [file]
    uv run python -m benchmarks.factsdiff --corpus [N]

With no file it uses the built-in ~30-line sample. The mypy lane is an
optional cross-check (install the `oracle-mypy` extra); without mypy the
tool runs the pyrefly lanes only. `--corpus` runs pyrefly-tsp (and mypy, if
installed) over N (default 12, minimum 12) visible files under
benchmarks/compat/top100/src and writes benchmarks/factsdiff-report.json
(gitignored): expressions typed per oracle, kind-level agreement at shared
positions, callables recovered, and pyrefly-tsp coverage as a share of
mypy's typed positions (the gate for flipping the default oracle).
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from pathlib import Path

from pyscriptc.typefacts import (
    CallableFacts,
    ModuleFacts,
    PyreflyFactsAdapter,
    PyreflyTspFactsAdapter,
    TypeFacts,
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


def _mypy_kind(typ: object) -> str:
    from mypy.types import AnyType, CallableType, Instance, NoneType, TupleType, UnionType, get_proper_type

    typ = get_proper_type(typ)  # type: ignore[arg-type]
    if isinstance(typ, Instance):
        return typ.type.name
    for cls, kind in (
        (NoneType, "none"),
        (UnionType, "union"),
        (AnyType, "any"),
        (CallableType, "callable"),
        (TupleType, "tuple"),
    ):
        if isinstance(typ, cls):
            return kind
    return "unknown"


def _iter_mypy_nodes(root: object):
    """Yield every mypy node under `root` (mypyc classes cannot be visitor-subclassed)."""
    from mypy.nodes import Node

    stack = [root]
    visited: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        yield node
        for attr in getattr(type(node), "__mypyc_attrs__", ()):
            value = getattr(node, attr, None)
            if isinstance(value, Node):
                stack.append(value)
            elif isinstance(value, (list, tuple)):
                stack.extend(item for item in value if isinstance(item, Node))


class MypyKindOracle:
    """Optional cross-check: mypy's expression TypeMap reduced to kinds.

    Not a FactsProvider (the full mypy adapter was removed in v0.3.1); it
    only reports what factsdiff compares: typed positions and callables.
    """

    name = "mypy"

    @staticmethod
    def available() -> bool:
        try:
            import mypy  # noqa: F401
        except ImportError:
            return False
        return True

    def facts(self, path: Path) -> ModuleFacts:
        from mypy import build as mypy_build
        from mypy.modulefinder import BuildSource
        from mypy.nodes import Expression, FuncDef
        from mypy.options import Options

        path = path.resolve()
        with tempfile.TemporaryDirectory(prefix="factsdiff-mypy-") as cache:
            options = Options()
            options.incremental = False
            options.follow_imports = "silent"
            options.cache_dir = cache
            options.preserve_asts = True
            options.export_types = True
            result = mypy_build.build(sources=[BuildSource(str(path), None, None)], options=options)
        if result.errors:
            raise RuntimeError("\n".join(result.errors))
        module = next(t for t in result.files.values() if Path(t.path) == path)
        expressions: dict[str, TypeFacts] = {}
        callables: dict[str, CallableFacts] = {}
        for node in _iter_mypy_nodes(module):
            if isinstance(node, Expression) and node in result.types:
                expressions.setdefault(f"{node.line}:{node.column}", TypeFacts(kind=_mypy_kind(result.types[node])))
            elif isinstance(node, FuncDef) and node.fullname.startswith(f"{module.fullname}."):
                callables[node.fullname] = CallableFacts(parameters=(), return_type=TypeFacts(kind="unknown"))
        return ModuleFacts(path=path, provider=self.name, expressions=expressions, callables=callables, overloads={})


CORPUS = Path(__file__).parent / "compat" / "top100" / "src"
REPORT = Path(__file__).parent / "factsdiff-report.json"


def _kind(fact: object) -> str:
    """Kind-level classifier used for agreement: literals fold into their base."""
    kind = getattr(fact, "kind", "unknown")
    if kind == "literal" and getattr(fact, "args", ()):
        return fact.args[0].kind
    return kind


def _sample_files(count: int) -> list[Path]:
    # Relative-import files are skipped: the facts adapters check one file as
    # `__main__`, where mypy cannot resolve `from .x import y`.
    files = sorted(
        p
        for p in CORPUS.rglob("*.py")
        if 40 <= len(p.read_text().splitlines()) <= 600 and not re.search(r"^\s*from \.", p.read_text(), re.M)
    )
    # Spread across packages: one file per package first, then fill.
    by_pkg: dict[str, list[Path]] = {}
    for path in files:
        by_pkg.setdefault(path.relative_to(CORPUS).parts[0], []).append(path)
    picked: list[Path] = []
    depth = 0
    while len(picked) < count and any(len(v) > depth for v in by_pkg.values()):
        for group in by_pkg.values():
            if len(group) > depth and len(picked) < count:
                picked.append(group[depth])
        depth += 1
    return picked


def corpus_report(count: int) -> int:
    paths = _sample_files(max(count, 12))
    totals = {"mypy": 0, "pyrefly-tsp": 0, "shared": 0, "agree": 0, "mypy_callables": 0, "tsp_callables": 0}
    rows = []
    for path in paths:
        row: dict[str, object] = {"file": str(path.relative_to(CORPUS))}
        results = {}
        lanes = [("pyrefly-tsp", PyreflyTspFactsAdapter())]
        if MypyKindOracle.available():
            lanes.insert(0, ("mypy", MypyKindOracle()))
        for name, provider in lanes:
            try:
                results[name] = provider.facts(path)
            except Exception as exc:  # noqa: BLE001 — report, never abort
                row[f"{name}_error"] = str(exc)[:200]
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
        mypy, tsp = results.get("mypy"), results.get("pyrefly-tsp")
        # mypy's TypeMap also holds synthetic nodes (e.g. "4:-1"); count only
        # positions where this file has a real ast expression.
        valid = {f"{n.lineno}:{n.col_offset}" for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.expr)}
        for facts in (mypy, tsp):
            if facts is not None:
                object.__setattr__(facts, "expressions", {k: v for k, v in facts.expressions.items() if k in valid})
        if mypy is not None:
            row["mypy_exprs"] = len(mypy.expressions)
            row["mypy_callables"] = len(mypy.callables) + len(mypy.overloads)
            totals["mypy"] += len(mypy.expressions)
            totals["mypy_callables"] += len(mypy.callables) + len(mypy.overloads)
        if tsp is not None:
            row["tsp_exprs"] = len(tsp.expressions)
            row["tsp_callables"] = len(tsp.callables) + len(tsp.overloads)
            totals["pyrefly-tsp"] += len(tsp.expressions)
            totals["tsp_callables"] += len(tsp.callables) + len(tsp.overloads)
        if mypy is not None and tsp is not None:
            shared = set(mypy.expressions) & set(tsp.expressions)
            agree = sum(1 for k in shared if _kind(mypy.expressions[k]) == _kind(tsp.expressions[k]))
            row["shared"] = len(shared)
            row["agree"] = agree
            row["mypy_positions_covered"] = len(shared)
            totals["shared"] += len(shared)
            totals["agree"] += agree
            row["callables_shared"] = len(
                (set(mypy.callables) | set(mypy.overloads)) & (set(tsp.callables) | set(tsp.overloads))
            )
        rows.append(row)
        print(row)
    summary = {
        "files": len(rows),
        "mypy_typed": totals["mypy"],
        "tsp_typed": totals["pyrefly-tsp"],
        "coverage_of_mypy_positions": (totals["shared"] / totals["mypy"]) if totals["mypy"] else None,
        "kind_agreement_at_shared": (totals["agree"] / totals["shared"]) if totals["shared"] else None,
        "mypy_callables": totals["mypy_callables"],
        "tsp_callables_recovered": totals["tsp_callables"],
    }
    REPORT.write_text(json.dumps({"summary": summary, "files": rows}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--corpus":
        return corpus_report(int(sys.argv[2]) if len(sys.argv) > 2 else 12)
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).resolve()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="factsdiff-"))
        path = tmp / "sample.py"
        path.write_text(SAMPLE)
    providers: list = [("pyrefly", PyreflyFactsAdapter()), ("pyrefly-tsp", PyreflyTspFactsAdapter())]
    if MypyKindOracle.available():
        providers.insert(0, ("mypy", MypyKindOracle()))
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
