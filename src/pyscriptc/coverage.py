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
from pyscriptc.package import _discover, _skip_note, _skip_reason, lower_directory
from pyscriptc.reflection import mechanism_of, routes_to_island

SCHEMA_VERSION = 2

# A definition is routed by the fences inside it: island when one traces to
# runtime reflection, fenced when any other fence sits in it, native otherwise.
CAP_NATIVE = "native"
CAP_FENCED = "fenced"
CAP_ISLAND = "island"


def deps_run_deps(source: Path, *, cache_dir: Path | None = None) -> dict[str, Any]:
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
        bucket = fences_by_file[_opt_str(span, "file")]
        bucket.append(fence)
    paths: list[str] = []
    seen_paths: set[str] = set()
    for path_name in files:
        if path_name not in seen_paths:
            seen_paths.add(path_name)
            paths.append(path_name)
    extra_names: list[str] = [f for f in fences_by_file]
    for extra in sorted(extra_names):
        if extra not in seen_paths:
            seen_paths.add(extra)
            paths.append(extra)
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
        owned: dict[str, list[dict[str, Any]]] = {}
        for item in defs:
            owned[item.name] = []
        module_fences = 0
        for fence in local:
            owner = _innermost(defs, _opt_int(_span_of(fence), "line", 0))
            if owner is None:
                module_fences += 1
            else:
                owner_fences = owned[owner.name]
                owner_fences.append(fence)
        buckets: dict[str, int] = {"native": 0, "fenced": 0, "island": 0}
        for name in owned:
            inside = owned[name]
            if any(routes_to_island(_opt_str(fence, "message")) for fence in inside):
                buckets[CAP_ISLAND] += 1
            elif inside:
                buckets[CAP_FENCED] += 1
            else:
                buckets[CAP_NATIVE] += 1
        codes = {_opt_str(fence, "code") for fence in local}
        mechanisms: set[str] = set()
        for fence in local:
            found = mechanism_of(_opt_str(fence, "message"))
            if found is not None:
                mechanisms.add(found)
        code_list: list[str] = [c for c in codes]
        mechanism_list: list[str] = [m for m in mechanisms]
        all_codes |= codes
        all_mechanisms |= mechanisms
        entry: dict[str, Any] = {}
        entry["file"] = path
        entry["definitions"] = len(defs)
        entry["native"] = buckets[CAP_NATIVE]
        entry["fenced"] = buckets[CAP_FENCED]
        entry["island"] = buckets[CAP_ISLAND]
        entry["moduleFences"] = module_fences
        entry["fences"] = len(local)
        entry["codes"] = sorted(code_list)
        entry["mechanisms"] = sorted(mechanism_list)
        by_file.append(entry)
        totals["files"] += 1
        for key in ("definitions", "native", "fenced", "island", "moduleFences", "fences"):
            value: int = entry[key]
            totals[key] += value
    totals_out: dict[str, Any] = {}
    for key in totals:
        totals_out[key] = totals[key]
    all_codes_list: list[str] = [c for c in all_codes]
    all_mechanisms_list: list[str] = [m for m in all_mechanisms]
    totals_out["codes"] = sorted(all_codes_list)
    totals_out["mechanisms"] = sorted(all_mechanisms_list)
    result: dict[str, Any] = {}
    result["byFile"] = by_file
    result["totals"] = totals_out
    return result


def _definition_capability(entries: list[dict[str, Any]], indexes: list[int]) -> str:
    inside = [entries[index] for index in indexes]
    if any(routes_to_island(entry["message"]) for entry in inside):
        return CAP_ISLAND
    if inside:
        return CAP_FENCED
    return CAP_NATIVE


def _unusable_files(root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """The tree's files that cannot be analysed, each with the reason.

    A real package tree holds files the lowering cannot take: files the
    parser rejects and files a lowering pass crashes on (issue #730). The
    report drops them from the unit set and lists them instead of
    aborting, so the rest of the tree still scores. A file whose dotted
    name is not a chain of identifiers is not a failure: `_discover`
    skips it with a note, the way it skips a package `__main__.py`,
    because CPython cannot import it either (issue #764).
    """

    resolved = root.resolve()
    excluded: list[Path] = []
    failures: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*.py")):
        relative = path.relative_to(resolved)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if not all(part.isidentifier() for part in relative.with_suffix("").parts):
            continue
        reason = ""
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
            reason = str(exc)
        if reason:
            excluded.append(path)
            failures.append({"file": str(path), "reason": reason})
    return excluded, failures


def _empty_module(target: Path) -> dict[str, Any]:
    """A module with no units: the report reads fences and totals from it."""

    module: dict[str, Any] = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "sourceFile": str(target),
        "functions": [],
        "classes": [],
        "fences": [],
    }
    return module


