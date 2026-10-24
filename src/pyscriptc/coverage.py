"""Collect-all fence report behind `pyscriptc coverage`.

Lowering already keeps going after a fence inside a body, a class, or a module
statement. This module runs it on a file or a package tree, then classifies
each fence into a feature family, attaches a refactor hint and a code frame,
and groups the fences by the definition that holds them. It never changes how
`compile` treats a fence.
"""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyscriptc.errors import IRError, format_diagnostic
from pyscriptc.imports import REQUIRES_LIBPYTHON
from pyscriptc.lower import lower_file
from pyscriptc.package import _discover, lower_directory
from pyscriptc.reflection import mechanism_of, routes_to_island

SCHEMA_VERSION = 2

# A definition is routed by the fences inside it: island when one traces to
# runtime reflection, fenced when any other fence sits in it, native otherwise.
CAP_NATIVE = "native"
CAP_FENCED = "fenced"
CAP_ISLAND = "island"


def deps_run_deps(source: Path, *, cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Indirection over `deps.run_deps` so tests can fake the resolver."""
    from pyscriptc import deps

    return deps.run_deps(source, cache_dir=cache_dir)


@dataclass(frozen=True)
class Family:
    key: str
    hint: str
    derived: bool = False


_FAMILIES = {
    family.key: family
    for family in (
        Family(
            "reflection",
            "Native code keeps no runtime type, signature or annotation table. Use the typed "
            "idiom the fence names, or let the definition route to the island lane.",
        ),
        Family(
            "union-types",
            "Narrow the annotation to one arm, or split the function per arm. A tagged union is a compiler epic.",
        ),
        Family(
            "optional-types",
            "Accept or return T and signal absence with a sentinel. Optional[T] needs the tagged-union epic.",
        ),
        Family(
            "generic-alias", "Use list[T], dict[K, V] or tuple[...] in the signature instead of the abstract generic."
        ),
        Family(
            "dynamic-annotation",
            "Annotate with the concrete type. object, Any, Protocol, TypeVar and Callable do not lower.",
        ),
        Family("forward-reference", "Define the class before use and write the annotation unquoted."),
        Family(
            "unlowered-type-name",
            "Replace the alias or imported name with a lowered type, or lower the class it names.",
        ),
        Family("param-inference", "Annotate the parameter. Unannotated parameters need local type inference."),
        Family("local-inference", "Add an annotation where the name is first assigned."),
        Family("return-inference", "Add a return annotation."),
        Family("uninitialized-field", "Give the field an initial value in __init__ or at the assignment."),
        Family("value-type", "Use list[T], dict[K, V] or a lowered class for this annotation."),
        Family(
            "annotation-other",
            "Rewrite the annotation with int, float, bool, str, bytes, list, dict, tuple or a lowered class.",
        ),
        Family("decorators", "Remove the decorator or inline what it does. Only a closed set of decorators lowers."),
        Family(
            "parameter-forms",
            "Use plain positional parameters. Keyword-only, positional-only, *args and **kwargs do not lower.",
        ),
        Family("class-keywords", "Use one lowered base class and no class keywords such as metaclass."),
        Family("class-body", "Declare fields with an annotation and no value and set them in __init__."),
        Family("except-as", "Name a lowered exception and drop the `as` binding or the bare except."),
        Family("with-as", "Use a lowered context manager without `as`, or open and close explicitly."),
        Family("async", "Async definitions do not lower. Use a synchronous definition."),
        Family("enum", "Write the enum as integer constants in a plain class body."),
        Family(
            "module-statements",
            "Module scope holds imports, functions, classes and constant assignments only. "
            "Move the statement into a function.",
        ),
        Family("module-assign", "A module assignment must be a constant the lowering can fold."),
        Family(
            "import-table",
            "The imported module is outside the closed import table. Avoid it or wait for it to be queued.",
        ),
        Family("queued-stdlib", "The stdlib module is queued and not lowered yet."),
        Family("name-resolution", "The name is not a local, a parameter or a lowered global. Declare it or import it."),
        Family("typing-value", "A typing name is used as a value. Keep typing names in annotations only."),
        Family("module-value", "A module is used as a value. Call its lowered functions instead."),
        Family("unlowered-call", "Call a lowered function, or rewrite the call with lowered constructs."),
        Family("keyword-args", "Pass the arguments positionally."),
        Family("call-arity", "Match the lowered arity of the call."),
        Family("operator", "Convert the operands to a supported type pair."),
        Family(
            "non-latin1", "Use text without lone surrogates. A lone surrogate has no UTF-8 bytes and does not lower."
        ),
        Family("type-mismatch", "Convert the operand explicitly."),
        Family("fall-off-end", "End every path with a return or a raise."),
        Family("field-lookup", "Access only fields the class declares."),
        Family(
            "generators",
            "The generator shape does not lower: yield inside try or with, a nested def, a lambda, "
            "a return value, or a generic resumable. Rewrite the body with lowered constructs.",
        ),
        Family(
            "contextmanager",
            "The @contextmanager definition is blocked by an unrelated fence in the same module "
            "or uses contextlib forms that do not lower.",
        ),
        Family("construct-other", "Rewrite the statement with lowered constructs."),
        Family(
            "refused-dynamic-attrs",
            "Attribute access is resolved statically to declared fields, so the hook never runs. "
            "Use plain annotated fields and methods; `@property` getters lower.",
        ),
        Family(
            "refused-finalizer",
            "Finalizer timing is unspecified and the runtime has no GC to call it. "
            "Use `with` and an explicit `close()`.",
        ),
        Family(
            "export-cascade", "Follows from a fence in the exporting module. Fix that definition first.", derived=True
        ),
        Family("class-cascade", "Follows from a fence inside the class. Fix the class first.", derived=True),
        Family(
            "import-cycle",
            "Two modules import each other. Break the cycle: move the shared code into a third module, "
            "or import the module that does not depend on the other one.",
        ),
    )
}


def _stdlib_family(match: re.Match[str]) -> str:
    return f"stdlib:{match.group(1)}"


# Order matters: the first matching rule wins. A rule is (code or None, message regex, family).
_RULES: tuple[tuple[str | None, str, str], ...] = (
    (None, r"runtime reflection \(", "reflection"),
    (None, r"keyword forwarding \(\*\*kwargs\)", "reflection"),
    ("PS1101", r"", "union-types"),
    ("PS1102", r"", "optional-types"),
    ("PS1103", r"", "generic-alias"),
    ("PS1104", r"", "dynamic-annotation"),
    ("PS1105", r"", "forward-reference"),
    ("PS1106", r"", "unlowered-type-name"),
    ("PS1201", r"", "decorators"),
    ("PS1202", r"", "parameter-forms"),
    ("PS1203", r"", "class-keywords"),
    ("PS1204", r"", "class-body"),
    ("PS1205", r"", "except-as"),
    ("PS1206", r"", "with-as"),
    ("PS1001", r"^parameter .+ needs an annotation$", "param-inference"),
    ("PS1001", r"needs an annotation before use$", "local-inference"),
    ("PS1001", r"needs a return annotation$", "return-inference"),
    ("PS1001", r"needs an initial value$|needs __init__$|does not assign", "uninitialized-field"),
    ("PS1001", r"is not a value type in the IR", "value-type"),
    ("PS1001", r"", "annotation-other"),
    ("PS1003", r"", "type-mismatch"),
    ("PS4204", r"returns .+, the read site expects", "type-mismatch"),
    ("PS4204", r"", "type-mismatch"),
    ("PS1004", r"", "fall-off-end"),
    ("PS2001", r"is not a field$|has no fields$", "field-lookup"),
    ("PS2001", r"is not in the lowered subset of ([\w.]+)", "stdlib"),
    ("PS2001", r"", "construct-other"),
    ("PS2002", r"is not exported by this package", "export-cascade"),
    ("PS2002", r"^circular import: ", "import-cycle"),
    ("PS2002", r"^this class .+ is not lowered", "class-cascade"),
    ("PS2002", r"is not in the closed import table", "import-table"),
    ("PS2002", r"is not a local or parameter", "name-resolution"),
    ("PS2002", r"is a typing name and is not a value", "typing-value"),
    ("PS2002", r"^module .+ is not lowered|module .+ is not a value", "module-value"),
    ("PS2002", r"^call to .+ is not lowered|call of attribute is not lowered", "unlowered-call"),
    ("PS2002", r"AsyncFunctionDef|async ", "async"),
    ("PS2002", r"\benum\b", "enum"),
    ("PS2002", r"at module scope is not lowered", "module-statements"),
    ("PS2002", r"module assignment", "module-assign"),
    ("PS2002", r"decorators? ", "decorators"),
    ("PS2002", r"resumable|generator|\byield\b", "generators"),
    ("PS2002", r"contextmanager", "contextmanager"),
    ("PS2002", r"", "construct-other"),
    ("PS2003", r"keyword arguments", "keyword-args"),
    ("PS2003", r"", "call-arity"),
    ("PS2004", r"", "operator"),
    ("PS2005", r"", "non-latin1"),
    ("PS2101", r"", "queued-stdlib"),
    ("PS4201", r"", "refused-dynamic-attrs"),
    ("PS4202", r"", "refused-dynamic-attrs"),
    ("PS4203", r"", "refused-finalizer"),
)


def family_of(code: str, message: str) -> str:
    for rule_code, pattern, family in _RULES:
        if rule_code is not None and rule_code != code:
            continue
        match = re.search(pattern, message)
        if match is None:
            continue
        if family == "stdlib":
            return _stdlib_family(match)
        return family
    return "construct-other"


def hint_for(family: str, message: str) -> str:
    known = _FAMILIES.get(family)
    if known is not None:
        if family == "param-inference":
            name = re.search(r"parameter (\S+) needs", message)
            if name:
                return f"Annotate parameter `{name.group(1)}`. Unannotated parameters need local type inference."
        return known.hint
    if family.startswith("stdlib:"):
        return f"Use a lowered attribute of {family.split(':', 1)[1]}, or hand-write the logic."
    return _FAMILIES["construct-other"].hint


def is_derived(family: str) -> bool:
    known = _FAMILIES.get(family)
    return known is not None and known.derived


@dataclass
class _Def:
    name: str
    kind: str
    line: int
    end_line: int
    body_line: int


def _definitions(tree: ast.AST) -> list[_Def]:
    found: list[_Def] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                body_line = child.body[0].lineno if child.body else child.lineno
                found.append(_Def(name, kind, child.lineno, child.end_lineno or child.lineno, body_line))
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _innermost(defs: list[_Def], line: int) -> _Def | None:
    best: _Def | None = None
    for item in defs:
        if item.line <= line <= item.end_line and (best is None or item.line >= best.line):
            best = item
    return best


def _read(path: str, cache: dict[str, str | None]) -> str | None:
    if path not in cache:
        try:
            cache[path] = Path(path).read_text(encoding="utf-8")
        except OSError:
            cache[path] = None
    return cache[path]


def capabilities(module: dict[str, Any], files: list[str]) -> dict[str, Any]:
    """Per-file split of definitions into native, fenced and island routed.

    A definition is island when a fence inside it traces to runtime reflection
    (the message names the mechanism), fenced when any other fence sits inside
    it, and native otherwise. Module-level fences belong to no definition and
    are counted on their own. This reads the sources and the lowered fences
    only; it never lowers anything itself.
    """

    sources: dict[str, str | None] = {}
    fences_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fence in module.get("fences", []):
        fences_by_file[fence["span"]["file"]].append(fence)
    paths = list(dict.fromkeys([*files, *sorted(fences_by_file)]))
    by_file: list[dict[str, Any]] = []
    totals = {
        "files": 0,
        "definitions": 0,
        "native": 0,
        "fenced": 0,
        "island": 0,
        "moduleFences": 0,
        "fences": 0,
    }
    all_codes: set[str] = set()
    all_mechanisms: set[str] = set()
    for path in paths:
        text = _read(path, sources)
        defs = _definitions(ast.parse(text, filename=path)) if text is not None else []
        local = fences_by_file.get(path, [])
        owned: dict[str, list[dict[str, Any]]] = {item.name: [] for item in defs}
        module_fences = 0
        for fence in local:
            owner = _innermost(defs, fence["span"]["line"])
            if owner is None:
                module_fences += 1
            else:
                owned[owner.name].append(fence)
        buckets = Counter()
        for name in owned:
            inside = owned[name]
            if any(routes_to_island(fence["message"]) for fence in inside):
                buckets[CAP_ISLAND] += 1
            elif inside:
                buckets[CAP_FENCED] += 1
            else:
                buckets[CAP_NATIVE] += 1
        codes = {fence["code"] for fence in local}
        mechanisms = {found for fence in local if (found := mechanism_of(fence["message"])) is not None}
        all_codes |= codes
        all_mechanisms |= mechanisms
        entry = {
            "file": path,
            "definitions": len(defs),
            "native": buckets[CAP_NATIVE],
            "fenced": buckets[CAP_FENCED],
            "island": buckets[CAP_ISLAND],
            "moduleFences": module_fences,
            "fences": len(local),
            "codes": sorted(codes),
            "mechanisms": sorted(mechanisms),
        }
        by_file.append(entry)
        totals["files"] += 1
        for key in ("definitions", "native", "fenced", "island", "moduleFences", "fences"):
            totals[key] += entry[key]
    return {
        "byFile": by_file,
        "totals": {**totals, "codes": sorted(all_codes), "mechanisms": sorted(all_mechanisms)},
    }


def _definition_capability(entries: list[dict[str, Any]], indexes: list[int]) -> str:
    inside = [entries[index] for index in indexes]
    if any(routes_to_island(entry["message"]) for entry in inside):
        return CAP_ISLAND
    if inside:
        return CAP_FENCED
    return CAP_NATIVE


def _lower(target: Path, bridge: Any = None) -> tuple[dict[str, Any], list[str]]:
    from pyscriptc.config import load_config

    config = load_config(target)
    if target.is_dir():
        module, _ = lower_directory(target, bridge=bridge, config=config)
        files = [str(path) for _, path, _ in _discover(target)[0]]
        return module, files
    if target.is_file():
        if bridge is not None and not hasattr(bridge, "inferred_type"):
            bridge = bridge.for_file(target)
        module, _ = lower_file(str(target), bridge=bridge, config=config)
        return module, [str(target)]
    raise IRError(f"source not found: {target}")


def _policy_entries(target: Path) -> dict[str, dict[str, Any]]:
    """The `[tool.pyscriptc.imports]` island entries, keyed by import name.

    The policy table is the only route to the island lane, so a coverage
    report can name that route before the resolver has anything to say: an
    island policy entry is the user's opt-in, not a classifier guess. The
    `*` key stands for every import the table does not name.
    """

    from pyscriptc.config import load_config

    config = load_config(target)
    entries: dict[str, dict[str, Any]] = {}
    hint = f"island lane: {REQUIRES_LIBPYTHON}; the binary links the host interpreter"
    for name, lane in config.imports.items():
        if lane != "island":
            continue
        key = "*" if name == "*" else name.lower()
        entries[key] = {"distribution": "", "lane": "island", "hint": hint, "policy": name}
    return entries


def _dependency_map(target: Path) -> dict[str, dict[str, Any]]:
    """The recorded import-name map of the target's locked dependencies.

    Read from the unpacked wheels' `.imports` records (issue #408) without
    touching the network: a tree whose config declares no dependencies has
    nothing to read, and a resolution failure is not a coverage failure. The
    `[imports]` island policy entries are merged last and win, because they
    are the only route that lane has.
    """

    from pyscriptc.config import load_config

    config = load_config(target)
    mapping: dict[str, dict[str, Any]] = {}
    if config.dependencies:
        try:
            report = deps_run_deps(target, cache_dir=None)
        except Exception:
            report = None
        if report is not None:
            mapping = _wheel_map(report)
    mapping.update(_policy_entries(target))
    return mapping


def _wheel_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The import-name map of a deps report (unpacked wheels, issue #408)."""

    from pyscriptc.deps import UNKNOWN_IMPORT_NAMES

    mapping: dict[str, dict[str, Any]] = {}
    for package in report["packages"]:
        module = str(package["name"]).lower().replace("-", "_").replace(".", "_")
        names = tuple(package.get("import_names") or ())
        if not names:
            mapping[module] = {
                "distribution": package["name"],
                "lane": package["lane"],
                "hint": package["hint"],
                "importNames": UNKNOWN_IMPORT_NAMES,
            }
            continue
        for name in names:
            mapping[name.lower()] = {
                "distribution": package["name"],
                "lane": package["lane"],
                "hint": package["hint"],
            }
    return mapping


def build_report(target: Path, bridge: Any = None) -> dict[str, Any]:
    module, files = _lower(target, bridge)
    known = _dependency_map(target)
    sources: dict[str, str | None] = {}
    trees: dict[str, list[_Def]] = {}
    total_defs: dict[str, int] = {}
    for path in files:
        text = _read(path, sources)
        if text is None:
            continue
        defs = _definitions(ast.parse(text, filename=path))
        trees[path] = defs
        total_defs[path] = len(defs)

    seen: set[tuple[Any, ...]] = set()
    entries: list[dict[str, Any]] = []
    raw = sorted(
        module["fences"],
        key=lambda f: (f["span"]["file"], f["span"]["line"], f["span"]["col"], f["code"]),
    )
    for fence in raw:
        span = fence["span"]
        key = (fence["code"], span["file"], span["line"], span["col"], fence["message"])
        if key in seen:
            continue
        seen.add(key)
        path = span["file"]
        text = _read(path, sources)
        defs = trees.get(path, [])
        owner = _innermost(defs, span["line"])
        family = family_of(fence["code"], fence["message"])
        lines = text.splitlines() if text else []
        statement = lines[span["line"] - 1].strip() if 1 <= span["line"] <= len(lines) else ""
        entries.append(
            _annotate(
                {
                    "code": fence["code"],
                    "message": fence["message"],
                    "family": family,
                    "derived": is_derived(family),
                    "file": path,
                    "line": span["line"],
                    "col": span["col"],
                    "endLine": span["endLine"],
                    "endCol": span["endCol"],
                    "definition": owner.name if owner else None,
                    "statement": statement,
                    "frame": format_diagnostic(fence, text),
                    "hint": hint_for(family, fence["message"]),
                },
                known,
            )
        )

    by_def: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, entry in enumerate(entries):
        if entry["definition"] is not None:
            by_def[(entry["file"], entry["definition"])].append(index)
    definitions: list[dict[str, Any]] = []
    for (path, name), indexes in sorted(by_def.items()):
        owner = next(d for d in trees[path] if d.name == name)
        # A fence between the def line and the first body line sits in the signature or header.
        header = any(entries[i]["line"] < owner.body_line for i in indexes)
        definitions.append(
            {
                "file": path,
                "name": name,
                "kind": owner.kind,
                "line": owner.line,
                "fences": indexes,
                "rootCause": indexes[0],
                "bodyAnalyzed": not header,
                "capability": _definition_capability(entries, indexes),
            }
        )
    module_fences = [i for i, e in enumerate(entries) if e["definition"] is None]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "target": str(target),
        "summary": _summary(entries, definitions, files, total_defs, _island_imports(target, known)),
        "fences": entries,
        "definitions": definitions,
        "moduleFences": module_fences,
        "capabilities": capabilities(module, files),
        "chain": _chain(entries, definitions),
    }


