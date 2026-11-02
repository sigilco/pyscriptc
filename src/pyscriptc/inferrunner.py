"""Build the package-wide inference facts from a checker run.

The runner is the engine behind `pyscriptc infer`: it drives the checker
over a package tree, collects what the checker resolved at each call
site, and reduces the result to the pins every call site agrees on
(`inferfacts.InferFacts`).

The checker answers per module, but the pins are a package-wide question:
a parameter is pinned only when every call site in the tree agrees. So
the runner walks the tree, resolves each module's imports to qualified
names, asks the checker for the argument types at each call site, and
then reduces.

One checker process serves the whole tree (the `tsp` server keeps a
snapshot per open document), so the cost is one server start plus one
document open per module, not one server per module.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pyscriptc.check import CheckFailed, _resolve_pyrefly_binary
from pyscriptc.inferfacts import InferFacts, collect_calls, parse_pin_type, pins_from_calls
from pyscriptc.typefacts import PyreflyTspFactsAdapter

# The TSP configuration the runner pins, so an unannotated def reports its
# inferred return type and an unannotated parameter is still checked.
_TSP_CONFIG = 'check-unannotated-defs = true\ninfer-return-types = "checked"\n'


def _module_name(path: Path, root: Path) -> str:
    """The dotted module name of a file inside the tree.

    Every directory between the file and the root that holds an
    `__init__.py` is a package and joins the name; the root itself joins
    too when it is a package.

    The name must match the one the lowerer gives the same file, or a pin
    keyed under this name never reaches the definition it proves. The
    lowerer names a unit after the package directory it sits in, so a
    package directory joins the name once. The `__init__.py` of a package
    directory is the package itself and contributes no extra part.
    """

    path = path.resolve()
    root = root.resolve()
    relative = path.relative_to(root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    if not parts:
        return root.name
    # The program entry file lowers as `__main__`, not as a module of the
    # package (`python -m pkg` parity).
    if relative == Path("__main__.py"):
        return "__main__"
    # The lowerer names a unit after the tree it lowers: a package tree
    # namespaces its modules under the package directory, a program tree
    # (no `__init__.py` at the root) uses the plain relative path. The pin
    # key must be the same name, or the pin never reaches the definition
    # it proves.
    if (root / "__init__.py").is_file():
        return ".".join([root.name, *parts])
    return ".".join(parts)


def _imported_names(tree: ast.Module, module: str) -> dict[str, str]:
    """The local names a module binds to qualified names.

    `from lib import helper` binds `helper` to `lib.helper`; `import lib
    as L` binds `L` to `lib`. A relative import resolves against the
    module's own package. A name the checker could not resolve
    contributes nothing, and a call through it is a call site the pin
    cannot use.
    """

    names: dict[str, str] = {}
    package = module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                root_module = f"{base}.{node.module}" if node.module else base
            else:
                root_module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                names[local] = f"{root_module}.{alias.name}" if root_module else alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    return names


def _callee_names(tree: ast.Module, module: str, imports: dict[str, str]) -> dict[str, str]:
    """The local call names that resolve to a qualified callee.

    A same-module call (`helper(...)`) names the module's own function;
    an imported one names the imported qualified name; a call through an
    aliased module (`L.helper(...)`) names the attribute of the import.
    """

    own = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            local = node.func.id
            if local in imports:
                names[local] = imports[local]
            elif local in own:
                names[local] = f"{module}.{local}"
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            base = imports.get(node.func.value.id)
            if base is not None:
                names[node.func.value.id] = base
                names[f"{node.func.value.id}.{node.func.attr}"] = f"{base}.{node.func.attr}"
    return names


def _reexport_index(root: Path) -> dict[tuple[str, str], str]:
    """The defining module of every name a module re-exports.

    A module that imports a name does not define it: `from ._utils import
    url_or_none` binds the name to another module's function, and a
    consumer that imports it calls the function the *defining* module
    holds. The lowerer resolves such a call to the defining module's
    `ir_name`, so the pin map must name the same module or the pin never
    reaches the definition it proves.

    The key is `(module, re-exported name)` and the value is the
    qualified name of the name in the module that defines it. The map is
    a fixpoint: a name a module re-exports from a module that itself
    re-exports it resolves to the module that defines it. A name the tree
    does not define contributes nothing: the pin refuses, the fence
    stands.
    """

    # The names each module binds at module level, by its own module name,
    # and the import statements that bring a name in.
    bound: dict[str, set[str]] = {}
    imports: dict[str, list[tuple[str, str | None, str]]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name(path, root)
        is_package = path.name == "__init__.py"
        names = bound.setdefault(module, set())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                source = _import_source(module, node, is_package)
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)
                        imports.setdefault(module, []).append((alias.asname or alias.name, alias.name, source))
                    else:
                        imports.setdefault(module, []).append(("*", "*", source))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    # The fixpoint: each round resolves one hop of every re-export chain.
    index: dict[tuple[str, str], str] = {}
    for _round in range(len(bound) + 1):
        changed = False
        for module, entries in imports.items():
            for local, original, source in entries:
                if source is None:
                    continue
                if original == "*":
                    # A star import re-exports what the source module's
                    # `__all__` picks, or every public name without one.
                    for name in sorted(bound.get(source, ())):
                        if name.startswith("_"):
                            continue
                        found = index.get((source, name), f"{source}.{name}")
                        if index.setdefault((module, name), found) != found:
                            changed = True
                    continue
                found = index.get((source, original), f"{source}.{original}")
                if index.setdefault((module, local), found) != found:
                    changed = True
        if not changed:
            break
    # A name the source module does not define is not a re-export: the map
    # keeps the source's name, and the pin refuses when the signature map
    # has no such callee.
    return index


def _resolve_reexport(name: str, index: dict[tuple[str, str], str]) -> str:
    """The defining qualified name of an imported name, or the name itself.

    The chain is followed to its end: a module that re-exports a name
    another module already re-exports resolves to the module that
    defines it. A cycle stops at the name it started from, so the
    resolution always ends.
    """

    seen = {name}
    current = name
    while True:
        module, _, member = current.rpartition(".")
        if not module:
            return current
        found = index.get((module, member))
        if found is None or found in seen:
            return current
        seen.add(found)
        current = found


def _import_source(module: str, node: ast.ImportFrom, is_package: bool) -> str | None:
    """The module a `from ... import` statement names, or None.

    An absolute import names the module as written. A relative import
    resolves against the importing module's own package, the same way the
    lowerer's `_base_of` resolves it: level 1 is the containing package
    (the module itself when it is a package), and each extra level climbs
    one parent.
    """

    if not node.level:
        return node.module or None
    bits = module.split(".")
    if not is_package:
        bits = bits[:-1]
    drop = node.level - 1
    if drop > len(bits):
        return None
    base = bits[: len(bits) - drop]
    if node.module:
        return ".".join([*base, *node.module.split(".")])
    return ".".join(base) if base else None


def _class_index(root: Path) -> dict[str, str]:
    """The qualified name of every class in the tree, by class short name.

    A short name that two modules both define is dropped: the map must
    never name the wrong class. A method call on a receiver whose class is
    ambiguous resolves to no callee, which is the safe answer (the pin
    refuses, the fence stands).
    """

    found: dict[str, str] = {}
    ambiguous: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name(path, root)
        for cls in _class_nodes(tree):
            if cls.name in found and found[cls.name] != f"{module}.{cls.name}":
                ambiguous.add(cls.name)
                continue
            found[cls.name] = f"{module}.{cls.name}"
    for name in ambiguous:
        found.pop(name, None)
    return found


def _def_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """One def's positional-index keys mapped to the real parameter names."""

    out: dict[str, str] = {}
    plain = node.args.posonlyargs + node.args.args
    for index, arg in enumerate(plain):
        out[f"#{index}"] = arg.arg
    for arg in node.args.kwonlyargs:
        out[arg.arg] = arg.arg
    return out


