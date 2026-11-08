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

from pyscriptc.emit._ir import _items, _opt_int, _opt_str, _span_of
from pyscriptc.errors import IRError, format_diagnostic
from pyscriptc.imports import REQUIRES_LIBPYTHON
from pyscriptc.ir import IR_VERSION, SUBSET
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


_HINTS: dict[str, str] = {
    "annotation-other": "Rewrite the annotation with int, float, bool, str, bytes, list, dict, tuple or a lowered "
    "class.",
    "async": "Async definitions do not lower. Use a synchronous definition.",
    "call-arity": "Match the lowered arity of the call.",
    "class-body": "Declare fields with an annotation and no value and set them in __init__.",
    "class-cascade": "Follows from a fence inside the class. Fix the class first.",
    "class-keywords": "Use one lowered base class and no class keywords such as metaclass.",
    "construct-other": "Rewrite the statement with lowered constructs.",
    "contextmanager": "The @contextmanager definition is blocked by an unrelated fence in the same module or "
    "uses contextlib forms that do not lower.",
    "decorators": "Remove the decorator or inline what it does. Only a closed set of decorators lowers.",
    "dynamic-annotation": "Annotate with the concrete type. object, Any, Protocol, TypeVar and Callable do not lower.",
    "enum": "Write the enum as integer constants in a plain class body.",
    "except-as": "Name a lowered exception and drop the `as` binding or the bare except.",
    "export-cascade": "Follows from a fence in the exporting module. Fix that definition first.",
    "fall-off-end": "End every path with a return or a raise.",
    "field-lookup": "Access only fields the class declares.",
    "forward-reference": "Define the class before use and write the annotation unquoted.",
    "generators": "The generator shape does not lower: yield inside try or with, a nested def, a lambda, a "
    "return value, or a generic resumable. Rewrite the body with lowered constructs.",
    "generic-alias": "Use list[T], dict[K, V] or tuple[...] in the signature instead of the abstract generic.",
    "import-cycle": "Two modules import each other. Break the cycle: move the shared code into a third "
    "module, or import the module that does not depend on the other one.",
    "import-table": "The imported module is outside the closed import table. Avoid it or wait for it to be queued.",
    "keyword-args": "Pass the arguments positionally.",
    "local-inference": "Add an annotation where the name is first assigned.",
    "module-assign": "A module assignment must be a constant the lowering can fold.",
    "module-statements": "Module scope holds imports, functions, classes and constant assignments only. Move the "
    "statement into a function.",
    "module-value": "A module is used as a value. Call its lowered functions instead.",
    "name-resolution": "The name is not a local, a parameter or a lowered global. Declare it or import it.",
    "non-latin1": "Use text without lone surrogates. A lone surrogate has no UTF-8 bytes and does not lower.",
    "operator": "Convert the operands to a supported type pair.",
    "optional-types": "Accept or return T and signal absence with a sentinel. Optional[T] needs the tagged-union epic.",
    "param-inference": "Annotate the parameter. Unannotated parameters need local type inference.",
    "parameter-forms": "Use plain positional parameters. Keyword-only, positional-only, *args and **kwargs do "
    "not lower.",
    "queued-stdlib": "The stdlib module is queued and not lowered yet.",
    "reflection": "Native code keeps no runtime type, signature or annotation table. Use the typed idiom "
    "the fence names, or let the definition route to the island lane.",
    "refused-dynamic-attrs": "Attribute access is resolved statically to declared fields, so the hook never runs. Use "
    "plain annotated fields and methods; `@property` getters lower.",
    "refused-finalizer": "Finalizer timing is unspecified and the runtime has no GC to call it. Use `with` and an "
    "explicit `close()`.",
    "return-inference": "Add a return annotation.",
    "type-mismatch": "Convert the operand explicitly.",
    "typing-value": "A typing name is used as a runtime value. Typing names steer the lowering; keep them in "
    "annotations.",
    "uninitialized-field": "Give the field an initial value in __init__ or at the assignment.",
    "union-types": "Narrow the annotation to one arm, or split the function per arm. A tagged union is a "
    "compiler epic.",
    "unlowered-call": "Call a lowered function, or rewrite the call with lowered constructs.",
    "unlowered-type-name": "Replace the alias or imported name with a lowered type, or lower the class it names.",
    "value-type": "Use list[T], dict[K, V] or a lowered class for this annotation.",
    "with-as": "Use a lowered context manager without `as`, or open and close explicitly.",
}