def _annotate(entry: dict[str, Any], known: dict[str, dict[str, str]]) -> dict[str, Any]:
    """One fence entry, named after the distribution when the map knows it.

    A PS2002 fence on an import a locked distribution provides (`import
    yaml` with PyYAML locked) carries `dependency`: the distribution, its
    lane and the lane hint, so the report says what to do about it instead
    of a bare "not in the closed import table". An ambiguous name says so
    and lists the claimants.
    """

    message = entry["message"]
    if entry["code"] != "PS2002" or "is not in the closed import table" not in message:
        return entry
    for name in message.split():
        candidate = name.strip(".,:;'\"()[]")
        found = known.get(candidate) or known.get(candidate.lower())
        if found is None:
            found = known.get(candidate.split(".", 1)[0].lower())
        if found is None:
            found = known.get("*")
        if found is None:
            continue
        entry["dependency"] = found
        if found.get("policy") is not None:
            where = (
                f" ({candidate} is on the island lane ({REQUIRES_LIBPYTHON}); "
                f"the per-import policy {found['policy']!r} routes it and the binary links the host interpreter)"
            )
        else:
            where = f" ({found['distribution']} provides {candidate}, on the {found['lane']} lane: {found['hint']})"
        if found.get("ambiguous"):
            where = f" ({candidate} is claimed by more than one locked distribution: {found['ambiguous']})"
        entry["message"] = message + where
        entry["frame"] = entry["frame"].replace(message, entry["message"])
        break
    return entry