def _qualified_signatures(root: Path) -> dict[str, dict[str, str]]:
    """The parameter names of every def in the tree, by qualified name.

    The key is the callee's dotted name, matching
    `CallSiteFacts.signature_key`. A method is keyed `module.Class.name`,
    the same name the lowerer builds while it collects the class, so the
    pin reaches a method's parameters and not only a module-level def's.
    """

    signatures: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name(path, root)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            signatures[f"{module}.{node.name}"] = _def_params(node)
        for cls in _class_nodes(tree):
            for node in cls.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                signatures[f"{module}.{cls.name}.{node.name}"] = _def_params(node)
    return signatures


def _constructible_classes(root: Path) -> set[str]:
    """The qualified name of every class the tree defines with an `__init__`.

    A call on such a class is a construction: the lowerer fills the
    arguments against the class's `__init__` parameters, so the pin proves
    those parameters and not a function call that does not exist.
    """

    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name(path, root)
        for cls in _class_nodes(tree):
            for node in cls.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                    found.add(f"{module}.{cls.name}")
    return found


def _class_nodes(tree: ast.Module) -> list[ast.ClassDef]:
    """The class definitions of a module, at any nesting depth.

    A nested class is keyed `module.Outer.Inner.name`: the lowerer
    collects the inner class while it is inside the outer one, so the
    names must line up.
    """

    found: list[ast.ClassDef] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                found.append(child)
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, prefix)
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


