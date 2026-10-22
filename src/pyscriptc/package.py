"""Compile a directory as one module and resolve relative imports.

Each ``.py`` file becomes a dotted module under the directory name.
``from .util import add`` and ``from . import VERSION`` bind a function, a
module-level constant, or a class from another file in that tree. A class
export is the class the defining module already emits, including a subclass
of a class exported by another module in the tree.

Two directory shapes exist:

* A **package** directory: the tree is a library. ``__main__.py`` is the
  ``python -m`` script and is not an importable module, so it is skipped and
  the compile says so.
* A **program** directory: the tree has a ``__main__.py`` at its root. That
  file is the entry point, the sibling modules are imported by their plain
  names (``import util``, ``from pkg import x``) exactly as ``sys.path[0]``
  would resolve them, and the whole tree compiles into one native binary.

A single file still refuses a relative import.
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


def lower_directory(root: Path, program_mode: bool = False, bridge: Any = None) -> tuple[dict[str, Any], str]:
    units, skipped = _discover(root, program_mode)
    program = program_mode or _is_program_dir(root)
    exports, failed = _catalog(units, program)

    if program:
        fences, owners = _program_plan(units, exports)
        if fences:
            return _fenced_module(root, fences), ""
    else:
        owners = {}

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    fences: list[dict[str, Any]] = []
    class_names: dict[str, dict[str, Any]] = {}
    exc_n = USER_EXC_BASE
    for name, path, is_package in units:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        if program:
            tree = _inject_inits(tree, owners.get(name, []), exports)
        lowerer = Lowerer(str(path), text)
        lowerer.module_name = name
        lowerer.is_package = is_package
        lowerer.package = exports
        lowerer.exc_n = exc_n
        lowerer.program = program
        lowerer.module_init = program and name != "__main__"
        lowerer.bridge = bridge
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
        "entry": _program_entry(functions) if program else (functions[0]["name"] if functions else None),
        "functions": functions,
        "classes": classes,
        "fences": fences,
    }
    if skipped:
        merged["_notes"] = [_skip_note(root, rel) for rel in skipped]
    return merged, ""


def _program_entry(functions: list[dict[str, Any]]) -> str | None:
    """The program's entry: `__main__.main`, or the first function."""

    for fn in functions:
        if fn["name"].startswith("__main__."):
            return fn["name"]
    return functions[0]["name"] if functions else None


def _fenced_module(root: Path, fences: list[dict[str, Any]]) -> dict[str, Any]:
    """A module that carries only fences: the compile stops before emission."""

    return {
        "irVersion": 1,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": str(root),
        "entry": None,
        "functions": [],
        "classes": [],
        "fences": fences,
    }


def _program_plan(
    units: list[tuple[str, Path, bool]], exports: dict[str, dict[str, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[str]]]]:
    """The program's import graph: cycles refused, init owners assigned.

    CPython runs a module body once, at the first import that reaches it.
    The owner map says which module triggers each body, so the init call
    lands in the module whose import statement performs it. A cycle is
    refused with the path that closes it.
    """

    trees: dict[str, ast.Module] = {}
    files: dict[str, Path] = {}
    for name, path, _ in units:
        trees[name] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        files[name] = path
    edges: dict[str, list[str]] = {name: _program_imports(trees[name], exports) for name in trees}
    # The statement that imports a module, so a cycle fence points at it.
    sites: dict[tuple[str, str], ast.stmt] = {}
    for name, tree in trees.items():
        for stmt in tree.body:
            for module, _local in _statement_imports(stmt, exports):
                sites.setdefault((name, module), stmt)
    # Only a module with script statements has an `__init__` export: its body
    # runs at import. A definition-only module contributes nothing.
    inited = {name for name, bucket in exports.items() if "__init__" in bucket}

    fences: list[dict[str, Any]] = []
    owners: dict[str, dict[str, list[str]]] = {name: [] for name in trees}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        state[name] = 1
        stack.append(name)
        for child in edges.get(name, []):
            if child not in trees:
                continue
            if state.get(child) == 1:
                cycle = stack[stack.index(child) :] + [child]
                fences.append(
                    {
                        "code": "PS2002",
                        "message": f"circular import: {' -> '.join(cycle)}",
                        "span": _cycle_span(files[name], sites.get((name, child))),
                        "function": None,
                    }
                )
                continue
            if state.get(child) == 2:
                continue
            if child in inited:
                owners[name].append(child)
            visit(child)
        stack.pop()
        state[name] = 2

    for name in _program_roots(trees):
        if state.get(name) is None:
            visit(name)
    if fences:
        return fences, {}
    return fences, owners


def _program_roots(trees: dict[str, ast.Module]) -> list[str]:
    """The order the graph walk starts in.

    The program's entry comes first: CPython runs it, and it is what
    reaches the other modules. The rest follow in name order.
    """

    roots = ["__main__"] if "__main__" in trees else []
    roots.extend(sorted(name for name in trees if name not in roots))
    return roots


def _cycle_span(path: Path, stmt: ast.stmt | None) -> dict[str, Any]:
    """The span of the import statement that closes a cycle."""

    line = getattr(stmt, "lineno", 1) or 1
    col = getattr(stmt, "col_offset", 0) or 0
    end_line = getattr(stmt, "end_lineno", line) or line
    end_col = getattr(stmt, "end_col_offset", col) or col
    return {
        "file": str(path),
        "line": line,
        "col": col + 1,
        "endLine": end_line,
        "endCol": end_col + 1,
    }


