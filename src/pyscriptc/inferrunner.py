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
    """

    path = path.resolve()
    root = root.resolve()
    relative = path.relative_to(root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    # Walk up from the file, adding every enclosing package directory.
    directory = path.parent
    prefixes: list[str] = []
    while directory != root and (directory / "__init__.py").is_file():
        prefixes.insert(0, directory.name)
        if directory.parent == directory:
            break
        directory = directory.parent
    if directory == root and (root / "__init__.py").is_file():
        prefixes.insert(0, root.name)
    return ".".join([*prefixes, *parts]) if parts else ".".join(prefixes)


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
    `CallSiteFacts.signature_key`.
    """

    signatures: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except OSError, SyntaxError, UnicodeDecodeError:
            continue
        module = _module_name(path, root)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            signatures[f"{module}.{node.name}"] = _def_params(node)
    return signatures


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
    with _tsp_server() as adapter:
        for path in sorted(root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except OSError, SyntaxError, UnicodeDecodeError:
                continue
            module = _module_name(path, root)
            stem = path.stem
            try:
                module_facts = adapter.facts(path)
            except CheckFailed:
                continue
            imports = _imported_names(tree, module)
            callees = _callee_names(tree, module, imports)
            facts.calls.extend(collect_calls(stem, tree, callees, module_facts.expressions))
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
    copy of each module: the parameters carry the proved annotations, so
    the returns and locals the checker derives from them are consistent
    with what the lowerer will apply.
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
    bridge = PyreflyBridge()
    mirror = tempfile.mkdtemp(prefix="pyscriptc-pinned-")
    try:
        # The mirror holds the whole tree, so an import of a sibling
        # module resolves the way it does in the original.
        shutil.copytree(root, Path(mirror, "tree"), dirs_exist_ok=True)
        for path, file_pins in pins_by_file.items():
            if not file_pins:
                try:
                    out[path] = bridge.facts(path)
                except CheckFailed:
                    continue
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
