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
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pyscriptc.check import CheckFailed, _resolve_pyrefly_binary
from pyscriptc.inferfacts import InferFacts, collect_calls, parse_pin_type, pins_from_calls
from pyscriptc.typefacts import PyreflyTspFactsAdapter

# The TSP configuration the runner pins, so an unannotated def reports its
# inferred return type and an unannotated parameter is still checked.
_TSP_CONFIG = 'check-unannotated-defs = true\ninfer-return-types = "checked"\n'

# `pyrefly infer` flags for the mirror pass (issue #731): annotate
# parameters and returns, check unannotated bodies, and infer returns for
# fully unannotated defs. `--imports=false` keeps pyrefly from inserting
# import lines: an inserted line shifts every later position, and the
# fact maps are keyed by line and column.
_PYREFLY_INFER_ARGS = [
    "infer",
    "--parameter-types",
    "--return-types",
    "--check-unannotated-defs",
    "--infer-return-types",
    "checked",
    "--imports=false",
]
# One `pyrefly infer` pass is not a fixpoint: a parameter it proves on one
# pass feeds the call sites the next pass reads, so the pass repeats until
# a run changes nothing, bounded so a non-converging tree cannot hang.
_PYREFLY_INFER_PASSES = 4


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
        except (OSError, SyntaxError, UnicodeDecodeError):
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

    The `own` provider runs the local checker-free engine
    (`owninfer.build_own_result`); `pyrefly` runs the pyrefly `tsp`
    server. Raises `CheckFailed` when the provider is not wired; a file
    the engine or the checker cannot read contributes no call sites,
    which simply means its callees keep their fences.
    """

    root = root.resolve()
    if provider == "own":
        from pyscriptc.owninfer import build_own_result

        return build_own_result(root).facts
    if provider != "pyrefly":
        raise CheckFailed(f"the infer runner supports pyrefly and own, not {provider!r}")
    facts = InferFacts(provider=provider)
    signatures = _qualified_signatures(root)
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
            callees = _callee_names(tree, module, imports)
            facts.calls.extend(collect_calls(stem, tree, callees, module_facts.expressions))
    facts.pins = pins_from_calls(facts.calls, signatures)
    return facts


def apply_pins(source: bytes, tree: ast.Module, pins: dict[str, Any]) -> bytes:
    """The source with each pin spliced on as an annotation, positions kept.

    The checker re-runs over the pinned copy, so its own inference (a
    return that reads a pinned parameter) is consistent with what the
    lowerer will apply. The original source is not touched: the overlay
    is a lowering-time fact, never an edit of the user's file.

    The splice inserts `: <type>` after each unannotated parameter name
    in the original text rather than rebuilding the module through
    `ast.unparse`: unparse collapses blank lines and shifts every later
    position, and the fact maps are keyed by line and column, so a
    shifted def would lose its def facts and every expression fact below
    it. Line numbers never move here; only the columns after an
    insertion on its own line do (a one-line `def f(x): return x`
    keeps its fence, never a wrong fact). The checker's own rendering
    is inserted verbatim, so container arguments (`list[str]`) survive,
    which the AST overlay lost.
    """

    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            if arg.annotation is not None:
                continue
            pin = pins.get(arg.arg)
            if pin is None:
                continue
            parsed = parse_pin_type(pin.kind)
            if parsed is None or parsed.kind == "none":
                continue
            offset = starts[arg.end_lineno - 1] + arg.end_col_offset
            edits.append((offset, b": " + pin.kind.encode("utf-8")))
    edits.sort(reverse=True)
    out = source
    for offset, text in edits:
        out = out[:offset] + text + out[offset:]
    return out


def _pyrefly_infer_annotations(tree: Path) -> dict[str, str]:
    """The files `pyrefly infer` annotated inside a scratch tree.

    `pyrefly infer` rewrites its input in place, so it only ever runs on
    the mirror copy `pinned_facts` builds, never on the user's source.
    `--dry-run` names each file a pass would annotate and exits 1 while
    any remain, so the loop applies real passes until a dry run comes
    back clean: a parameter proven on one pass feeds the call sites the
    next pass reads, and the pass count is bounded so a non-converging
    tree cannot hang. The returned dict holds the path of every file
    any pass named, keyed to True. A failed run keeps whatever earlier
    passes annotated: the result is still checker-proved facts, just
    fewer of them.
    """

    base: list = _resolve_pyrefly_binary() + _PYREFLY_INFER_ARGS
    changed: dict[str, str] = {}
    for _ in range(_PYREFLY_INFER_PASSES):
        probe = subprocess.run(base + ["--dry-run", str(tree)], capture_output=True)
        if probe.returncode == 0:
            break
        text: str = probe.stdout.decode()
        found: bool = False
        for line in text.splitlines():
            if ": would add " not in line:
                continue
            changed[line.split(": would add ", 1)[0]] = line
            found = True
        if not found:
            break
        subprocess.run(base + [str(tree)])
    return changed


def pinned_facts(root: Path, facts: InferFacts, provider: str = "pyrefly") -> dict[Path, Any]:
    """The per-file bridge facts, re-checked with the pins applied.

    The checker's first answer types an unannotated parameter `Any`, so
    its return inference stops there even when the call sites proved the
    parameter's type. This runs the checker a second time over a pinned
    copy of each module with pins: the parameters carry the proved
    annotations, so the returns and locals the checker derives from them
    are consistent with what the lowerer will apply. A file no pin lands
    in keeps the first pass's facts, so it is not re-checked at all.

    With the pyrefly lane, `pyrefly infer` then annotates the mirror tree
    to a fixpoint (issue #731): it proves the parameters and returns the
    single-pass pins cannot chain (a parameter proven from one call site
    feeds the next call site's argument). The checker re-checks every
    file the pass annotated, so the facts stay checker-proved and are
    recorded through `infer_records` with their proof, the same marking
    the pins carry; they are never written back to the user's source.
    """

    from pyscriptc.check import CheckFailed
    from pyscriptc.factsbridge import PyreflyBridge

    root = root.resolve()
    pins_by_file: dict[Path, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.py")):
        callee_head = _module_name(path, root)
        file_pins: dict[str, Any] = {}
        for (callee, param), pin in facts.pins.items():
            if callee.rpartition(".")[0] == callee_head:
                file_pins[param] = pin
        pins_by_file[path] = file_pins
    out: dict[Path, Any] = {}
    mirror = tempfile.mkdtemp(prefix="pyscriptc-pinned-")
    try:
        tree_dir = Path(mirror, "tree")
        # The mirror holds the whole tree, so an import of a sibling
        # module resolves the way it does in the original.
        shutil.copytree(root, tree_dir, dirs_exist_ok=True)
        # The pinned copy keeps the module's place in the package: a
        # relative import must resolve the same way it does in the
        # original, or the checker types the whole module `Any` and the
        # re-check proves nothing. The splice keeps every line number, so
        # the position-keyed fact maps still line up.
        targets: dict[Path, Path] = {}
        for path, file_pins in pins_by_file.items():
            if not file_pins:
                continue
            source = path.read_bytes()
            tree = ast.parse(source)
            module = _module_name(path, root)
            target = Path(tree_dir, *module.split(".")).with_suffix(".py")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(apply_pins(source, tree, file_pins))
            targets[path] = target
        inferred: dict[str, str] = _pyrefly_infer_annotations(tree_dir) if provider == "pyrefly" else {}
        # One `pyrefly tsp` server serves the whole re-check: the server
        # keeps a snapshot per open document, so a package costs one
        # server start instead of one per file.
        with PyreflyBridge() as bridge:
            for path in pins_by_file:
                if path in targets:
                    target = targets[path]
                else:
                    target = Path(tree_dir, path.relative_to(root))
                if path not in targets and str(target) not in inferred:
                    # No pin and no inferred annotation can change this
                    # file's facts, so the first pass already holds the
                    # answer.
                    continue
                try:
                    found = bridge.facts(target)
                except CheckFailed:
                    continue
                out[path] = found
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