def _program_imports(tree: ast.Module, exports: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """The program's own modules that this module imports, in source order."""

    found: list[str] = []

    def note(module: str) -> None:
        if module in exports and module not in found:
            found.append(module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in exports:
                    note(alias.name)
                else:
                    head = alias.name.split(".", 1)[0]
                    if head in exports:
                        note(head)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in exports:
                note(node.module)
                for alias in node.names:
                    child = f"{node.module}.{alias.name}"
                    if child in exports:
                        note(child)
    return found


def _statement_imports(stmt: ast.stmt, exports: dict[str, dict[str, dict[str, Any]]]) -> list[tuple[str, str]]:
    """The program modules one import statement brings in.

    Each entry is the module and the local name the import binds for it, so
    the init call can be spelled the way the importing module sees it.
    """

    found: list[tuple[str, str]] = []

    def note(module: str, local: str) -> None:
        if module not in exports:
            return
        if any(item[0] == module for item in found):
            return
        found.append((module, local))

    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if alias.asname:
                note(alias.name, alias.asname)
                continue
            if alias.name in exports:
                note(alias.name, alias.name.split(".")[0])
                continue
            head = alias.name.split(".", 1)[0]
            if head in exports:
                note(head, head)
        return found
    if isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module:
        note(stmt.module, stmt.module.split(".")[0])
        for alias in stmt.names:
            note(f"{stmt.module}.{alias.name}", alias.asname or alias.name)
    return found


def _inject_inits(tree: ast.Module, owned: list[str], exports: dict[str, dict[str, dict[str, Any]]]) -> ast.Module:
    """Call the init of every module this one first imports.

    CPython runs an imported module's body at the import statement, so the
    call goes right after the statement that triggers it. A module this one
    does not own (another module already imported it) is left alone: its
    body already ran.
    """

    if not owned:
        return tree
    body: list[ast.stmt] = []
    done: set[str] = set()
    for stmt in tree.body:
        body.append(stmt)
        for module, local in _statement_imports(stmt, exports):
            if module not in owned or module in done:
                continue
            done.add(module)
            body.append(_init_call(local, stmt))
    tree.body = body
    ast.fix_missing_locations(tree)
    return tree


def _init_call(local: str, anchor: ast.stmt) -> ast.stmt:
    """`local.__init__()` as a statement, placed at the anchor's position."""

    call = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=local, ctx=ast.Load()),
            attr="__init__",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    ast.copy_location(call, anchor)
    expr = ast.Expr(value=call)
    ast.copy_location(expr, anchor)
    return expr


def _catalog(
    units: list[tuple[str, Path, bool]], program: bool = False
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    """Repeat until a subclass can see a base exported by an earlier pass."""

    exports: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    for _ in range(len(units) + 1):
        nxt, nxt_failed = _catalog_once(units, exports, program)
        if nxt == exports:
            return nxt, nxt_failed
        exports, failed = nxt, nxt_failed
    return exports, failed


def _catalog_once(
    units: list[tuple[str, Path, bool]],
    exports: dict[str, dict[str, dict[str, Any]]],
    program: bool = False,
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
        lowerer.program = program
        lowerer.module_init = program and name != "__main__"
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


def _is_program_dir(root: Path) -> bool:
    """True when the directory is a program.

    Two shapes infer a program: a root ``__main__.py`` (the ``python DIR``
    and ``python -m DIR`` entry), or a root ``main.py`` with no
    ``__init__.py`` (``main.py`` plus sibling modules, the other R2 shape).
    A root ``__init__.py`` without either stays a library package.
    """

    root = root.resolve()
    if (root / "__main__.py").is_file() and not (root / "__init__.py").is_file():
        return True
    return (root / "main.py").is_file() and not (root / "__init__.py").is_file()


def _program_entry_file(root: Path) -> Path | None:
    """The inferred entry file: ``__main__.py`` first, then ``main.py``."""

    root = root.resolve()
    if (root / "__main__.py").is_file():
        return root / "__main__.py"
    if (root / "main.py").is_file() and not (root / "__init__.py").is_file():
        return root / "main.py"
    return None


def _discover(root: Path, program_mode: bool = False) -> tuple[list[tuple[str, Path, bool]], list[str]]:
    """Find every module in the tree.

    A directory whose root holds ``__main__.py`` and no ``__init__.py`` is a
    program: that file is the entry point, and every sibling module is
    reachable under its plain name. Any other directory is a package, where
    the directory name is the module root and ``__main__.py`` is the
    ``python -m`` script, not an importable module.

    With ``program_mode`` the caller asked for the ``python -m pkg`` shape:
    the root ``__main__.py`` is the entry even when the directory is a
    package, and the modules keep the package namespace.
    """

    root = root.resolve()
    package = root.name
    if not package.isidentifier():
        raise IRError(f"package directory {package} is not an identifier")
    program = program_mode or _is_program_dir(root)
    namespaced = program_mode and not _is_program_dir(root)
    entry = _program_entry_file(root)
    found: list[tuple[str, Path, bool]] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if program and entry is not None and path == entry:
            # The program entry. It lowers as the `__main__` module.
            found.append(("__main__", path, False))
            continue
        if path.name == "__main__.py":
            skipped.append(relative.as_posix())
            continue
        if path.name == "__init__.py":
            parts = relative.parts[:-1]
            if program and not namespaced:
                # A program sub-package: `pkg/__init__.py` is the module
                # `pkg`, imported by its plain name.
                found.append((".".join(parts), path, True))
                continue
            name = ".".join((package, *parts)) if parts else package
            found.append((name, path, True))
            continue
        stem = relative.with_suffix("")
        if not all(part.isidentifier() for part in stem.parts):
            raise IRError(f"{relative} is not a module name")
        if program and not namespaced:
            # A program sibling is imported by its plain name.
            found.append((".".join(stem.parts), path, False))
            continue
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
