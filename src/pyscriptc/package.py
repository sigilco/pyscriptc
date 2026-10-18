"""Compile a directory as one module and resolve relative imports.

Each ``.py`` file becomes a dotted module under the directory name.
``from .util import add`` and ``from . import VERSION`` bind a function, a
module-level constant, or a class from another file in that tree. A class
export is the class the defining module already emits, including a subclass
of a class exported by another module in the tree. ``__main__.py`` is the
``python -m`` script and is not an importable module, so it is skipped and
the compile says so. A single file still refuses a relative import.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

from pyscriptc.builtins import USER_EXC_BASE
from pyscriptc.errors import IRError
from pyscriptc.ir import SUBSET, cpython_version
from pyscriptc.lower import Lowerer


def lower_directory(root: Path) -> tuple[dict[str, Any], str]:
    units, skipped = _discover(root)
    exports, failed = _catalog(units)

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    fences: list[dict[str, Any]] = []
    class_names: dict[str, dict[str, Any]] = {}
    exc_n = USER_EXC_BASE
    for name, path, is_package in units:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lowerer = Lowerer(str(path), text)
        lowerer.module_name = name
        lowerer.is_package = is_package
        lowerer.package = exports
        lowerer.exc_n = exc_n
        module = lowerer.module(tree)
        exc_n = lowerer.exc_n
        for fn in module["functions"]:
            functions.append(fn)
        for cls in module["classes"]:
            previous = class_names.get(cls["name"])
            if previous is not None:
                if previous["fields"] == cls["fields"] and previous["methods"] == cls["methods"]:
                    continue
                fences.append(
                    {
                        "code": "PS2002",
                        "message": f"class {cls['name']} is defined in more than one module",
                        "span": cls["span"],
                        "function": None,
                    }
                )
                continue
            class_names[cls["name"]] = cls
            classes.append(cls)
        fences.extend(module["fences"])
    fences = _promote_definition_fences(fences, failed)
    merged = {
        "irVersion": 1,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": str(root),
        "entry": functions[0]["name"] if functions else None,
        "functions": functions,
        "classes": classes,
        "fences": fences,
    }
    if skipped:
        merged["_notes"] = [_skip_note(root, rel) for rel in skipped]
    return merged, ""


def _catalog(
    units: list[tuple[str, Path, bool]],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    """Repeat until a subclass can see a base exported by an earlier pass."""

    exports: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    for _ in range(len(units) + 1):
        nxt, nxt_failed = _catalog_once(units, exports)
        if nxt == exports:
            return nxt, nxt_failed
        exports, failed = nxt, nxt_failed
    return exports, failed


def _catalog_once(
    units: list[tuple[str, Path, bool]], exports: dict[str, dict[str, dict[str, Any]]]
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    found: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    exc_n = USER_EXC_BASE
    for name, path, is_package in units:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lowerer = Lowerer(str(path), text)
        lowerer.module_name = name
        lowerer.is_package = is_package
        lowerer.cataloguing = True
        lowerer.package = exports
        lowerer.exc_n = exc_n
        module = lowerer.module(tree)
        exc_n = lowerer.exc_n
        found[name] = _exports_of(name, lowerer)
        missed = _unexported_definitions(tree, lowerer, module["fences"])
        if missed:
            failed[name] = missed
    return found, failed


def _exports_of(module_name: str, lowerer: Lowerer) -> dict[str, dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}
    for fn, (params, ret) in lowerer.sigs.items():
        bucket[fn] = {
            "kind": "func",
            "ir_name": f"{module_name}.{fn}",
            "params": copy.deepcopy(params),
            "return": copy.deepcopy(ret),
        }
    for const_name, value in lowerer.constants.items():
        bucket[const_name] = {"kind": "const", "value": copy.deepcopy(value)}
    for class_name, info in lowerer.classes.items():
        if info.get("synthetic") or info.get("imported"):
            continue
        bucket[class_name] = {"kind": "class", "name": class_name, "info": _class_export(info)}
    return bucket


def _class_export(info: dict[str, Any]) -> dict[str, Any]:
    """Class shape without the AST node. The defining module still emits the class."""

    exported: dict[str, Any] = {
        "fields": copy.deepcopy(info.get("fields", [])),
        "own_fields": copy.deepcopy(info.get("own_fields", [])),
        "methods": copy.deepcopy(info.get("methods", {})),
        "method_kind": dict(info.get("method_kind", {})),
        "method_owner": dict(info.get("method_owner", {})),
        "base": info.get("base"),
        "imported": True,
    }
    if info.get("generated_init") is not None:
        exported["generated_init"] = info["generated_init"]
    if info.get("exception"):
        exported["exception"] = True
        exported["exc_code"] = info.get("exc_code")
    if info.get("enum"):
        exported["enum"] = True
        exported["members"] = dict(info.get("members") or {})
    return exported


def _discover(root: Path) -> tuple[list[tuple[str, Path, bool]], list[str]]:
    root = root.resolve()
    package = root.name
    if not package.isidentifier():
        raise IRError(f"package directory {package} is not an identifier")
    found: list[tuple[str, Path, bool]] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.name == "__main__.py":
            skipped.append(relative.as_posix())
            continue
        if path.name == "__init__.py":
            parts = relative.parts[:-1]
            name = ".".join((package, *parts)) if parts else package
            found.append((name, path, True))
            continue
        stem = relative.with_suffix("")
        if not all(part.isidentifier() for part in stem.parts):
            raise IRError(f"{relative} is not a module name")
        found.append(((".".join((package, *stem.parts))), path, False))
    if not found:
        raise IRError(f"no Python files in {root}")
    return found, skipped


def _skip_note(root: Path, relative: str) -> str:
    return f"note: skipped {root.name}/{relative}; __main__.py is the python -m script, not an importable module"


_NOT_EXPORTED = " is not exported by this package"


def _promote_definition_fences(
    fences: list[dict[str, Any]], failed: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Report the fence inside a class or function before the import that missed it."""

    by_qual = {f"{module}.{name}": fence for module, names in failed.items() for name, fence in names.items()}
    if not by_qual:
        return fences

    def ident(fence: dict[str, Any]) -> tuple[Any, ...]:
        span = fence.get("span") or {}
        return (fence.get("code"), fence.get("message"), span.get("file"), span.get("line"), span.get("col"))

    wanted = {ident(fence) for fence in by_qual.values()}
    emitted: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for fence in fences:
        message = fence.get("message") or ""
        if message.endswith(_NOT_EXPORTED):
            defined = by_qual.get(message[: -len(_NOT_EXPORTED)])
            if defined is not None:
                mark = ident(defined)
                if mark not in emitted:
                    out.append(defined)
                    emitted.add(mark)
        mark = ident(fence)
        if mark in emitted and mark in wanted:
            continue
        out.append(fence)
        if mark in wanted:
            emitted.add(mark)
    return out


def _unexported_definitions(
    tree: ast.Module, lowerer: Lowerer, fences: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    exported = set(lowerer.sigs)
    for class_name, info in lowerer.classes.items():
        if info.get("synthetic") or info.get("imported"):
            continue
        exported.add(class_name)
    found: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in exported:
            continue
        start = node.lineno
        decorators = getattr(node, "decorator_list", None) or []
        if decorators:
            start = min(start, min(item.lineno for item in decorators))
        end = node.end_lineno or node.lineno
        inside = [fence for fence in fences if start <= fence["span"]["line"] <= end]
        if inside:
            found[node.name] = inside[0]
    return found