def _lower(
    target: Path, bridge: Any = None
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    from pyscriptc.config import load_config

    config = load_config(target)
    if target.is_dir():
        excluded, failures = _unusable_files(target)
        exclude = frozenset(excluded) if excluded else None
        unit_failures: list[dict[str, Any]] = []
        module, _ = lower_directory(target, bridge=bridge, config=config, exclude=exclude, unit_failures=unit_failures)
        failed_paths = {item["file"] for item in unit_failures}
        resolved = target.resolve()
        units, skipped = _discover(target, exclude=exclude)
        files = [str(path) for _, path, _ in units if str(path) not in failed_paths]
        skipped_files = [
            {
                "file": str(resolved / relative),
                "reason": _skip_reason(relative),
                "note": _skip_note(resolved, relative),
            }
            for relative in skipped
        ]
        failures.extend(unit_failures)
        failures.sort(key=lambda item: item["file"])
        return module, files, failures, skipped_files
    if target.is_file():
        if bridge is not None and not hasattr(bridge, "inferred_type"):
            bridge = bridge.for_file(target)
        try:
            module, _ = lower_file(str(target), bridge=bridge, config=config)
        except Exception as exc:
            return _empty_module(target), [str(target)], [{"file": str(target), "reason": str(exc)}], []
        return module, [str(target)], [], []
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
        report: dict[str, Any] | None = None
        try:
            report = deps_run_deps(target, cache_dir=None)
        except Exception:
            pass
        if report is not None:
            mapping = _wheel_map(report)
    mapping.update(_policy_entries(target))
    return mapping


def _wheel_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The import-name map of a deps report (unpacked wheels, issue #408)."""

    from pyscriptc.deps import UNKNOWN_IMPORT_NAMES

    mapping: dict[str, dict[str, Any]] = {}
    packages: list[dict[str, Any]] = report["packages"]
    for package in packages:
        dist_name: str = package["name"]
        module = str(dist_name).lower().replace("-", "_").replace(".", "_")
        names: list[str] = []
        if "import_names" in package:
            names = package["import_names"]
        lane_name: str = package["lane"]
        hint_text: str = package["hint"]
        if not names:
            mapping[module] = {
                "distribution": dist_name,
                "lane": lane_name,
                "hint": hint_text,
                "importNames": UNKNOWN_IMPORT_NAMES,
            }
            continue
        for name in names:
            mapping[name.lower()] = {
                "distribution": dist_name,
                "lane": lane_name,
                "hint": hint_text,
            }
    return mapping


def build_report(target: Path, bridge: Any = None) -> dict[str, Any]:
    module, files, failed_files, skipped_files = _lower(target, bridge)
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
        "summary": _summary(
            entries, definitions, files, total_defs, _island_imports(target, known), failed_files, skipped_files
        ),
        "fences": entries,
        "definitions": definitions,
        "moduleFences": module_fences,
        "capabilities": capabilities(module, files),
        "chain": _chain(entries, definitions),
        "failedFiles": failed_files,
        "skippedFiles": skipped_files,
    }


def _annotate(entry: dict[str, Any], known: dict[str, dict[str, str]]) -> dict[str, Any]:
    """One fence entry, named after the distribution when the map knows it.

    A PS2002 fence on an import a locked distribution provides (`import
    yaml` with PyYAML locked) carries `dependency`: the distribution, its
    lane and the lane hint, so the report says what to do about it instead
    of a bare "not in the closed import table". An ambiguous name says so
    and lists the claimants.
    """

    message: str = entry["message"]
    entry_code: str = entry["code"]
    if entry_code != "PS2002" or "is not in the closed import table" not in message:
        return entry
    for name in message.split():
        candidate = name
        while candidate and candidate[0] in ".,:;'\"()[]":
            candidate = candidate[1:]
        while candidate and candidate[-1] in ".,:;'\"()[]":
            candidate = candidate[:-1]
        found: dict[str, str] | None = known.get(candidate)
        if found is None:
            found = known.get(candidate.lower())
        if found is None:
            found = known.get(candidate.split(".", 1)[0].lower())
        if found is None:
            found = known.get("*")
        if found is None:
            continue
        entry["dependency"] = found
        where: str = ""
        if found.get("policy") is not None:
            policy_name: str = found["policy"]
            where = (
                f" ({candidate} is on the island lane ({REQUIRES_LIBPYTHON}); "
                f"the per-import policy {policy_name!r} routes it and the binary links the host interpreter)"
            )
        else:
            dist_name: str = found["distribution"]
            lane_name: str = found["lane"]
            hint_text: str = found["hint"]
            where = f" ({dist_name} provides {candidate}, on the {lane_name} lane: {hint_text})"
        ambiguous: str | None = found.get("ambiguous")
        if ambiguous:
            where = f" ({candidate} is claimed by more than one locked distribution: {ambiguous})"
        entry["message"] = message + where
        frame_text: str = entry["frame"]
        new_message: str = entry["message"]
        entry["frame"] = frame_text.replace(message, new_message)
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
        name: str = site["name"]
        level: int = site["level"]
        if not name or level > 0 or name.split(".", 1)[0] == own:
            continue
        root: str = name.split(".", 1)[0]
        entry: dict[str, Any] | None = known.get(name.lower())
        if entry is None:
            entry = known.get(root.lower())
        if entry is None:
            entry = known.get("*")
        if entry is None:
            continue
        lane_value: str = entry["lane"]
        if lane_value != "island":
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
    skipped_files: list[dict[str, Any]],
) -> dict[str, Any]:
    fences_by_family: dict[str, int] = {}
    code_counts: dict[str, int] = {}
    root_fences = 0
    for e in entries:
        family: str = e["family"]
        code: str = e["code"]
        entry_derived: bool = e["derived"]
        fences_by_family[family] = fences_by_family.get(family, 0) + 1
        code_counts[code] = code_counts.get(code, 0) + 1
        if not entry_derived:
            root_fences += 1
    total_definitions = sum(total_defs.values())
    fenced_definitions = len(definitions)
    sorted_codes: list[str] = sorted([c for c in code_counts])
    by_code: dict[str, int] = {}
    for code in sorted_codes:
        by_code[code] = code_counts[code]
    family_rows: list[tuple[str, int]] = []
    for family in fences_by_family:
        family_rows.append((family, fences_by_family[family]))
    row_count: int = len(family_rows)
    for i in range(row_count - 1):
        for j in range(row_count - 1 - i):
            left_row: tuple[str, int] = family_rows[j]
            right_row: tuple[str, int] = family_rows[j + 1]
            left_count: int = left_row[1]
            right_count: int = right_row[1]
            swap_rows = False
            if left_count != right_count:
                swap_rows = left_count < right_count
            else:
                left_family: str = left_row[0]
                right_family: str = right_row[0]
                swap_rows = left_family > right_family
            if swap_rows:
                family_rows[j] = right_row
                family_rows[j + 1] = left_row
    by_family: dict[str, Any] = {}
    for family, count in family_rows:
        by_family[family] = _family_capability(count, family)
    result: dict[str, Any] = {}
    result["files"] = len(files)
    result["filesFailed"] = len(failed_files)
    result["filesSkipped"] = len(skipped_files)
    result["definitions"] = total_definitions
    result["nativeDefinitions"] = max(0, total_definitions - fenced_definitions)
    result["fencedDefinitions"] = fenced_definitions
    result["fences"] = len(entries)
    result["rootFences"] = root_fences
    result["requiresLibpython"] = bool(island_imports)
    result["islandImports"] = island_imports
    result["byCode"] = by_code
    result["byFamily"] = by_family
    return result