def family_of(code: str, message: str) -> str:
    if re.search(r"runtime reflection \(", message) is not None:
        return "reflection"
    if re.search(r"keyword forwarding \(\*\*kwargs\)", message) is not None:
        return "reflection"
    if code == "PS1101":
        return "union-types"
    if code == "PS1102":
        return "optional-types"
    if code == "PS1103":
        return "generic-alias"
    if code == "PS1104":
        return "dynamic-annotation"
    if code == "PS1105":
        return "forward-reference"
    if code == "PS1106":
        return "unlowered-type-name"
    if code == "PS1201":
        return "decorators"
    if code == "PS1202":
        return "parameter-forms"
    if code == "PS1203":
        return "class-keywords"
    if code == "PS1204":
        return "class-body"
    if code == "PS1205":
        return "except-as"
    if code == "PS1206":
        return "with-as"
    if code == "PS1001":
        if re.search(r"^parameter .+ needs an annotation$", message) is not None:
            return "param-inference"
        if re.search(r"needs an annotation before use$", message) is not None:
            return "local-inference"
        if re.search(r"needs a return annotation$", message) is not None:
            return "return-inference"
        if re.search(r"needs an initial value$|needs __init__$|does not assign", message) is not None:
            return "uninitialized-field"
        if re.search(r"is not a value type in the IR|is not a lowered type in the IR", message) is not None:
            return "value-type"
        return "annotation-other"
    if code == "PS1003" or code == "PS4204":
        return "type-mismatch"
    if code == "PS1004":
        return "fall-off-end"
    if code == "PS2001":
        if re.search(r"is not a field$|has no fields$", message) is not None:
            return "field-lookup"
        found = re.search(r"is not in the lowered subset of ([\w.]+)", message)
        if found is not None:
            group = found.group(1)
            if group is not None:
                return "stdlib:" + group
        return "construct-other"
    if code == "PS2002":
        if re.search(r"is not exported by this package", message) is not None:
            return "export-cascade"
        if re.search(r"^circular import: ", message) is not None:
            return "import-cycle"
        if re.search(r"^this class .+ is not lowered", message) is not None:
            return "class-cascade"
        if re.search(r"is not in the closed import table", message) is not None:
            return "import-table"
        if re.search(r"is not a local or parameter", message) is not None:
            return "name-resolution"
        if re.search(r"is a typing name and is not a value", message) is not None:
            return "typing-value"
        if re.search(r"^module .+ is not lowered|module .+ is not a value", message) is not None:
            return "module-value"
        if re.search(r"^call to .+ is not lowered|call of attribute is not lowered", message) is not None:
            return "unlowered-call"
        if re.search(r"AsyncFunctionDef|async ", message) is not None:
            return "async"
        if re.search(r"\benum\b", message) is not None:
            return "enum"
        if re.search(r"at module scope is not lowered", message) is not None:
            return "module-statements"
        if re.search(r"module assignment", message) is not None:
            return "module-assign"
        if re.search(r"decorators? ", message) is not None:
            return "decorators"
        if re.search(r"resumable|generator|\byield\b", message) is not None:
            return "generators"
        if re.search(r"contextmanager", message) is not None:
            return "contextmanager"
        return "construct-other"
    if code == "PS2006":
        return "decorators"
    if code == "PS2003":
        if re.search(r"keyword arguments", message) is not None:
            return "keyword-args"
        return "call-arity"
    if code == "PS2004":
        return "operator"
    if code == "PS2005":
        return "non-latin1"
    if code == "PS2101":
        return "queued-stdlib"
    if code == "PS4201" or code == "PS4202":
        return "refused-dynamic-attrs"
    if code == "PS4203":
        return "refused-finalizer"
    return "construct-other"


def hint_for(family: str, message: str) -> str:
    known = _HINTS.get(family)
    if known is not None:
        if family == "param-inference":
            name = re.search(r"parameter (\S+) needs", message)
            if name is not None:
                group = name.group(1)
                if group is not None:
                    return f"Annotate parameter `{group}`. Unannotated parameters need local type inference."
        return known
    if family.startswith("stdlib:"):
        return f"Use a lowered attribute of {family.split(':', 1)[1]}, or hand-write the logic."
    return _HINTS["construct-other"]


def is_derived(family: str) -> bool:
    return family == "export-cascade" or family == "class-cascade"


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
        except OSError, UnicodeDecodeError:
            cache[path] = None
    return cache[path]


def _parse_defs(path: str, sources: dict[str, str | None]) -> list[_Def]:
    """The definitions of one file, or none when the file cannot be parsed."""
    text = _read(path, sources)
    if text is None:
        return []
    try:
        return _definitions(ast.parse(text, filename=path))
    except SyntaxError:
        return []


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
    for fence in _items(module, "fences"):
        span = _span_of(fence)
        fences_by_file[_opt_str(span, "file")].append(fence)
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
        defs = _parse_defs(path, sources)
        local = fences_by_file.get(path, [])
        owned: dict[str, list[dict[str, Any]]] = {item.name: [] for item in defs}
        module_fences = 0
        for fence in local:
            owner = _innermost(defs, _opt_int(_span_of(fence), "line", 0))
            if owner is None:
                module_fences += 1
            else:
                owned[owner.name].append(fence)
        buckets = Counter()
        for name in owned:
            inside = owned[name]
            if any(routes_to_island(_opt_str(fence, "message")) for fence in inside):
                buckets[CAP_ISLAND] += 1
            elif inside:
                buckets[CAP_FENCED] += 1
            else:
                buckets[CAP_NATIVE] += 1
        codes = {_opt_str(fence, "code") for fence in local}
        mechanisms = {found for fence in local if (found := mechanism_of(_opt_str(fence, "message"))) is not None}
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