def _island_imports(target: Path, known: dict[str, dict[str, Any]]) -> list[str]:
    """The imports the policy table routes to the island, sorted and unique.

    Read from the source tree rather than from the fences, so the summary
    answers "does this program need libpython?" even when the import is not
    fenced today (a `compile` row that the lowering binds never fences).
    """

    from pyscriptc.imports import collect_imports

    own = target.name if target.is_dir() else ""
    found: list[str] = []
    for site in collect_imports(target):
        name = site["name"]
        if not name or site["level"] > 0 or name.split(".", 1)[0] == own:
            continue
        root = name.split(".", 1)[0]
        entry = known.get(name.lower()) or known.get(root.lower()) or known.get("*")
        if entry is None or entry.get("lane") != "island":
            continue
        if name not in found:
            found.append(name)
    return sorted(found)


def _summary(
    entries: list[dict[str, Any]],
    definitions: list[dict[str, Any]],
    files: list[str],
    total_defs: dict[str, int],
    island_imports: list[str],
) -> dict[str, Any]:
    fences_by_family = Counter(e["family"] for e in entries)
    total_definitions = sum(total_defs.values())
    fenced_definitions = len(definitions)
    return {
        "files": len(files),
        "definitions": total_definitions,
        "nativeDefinitions": max(0, total_definitions - fenced_definitions),
        "fencedDefinitions": fenced_definitions,
        "fences": len(entries),
        "rootFences": sum(1 for e in entries if not e["derived"]),
        "requiresLibpython": bool(island_imports),
        "islandImports": island_imports,
        "byCode": dict(sorted(Counter(e["code"] for e in entries).items())),
        "byFamily": {
            family: {"fences": count, "derived": is_derived(family)}
            for family, count in sorted(fences_by_family.items(), key=lambda kv: (-kv[1], kv[0]))
        },
    }


