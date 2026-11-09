"""AST-level project bundler (opt-in, not wired into the compile path).

Reads a multi-module project tree rooted at the entry file's directory,
orders the modules topologically, renames every top-level name with a
deterministic module prefix, rewrites intra-project imports to the renamed
names, and emits a single-module source together with a line map that maps
each bundled line back to the original ``file:line`` for diagnostics.

Refuses import cycles with a clear diagnostic and refuses ``import *``
from project modules. Standard-library and third-party imports are kept
verbatim. Known limitation: a project module used as a bare object (not
followed by an attribute chain) is refused.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


class BundleError(Exception):
    """A project tree cannot be bundled."""


@dataclass
class Bundle:
    """The bundled single-module source plus provenance."""

    source: str
    order: list[str]
    line_map: dict[int, tuple[str, int]]

    def original_location(self, line: int) -> tuple[str, int] | None:
        for bundled_line, loc in self.line_map.items():
            if bundled_line == line:
                return loc
        return None


@dataclass
class _Binding:
    """An intra-project import binding inside one module."""

    target_module: str
    target_name: str | None
    is_module: bool


def bundle_project(entry: Path) -> Bundle:
    """Bundle the project tree that contains ``entry`` into one module."""
    entry = entry.resolve()
    if not entry.is_file():
        raise BundleError(f"entry file not found: {entry}")
    root = entry.parent
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        name = _module_name(root, path)
        if name:
            modules[name] = path
    entry_name = _module_name(root, entry)
    if entry_name is None or entry_name not in modules:
        raise BundleError(f"entry {entry} is not part of the project tree under {root}")

    deps = {name: _project_deps(modules[name], modules, name) for name in modules}
    order = _topo_order(entry_name, deps)
    renames: dict[str, dict[str, str]] = {}
    bindings: dict[str, dict[str, _Binding]] = {}
    for name in modules:
        prefix = name.replace(".", "__")
        tree = _parse(modules[name])
        renames[name] = _top_level_renames(tree, prefix, name)
        bindings[name] = _import_bindings(tree, name, modules)

    chunks: list[str] = []
    line_map: dict[int, tuple[str, int]] = {}
    line = 1
    for name in order:
        path = modules[name]
        tree = _parse(path)
        rewriter = _Rewriter(renames, bindings[name], name, modules)
        new_body: list[ast.stmt] = []
        for stmt in tree.body:
            if _drops_project_import(stmt, name, modules):
                continue
            new_body.append(rewriter.visit(stmt))
        tree.body = new_body
        ast.fix_missing_locations(tree)
        for stmt in tree.body:
            text = ast.unparse(stmt)
            n_lines = text.count("\n") + 1
            for offset in range(n_lines):
                orig_line = stmt.lineno + offset
                if stmt.end_lineno is not None and orig_line > stmt.end_lineno:
                    orig_line = stmt.end_lineno
                line_map[line + offset] = (str(path), orig_line)
            chunks.append(text)
            line += n_lines
    return Bundle(source="\n".join(chunks) + "\n", order=order, line_map=line_map)


def _module_name(root: Path, path: Path) -> str | None:
    rel: Path = path.relative_to(root)
    parts: list[str] = list(rel.parts[:-1])
    stem: str = rel.stem
    if stem == "__init__":
        if not parts:
            return None
        return ".".join(parts)
    parts.append(stem)
    return ".".join(parts)


def _parse(path: Path) -> ast.Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if not isinstance(tree, ast.Module):
        raise BundleError(f"{path}: not a module")
    return tree


def _top_level_renames(tree: ast.Module, prefix: str, modname: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for stmt in tree.body:
        for name in _defined_names(stmt):
            if name in out:
                raise BundleError(f"{modname}: duplicate top-level name {name!r}")
            out[name] = f"{prefix}__{name}"
    return out


def _defined_names(stmt: ast.stmt) -> list[str]:
    names: list[str] = []
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.append(stmt.name)
    elif isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    elif isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
    return names


def _import_bindings(tree: ast.Module, modname: str, modules: dict[str, Path]) -> dict[str, _Binding]:
    out: dict[str, _Binding] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name in modules:
                    local = alias.asname or alias.name.split(".")[0]
                    _record_binding(out, local, _Binding(alias.name, None, False), modname, local)
        elif isinstance(stmt, ast.ImportFrom):
            if stmt.level and stmt.module is None:
                raise BundleError(f"{modname}: bare relative 'from . import x' is not supported")
            if stmt.level:
                raise BundleError(f"{modname}: relative imports are not supported yet")
            for alias in stmt.names:
                if alias.name == "*":
                    raise BundleError(f"{modname}: 'import *' cannot be bundled")
                target = f"{stmt.module}.{alias.name}"
                local = alias.asname or alias.name
                if target in modules:
                    _record_binding(out, local, _Binding(target, alias.name, False), modname, local)
                elif stmt.module in modules:
                    _record_binding(out, local, _Binding(stmt.module, alias.name, False), modname, local)
    return out


def _record_binding(
    out: dict[str, _Binding],
    local: str,
    binding: _Binding,
    modname: str,
    what: str,
) -> None:
    prior = out.get(local)
    if prior is not None and prior != binding:
        raise BundleError(
            f"{modname}: name {what!r} binds two different project symbols "
            f"({prior.target_module!r} and {binding.target_module!r}); "
            "use an explicit 'as' alias so the bundler can tell them apart"
        )
    out[local] = binding


def _project_deps(path: Path, modules: dict[str, Path], modname: str) -> set[str]:
    deps: set[str] = set()
    tree = _parse(path)
    for stmt in tree.body:
        targets: list[str] = []
        if isinstance(stmt, ast.Import):
            targets = [alias.name for alias in stmt.names]
        elif isinstance(stmt, ast.ImportFrom):
            if stmt.level:
                raise BundleError(f"{modname}: relative imports are not supported yet")
            if stmt.module and stmt.module in modules:
                targets.append(stmt.module)
            elif stmt.module:
                for alias in stmt.names:
                    target = f"{stmt.module}.{alias.name}"
                    if target in modules:
                        targets.append(target)
        for target in targets:
            head = target.split(".")[0]
            if target in modules:
                deps.add(target)
            elif head in modules:
                while "." in target:
                    target = target.rsplit(".", 1)[0]
                    if target in modules:
                        deps.add(target)
                        break
    deps.discard(modname)
    return deps


def _drops_project_import(stmt: ast.stmt, modname: str, modules: dict[str, Path]) -> bool:
    if isinstance(stmt, ast.Import):
        return any(alias.name in modules for alias in stmt.names)
    if isinstance(stmt, ast.ImportFrom):
        if stmt.module in modules:
            return True
        return any(f"{stmt.module}.{alias.name}" in modules for alias in stmt.names) and stmt.module is not None
    return False


def _topo_order(entry: str, deps: dict[str, set[str]]) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        mark = state.get(name, 0)
        if mark == 1:
            start = stack.index(name)
            cycle = stack[start:] + [name]
            raise BundleError("import cycle detected: " + " -> ".join(cycle))
        if mark == 2:
            return
        state[name] = 1
        stack.append(name)
        for dep in sorted(deps.get(name, set())):
            visit(dep)
        stack.pop()
        state[name] = 2
        order.append(name)

    visit(entry)
    return order


class _Rewriter(ast.NodeTransformer):
    def __init__(
        self,
        renames: dict[str, dict[str, str]],
        bindings: dict[str, _Binding],
        modname: str,
        modules: dict[str, Path],
    ) -> None:
        self._renames = renames
        self._bindings = bindings
        self._modname = modname
        self._modules = modules

    def visit_Import(self, node: ast.Import) -> ast.stmt | None:
        kept = [
            alias
            for alias in node.names
            if alias.name not in self._modules and alias.name.split(".")[0] not in self._modules
        ]
        if not kept:
            return None
        node.names = kept
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.stmt | None:
        if node.module is None:
            return node
        if node.module in self._modules:
            return None
        if all(f"{node.module}.{alias.name}" not in self._modules for alias in node.names):
            return node
        if node.level:
            return node
        kept: list[ast.alias] = []
        for alias in node.names:
            if f"{node.module}.{alias.name}" not in self._modules:
                kept.append(alias)
        if not kept:
            return None
        node.names = kept
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        attrs: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self._bindings:
            binding = self._bindings[cur.id]
            dotted = binding.target_module + "." + ".".join(reversed(attrs))
            resolved = self._resolve_dotted(dotted)
            if resolved is not None:
                out = ast.Name(id=resolved, ctx=ast.Load())
                return ast.copy_location(out, node)
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.expr:
        module_renames = self._renames.get(self._modname, {})
        if node.id in module_renames and isinstance(node.ctx, (ast.Store, ast.Del)):
            out = ast.Name(id=module_renames[node.id], ctx=node.ctx)
            return ast.copy_location(out, node)
        if node.id in self._bindings:
            binding = self._bindings[node.id]
            if binding.is_module:
                raise BundleError(
                    f"{self._modname}: module {binding.target_module!r} is used as "
                    "a bare value, which cannot be bundled"
                )
            resolved = self._resolve_symbol(binding.target_module, binding.target_name)
            if resolved is not None:
                out = ast.Name(id=resolved, ctx=node.ctx)
                return ast.copy_location(out, node)
        return node

    def _resolve_dotted(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            prefix = ".".join(parts[:cut])
            if prefix in self._modules:
                rest = parts[cut:]
                if not rest:
                    return None
                if len(rest) > 1:
                    return None
                return self._resolve_symbol(prefix, rest[0])
        return None

    def _resolve_symbol(self, module: str, name: str | None) -> str | None:
        if name is None:
            return None
        module_renames = self._renames.get(module)
        if module_renames is None:
            return None
        return module_renames.get(name)