class _tsp_server:
    """A `pyrefly tsp` server with the inference configuration pinned.

    The server keeps one snapshot per open document, so a whole package
    runs on one process: the adapter opens each file in turn.
    """

    def __init__(self) -> None:
        self._directory: Any = None
        self._adapter: PyreflyTspFactsAdapter | None = None

    def __enter__(self) -> PyreflyTspFactsAdapter:
        self._directory = tempfile.TemporaryDirectory(prefix="pyscriptc-infer-")
        config = Path(self._directory.name, "pyrefly.toml")
        config.write_text(_TSP_CONFIG, encoding="utf-8")
        argv = [*_resolve_pyrefly_binary(), "tsp", "--config", str(config)]
        self._adapter = PyreflyTspFactsAdapter(argv)
        self._adapter._connect()
        return self._adapter

    def __exit__(self, *_exc: object) -> None:
        if self._adapter is not None:
            self._adapter.close()
        if self._directory is not None:
            self._directory.cleanup()


def build_facts(root: Path, provider: str = "pyrefly") -> InferFacts:
    """The package-wide inference facts for a tree.

    Raises `CheckFailed` when the provider is not wired; a file the
    checker cannot read contributes no call sites, which simply means its
    callees keep their fences.
    """

    root = root.resolve()
    if provider != "pyrefly":
        raise CheckFailed(f"the infer runner supports pyrefly only, not {provider!r}")
    facts = InferFacts(provider=provider)
    signatures = _qualified_signatures(root)
    classes = _class_index(root)
    reexports = _reexport_index(root)
    constructible = _constructible_classes(root)
    with _tsp_server() as adapter:
        for path in sorted(root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            module = _module_name(path, root)
            stem = path.stem
            try:
                module_facts = adapter.facts(path)
            except CheckFailed:
                continue
            imports = _imported_names(tree, module)
            # A name the import names through a package re-export resolves
            # to the defining module: the lowerer calls the defining
            # module's `ir_name`, so the pin must name the same thing.
            imports = {local: _resolve_reexport(qualified, reexports) for local, qualified in imports.items()}
            callees = _callee_names(tree, module, imports)
            facts.calls.extend(
                collect_calls(stem, tree, callees, module_facts.expressions, classes, constructible)
            )
    facts.pins = pins_from_calls(facts.calls, signatures)
    return facts


def apply_pins(tree: ast.Module, pins: dict[str, Any]) -> ast.Module:
    """A copy of the module with the pins written as annotations.

    The checker re-runs over the pinned copy, so its own inference (a
    return that reads a pinned parameter) is consistent with what the
    lowerer will apply. The original tree is not touched: the overlay is
    a lowering-time fact, never an edit of the user's source.
    """

    import copy as _copy

    tree = _copy.deepcopy(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for param in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            if param.annotation is not None:
                continue
            pin = pins.get(param.arg)
            if pin is None:
                continue
            parsed = parse_pin_type(pin.kind)
            if parsed is None:
                continue
            name = parsed.kind
            if name == "class":
                name = pin.kind
            param.annotation = ast.Name(id=name, ctx=ast.Load())
            ast.fix_missing_locations(node)
    return tree


def pinned_facts(root: Path, facts: InferFacts, provider: str = "pyrefly") -> dict[Path, Any]:
    """The per-file bridge facts, re-checked with the pins applied.

    The checker's first answer types an unannotated parameter `Any`, so
    its return inference stops there even when the call sites proved the
    parameter's type. This runs the checker a second time over a pinned
    copy of each module with pins: the parameters carry the proved
    annotations, so the returns and locals the checker derives from them
    are consistent with what the lowerer will apply. A file no pin lands
    in keeps the first pass's facts, so it is not re-checked at all.
    """

    from pyscriptc.check import CheckFailed
    from pyscriptc.factsbridge import PyreflyBridge

    pins_by_file: dict[Path, dict[str, Any]] = {}
    for path in sorted(root.resolve().rglob("*.py")):
        callee_head = _module_name(path, root)
        file_pins: dict[str, Any] = {}
        for (callee, param), pin in facts.pins.items():
            if callee.rpartition(".")[0] == callee_head:
                file_pins[param] = pin
        pins_by_file[path] = file_pins
    out: dict[Path, Any] = {}
    mirror = tempfile.mkdtemp(prefix="pyscriptc-pinned-")
    try:
        # The mirror holds the whole tree, so an import of a sibling
        # module resolves the way it does in the original.
        shutil.copytree(root, Path(mirror, "tree"), dirs_exist_ok=True)
        # One `pyrefly tsp` server serves the whole re-check: the server
        # keeps a snapshot per open document, so a package costs one
        # server start instead of one per file.
        with PyreflyBridge() as bridge:
            for path, file_pins in pins_by_file.items():
                if not file_pins:
                    # No pin can change this file's facts, so the first
                    # pass already holds the answer.
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                pinned = apply_pins(tree, file_pins)
                # The pinned copy keeps the module's place in the package: a
                # relative import must resolve the same way it does in the
                # original, or the checker types the whole module `Any` and
                # the re-check proves nothing.
                module = _module_name(path, root)
                target = Path(mirror, "tree", *module.split(".")).with_suffix(".py")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(ast.unparse(pinned), encoding="utf-8")
                try:
                    out[path] = bridge.facts(target)
                except CheckFailed:
                    continue
    finally:
        shutil.rmtree(mirror, ignore_errors=True)
    return out


def facts_as_pins(facts: InferFacts) -> Any:
    """The pin map shaped the way the lowerer reads it.

    The lowerer asks for a callee's pins by the qualified name it lowers
    under (`pkg.lib.helper`), where the package root is the directory it
    was handed. The checker resolved the same names on the same tree, so
    the map passes through; the fallback matches a callee by its suffix
    for a tree compiled from a different root (`src/pkg` lowers as
    `pkg`).
    """

    class _PinsAsFacts:
        provider = facts.provider

        def pins_for(self, callee: str) -> dict[str, Any]:
            merged: dict[str, Any] = {}
            for (callee_key, param), pin in facts.pins.items():
                if callee_key == callee or callee_key.endswith("." + callee):
                    merged.setdefault(param, pin)
            return merged

    return _PinsAsFacts()