def _family_capability(count: int, family: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    row["fences"] = count
    row["derived"] = is_derived(family)
    return row


def _chain_row_less(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_unblocked: int = left["definitionsUnblocked"]
    right_unblocked: int = right["definitionsUnblocked"]
    left_fences: int = left["fences"]
    right_fences: int = right["fences"]
    left_family: str = left["family"]
    right_family: str = right["family"]
    if left_unblocked != right_unblocked:
        return left_unblocked < right_unblocked
    if left_fences != right_fences:
        return left_fences < right_fences
    return left_family > right_family


def _chain_row(family: str, fence_count: int, unblocked: int, left: int) -> dict[str, Any]:
    row: dict[str, Any] = {}
    row["family"] = family
    row["fences"] = fence_count
    row["definitionsUnblocked"] = unblocked
    row["rootFencesLeft"] = left
    return row


def _chain(entries: list[dict[str, Any]], definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per family: how many definitions it alone blocks, and how many root fences stay if it is fixed."""
    root_total = 0
    root_by_family: dict[str, int] = {}
    for e in entries:
        entry_derived: bool = e["derived"]
        if entry_derived:
            continue
        root_total += 1
        family: str = e["family"]
        root_by_family[family] = root_by_family.get(family, 0) + 1
    alone: Counter[str] = Counter()
    for definition in definitions:
        families: set[str] = set()
        def_fences: list[int] = definition["fences"]
        for i in def_fences:
            fence_entry: dict[str, Any] = entries[i]
            fence_derived: bool = fence_entry["derived"]
            if not fence_derived:
                fence_family: str = fence_entry["family"]
                families.add(fence_family)
        if len(families) == 1:
            alone[next(iter(families))] += 1
    chain: list[dict[str, Any]] = [
        _chain_row(family, count, alone[family], root_total - count) for family, count in root_by_family.items()
    ]
    chain_count: int = len(chain)
    for i in range(chain_count - 1):
        for j in range(chain_count - 1 - i):
            if _chain_row_less(chain[j], chain[j + 1]):
                tmp_row: dict[str, Any] = chain[j]
                chain[j] = chain[j + 1]
                chain[j + 1] = tmp_row
    return chain


def render_text(report: dict[str, Any]) -> str:
    summary: dict[str, Any] = report["summary"]
    report_target: str = report["target"]
    summary_fences: int = summary["fences"]
    summary_root: int = summary["rootFences"]
    summary_fenced_defs: int = summary["fencedDefinitions"]
    summary_definitions: int = summary["definitions"]
    summary_native_defs: int = summary["nativeDefinitions"]
    parts: list[str] = [
        f"{report_target}: {summary_fences} fences "
        f"({summary_root} root) in {summary_fenced_defs} of {summary_definitions} definitions "
        f"({summary_native_defs} native)"
    ]
    report_caps: dict[str, Any] = report["capabilities"]
    cap: dict[str, Any] = report_caps["totals"]
    cap_native: int = cap["native"]
    cap_fenced: int = cap["fenced"]
    cap_island: int = cap["island"]
    cap_definitions: int = cap["definitions"]
    cap_files: int = cap["files"]
    parts.append(
        f"capability: {cap_native} native, {cap_fenced} fenced, {cap_island} island "
        f"definitions of {cap_definitions} ({cap_files} files)"
    )
    by_file: list[dict[str, Any]] = report_caps["byFile"]
    for item in by_file:
        item_fences: int = item["fences"]
        item_definitions: int = item["definitions"]
        if not (item_fences or item_definitions):
            continue
        item_native: int = item["native"]
        item_fenced_cap: int = item["fenced"]
        item_island: int = item["island"]
        item_file: str = item["file"]
        item_mechanisms: list[str] = item["mechanisms"]
        mechanisms = ""
        if item_mechanisms:
            mechanisms = ", reflection: " + ", ".join(item_mechanisms)
        parts.append(
            f"  {item_file}: {item_native} native, {item_fenced_cap} fenced, {item_island} island "
            f"of {item_definitions} definitions, {item_fences} fences{mechanisms}"
        )
    failed: list[dict[str, Any]] = []
    if "failedFiles" in report:
        failed = report["failedFiles"]
    if failed:
        parts.append(f"could not analyse {len(failed)} file(s):")
        for item in failed:
            fail_file: str = item["file"]
            fail_reason: str = item["reason"]
            parts.append(f"  {fail_file}: {fail_reason}")
    skipped: list[dict[str, Any]] = []
    if "skippedFiles" in report:
        skipped = report["skippedFiles"]
    for item in skipped:
        note_text: str = item["note"]
        parts.append(note_text)
    entries: list[dict[str, Any]] = report["fences"]
    for entry in entries:
        frame: str = entry["frame"]
        entry_family: str = entry["family"]
        entry_derived: bool = entry["derived"]
        entry_definition: str | None = entry["definition"]
        entry_hint: str = entry["hint"]
        parts.append(frame)
        owner = entry_definition or "<module>"
        parts.append(f"  family: {entry_family}{' (derived)' if entry_derived else ''}  in: {owner}")
        parts.append(f"  hint: {entry_hint}")
    chain_all: list[dict[str, Any]] = report["chain"]
    chain_items = chain_all[:10]
    if chain_items:
        parts.append("blockers, most definitions first:")
        for item in chain_items:
            item_family: str = item["family"]
            item_fences: int = item["fences"]
            item_unblocked: int = item["definitionsUnblocked"]
            item_left: int = item["rootFencesLeft"]
            parts.append(
                f"  {item_family}: {item_fences} fences, "
                f"fixing it unblocks {item_unblocked} definitions "
                f"and leaves {item_left} more root fences"
            )
    return "\n".join(parts)