def _chain(entries: list[dict[str, Any]], definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per family: how many definitions it alone blocks, and how many root fences stay if it is fixed."""
    root_total = sum(1 for e in entries if not e["derived"])
    root_by_family = Counter(e["family"] for e in entries if not e["derived"])
    alone: Counter[str] = Counter()
    for definition in definitions:
        families = {entries[i]["family"] for i in definition["fences"] if not entries[i]["derived"]}
        if len(families) == 1:
            alone[next(iter(families))] += 1
    chain = [
        {
            "family": family,
            "fences": count,
            "definitionsUnblocked": alone[family],
            "rootFencesLeft": root_total - count,
        }
        for family, count in root_by_family.items()
    ]
    chain.sort(key=lambda item: (-item["definitionsUnblocked"], -item["fences"], item["family"]))
    return chain


def render_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    parts = [
        f"{report['target']}: {summary['fences']} fences "
        f"({summary['rootFences']} root) in {summary['fencedDefinitions']} of {summary['definitions']} definitions "
        f"({summary['nativeDefinitions']} native)"
    ]
    cap = report["capabilities"]["totals"]
    parts.append(
        f"capability: {cap['native']} native, {cap['fenced']} fenced, {cap['island']} island "
        f"definitions of {cap['definitions']} ({cap['files']} files)"
    )
    for item in report["capabilities"]["byFile"]:
        if not (item["fences"] or item["definitions"]):
            continue
        mechanisms = f", reflection: {', '.join(item['mechanisms'])}" if item["mechanisms"] else ""
        parts.append(
            f"  {item['file']}: {item['native']} native, {item['fenced']} fenced, {item['island']} island "
            f"of {item['definitions']} definitions, {item['fences']} fences{mechanisms}"
        )
    entries = report["fences"]
    for entry in entries:
        parts.append(entry["frame"])
        owner = entry["definition"] or "<module>"
        parts.append(f"  family: {entry['family']}{' (derived)' if entry['derived'] else ''}  in: {owner}")
        parts.append(f"  hint: {entry['hint']}")
    if report["chain"]:
        parts.append("blockers, most definitions first:")
        for item in report["chain"][:10]:
            parts.append(
                f"  {item['family']}: {item['fences']} fences, "
                f"fixing it unblocks {item['definitionsUnblocked']} definitions "
                f"and leaves {item['rootFencesLeft']} more root fences"
            )
    return "\n".join(parts)