def _unusable_files(root: Path) -> tuple[set[Path], list[dict[str, Any]]]:
    """The tree's files that cannot be analysed, each with the reason.

    A real package tree holds files the lowering cannot take: migrations
    and template modules whose names are not identifiers, namespace
    directories the package table cannot name, and files the parser
    rejects (issue #730). The report drops them from the unit set and
    lists them instead of aborting, so the rest of the tree still scores.
    """

    resolved = root.resolve()
    excluded: set[Path] = set()
    failures: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*.py")):
        relative = path.relative_to(resolved)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        reason = ""
        if not all(part.isidentifier() for part in relative.with_suffix("").parts):
            reason = f"{relative.as_posix()} is not a module name"
        else:
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
                reason = str(exc)
        if reason:
            excluded.add(path)
            failures.append({"file": str(path), "reason": reason})
    return excluded, failures


def _empty_module(target: Path) -> dict[str, Any]:
    """A module with no units: the report reads fences and totals from it."""

    return {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "sourceFile": str(target),
        "functions": [],
        "classes": [],
        "fences": [],
    }


def _lower(target: Path, bridge: Any = None) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    from pyscriptc.config import load_config

    config = load_config(target)
    if target.is_dir():
        excluded, failures = _unusable_files(target)
        exclude = frozenset(excluded) if excluded else None
        unit_failures: list[dict[str, Any]] = []
        module, _ = lower_directory(target, bridge=bridge, config=config, exclude=exclude, unit_failures=unit_failures)
        failed_paths = {item["file"] for item in unit_failures}
        files = [str(path) for _, path, _ in _discover(target, exclude=exclude)[0] if str(path) not in failed_paths]
        failures.extend(unit_failures)
        failures.sort(key=lambda item: item["file"])
        return module, files, failures
    if target.is_file():
        if bridge is not None and not hasattr(bridge, "inferred_type"):
            bridge = bridge.for_file(target)
        try:
            module, _ = lower_file(str(target), bridge=bridge, config=config)
        except Exception as exc:
            return _empty_module(target), [str(target)], [{"file": str(target), "reason": str(exc)}]
        return module, [str(target)], []
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
    module, files, failed_files = _lower(target, bridge)
    known = _dependency_map(target)
    sources: dict[str, str | None] = {}
    trees: dict[str, list[_Def]] = {}
    total_defs: dict[str, int] = {}
    for path in files:
        defs = _parse_defs(path, sources)
        trees[path] = defs
        total_defs[path] = len(defs)

    seen: set[tuple[Any, ...]] = set()
    entries: list[dict[str, Any]] = []
    raw = sorted(
        _items(module, "fences"),
        key=lambda f: (
            _opt_str(_span_of(f), "file"),
            _opt_int(_span_of(f), "line", 0),
            _opt_int(_span_of(f), "col", 0),
            _opt_str(f, "code"),
        ),
    )
    for fence in raw:
        span = _span_of(fence)
        code = _opt_str(fence, "code")
        message = _opt_str(fence, "message")
        path = _opt_str(span, "file")
        line = _opt_int(span, "line", 0)
        col = _opt_int(span, "col", 0)
        key = (code, path, line, col, message)
        if key in seen:
            continue
        seen.add(key)
        text = _read(path, sources)
        defs = trees.get(path, [])
        owner = _innermost(defs, line)
        family = family_of(code, message)
        lines = text.splitlines() if text else []
        statement = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
        entries.append(
            _annotate(
                {
                    "code": code,
                    "message": message,
                    "family": family,
                    "derived": is_derived(family),
                    "file": path,
                    "line": line,
                    "col": col,
                    "endLine": _opt_int(span, "endLine", 0),
                    "endCol": _opt_int(span, "endCol", 0),
                    "definition": owner.name if owner else None,
                    "statement": statement,
                    "frame": format_diagnostic(fence, text),
                    "hint": hint_for(family, message),
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
        "summary": _summary(entries, definitions, files, total_defs, _island_imports(target, known), failed_files),
        "fences": entries,
        "definitions": definitions,
        "moduleFences": module_fences,
        "capabilities": capabilities(module, files),
        "chain": _chain(entries, definitions),
        "failedFiles": failed_files,
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
    failed_files: list[dict[str, Any]],
) -> dict[str, Any]:
    fences_by_family = Counter(e["family"] for e in entries)
    total_definitions = sum(total_defs.values())
    fenced_definitions = len(definitions)
    return {
        "files": len(files),
        "filesFailed": len(failed_files),
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
    failed = report.get("failedFiles", [])
    if failed:
        parts.append(f"could not analyse {len(failed)} file(s):")
        for item in failed:
            parts.append(f"  {item['file']}: {item['reason']}")
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
