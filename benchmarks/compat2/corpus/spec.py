"""Build the per package scoring spec: public closed signature functions and candidate test oracles.

Run inside the Linux container: `python -m benchmarks.compat2.corpus.spec`. It only parses
source with `ast`; nothing from the pinned packages is imported or executed.

A function is closed when it is a top level, undecorated, non-async, non-generator def in a public
module, every parameter is annotated int, str or bool without *args or **kwargs, and the return
annotation is int, str or bool. This mirrors `_closed_args` in the frozen v1 scorer, but works on the
source so a package that still has fences can be listed. Only required parameters are called.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLOSED = {"int": 1, "str": "a", "bool": True}


# Calls the auto-picked arguments get wrong on CPython, fixed by hand (qual -> call_args).
ARG_FIXES: dict[str, list[object]] = {
    "idna.core.valid_contextj": ["a", 0],
    "idna.core.valid_contexto": ["a", 0],
    "wheel._commands.parse_build_tag": ["1abc"],
}
# Closed signatures that cannot be scored: they need a TTY, installed metadata, Windows, or are a CLI entry point.
DROPPED: dict[str, str] = {
    "click.click.termui.hidden_prompt_func": "needs a TTY",
    "click.click.termui.getchar": "needs a TTY",
    "importlib-metadata.importlib_metadata.version": "needs installed package metadata",
    "platformdirs.platformdirs.windows.get_win_folder": "Windows only",
    "platformdirs.platformdirs.windows.get_win_folder_from_env_vars": "Windows only",
    "platformdirs.platformdirs.windows.get_win_folder_from_registry": "Windows only",
    "platformdirs.platformdirs.windows.get_win_folder_via_ctypes": "Windows only",
    "wheel.wheel._commands.main": "argparse entry point, reads sys.argv",
}


def apply_fixes(project: str, functions: list[dict[str, object]]) -> list[dict[str, object]]:
    kept = []
    for fn in functions:
        if f"{project}.{fn['qual']}" in DROPPED:
            continue
        kept.append({**fn, "call_args": ARG_FIXES.get(str(fn["qual"]), fn["call_args"])})
    return kept


def _annotation(node: ast.expr | None) -> str:
    return node.id if isinstance(node, ast.Name) and node.id in CLOSED else ""


def _is_generator(fn: ast.FunctionDef) -> bool:
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))
    return False


def closed_signature(fn: ast.FunctionDef) -> dict[str, object] | None:
    args = fn.args
    if fn.decorator_list or args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        return None
    returns = _annotation(fn.returns)
    if not returns or _is_generator(fn):
        return None
    required = args.args[: len(args.args) - len(args.defaults)]
    params = []
    for arg in args.args:
        kind = _annotation(arg.annotation)
        if not kind:
            return None
        params.append({"name": arg.arg, "kind": kind})
    call = [CLOSED[p["kind"]] for p, a in zip(params, args.args, strict=True) if a in required]
    return {"params": params, "returns": returns, "call_args": call}


def _module_name(import_name: str, tree: Path, file: Path) -> str:
    if tree.is_file():
        return import_name
    rel = file.relative_to(tree).with_suffix("")
    parts = [import_name, *rel.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_public(parts: list[str]) -> bool:
    return not any(p.startswith("_") for p in parts[1:])


def package_functions(import_name: str, tree: Path) -> list[dict[str, object]]:
    files = [tree] if tree.is_file() else sorted(tree.rglob("*.py"))
    found = []
    for file in files:
        module = _module_name(import_name, tree, file)
        rel_parts = module.split(".")
        if not _is_public(rel_parts) and file.name != "__init__.py":
            continue
        try:
            module_ast = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:
            continue
        for node in module_ast.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                sig = closed_signature(node)
                if sig is not None:
                    found.append({"module": module, "name": node.name, "qual": f"{module}.{node.name}", **sig})
    return found


def test_oracles(tests: Path, names: set[str]) -> dict[str, object]:
    """Test files and the package functions each one calls by name (candidate oracles)."""

    files = []
    covered: dict[str, set[str]] = {}
    count = 0
    for file in sorted(tests.rglob("*.py")) if tests.is_dir() else [tests]:
        try:
            module_ast = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:
            continue
        defs = [n for n in ast.walk(module_ast) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
        called = {
            (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
            for n in ast.walk(module_ast)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
        } & names
        count += len(defs)
        files.append({"file": str(file.relative_to(tests.parent if tests.is_file() else tests)), "tests": len(defs)})
        for name in called:
            covered.setdefault(name, set()).add(file.name)
    return {
        "test_count": count,
        "files": files,
        "functions_called_by_tests": {k: sorted(v) for k, v in sorted(covered.items())},
    }


def build(pins_path: Path) -> dict[str, object]:
    pins = json.loads(pins_path.read_text(encoding="utf-8"))["packages"]
    out: dict[str, object] = {}
    for project, pin in sorted(pins.items()):
        tree = (pins_path.parent / str(pin["tree"])).resolve()
        if not tree.exists():
            continue
        functions = apply_fixes(project, package_functions(str(pin["import_name"]), tree))
        entry: dict[str, object] = {
            "import_name": pin["import_name"],
            "version": pin["version"],
            "closed_functions": functions,
        }
        tests = pin.get("tests")
        if tests and (pins_path.parent / str(tests)).exists():
            entry["oracles"] = test_oracles(
                (pins_path.parent / str(tests)).resolve(), {str(f["name"]) for f in functions}
            )
        out[project] = entry
    return out


def main() -> None:
    for pins_path, target in [
        (HERE / "pins.json", HERE / "package_spec.json"),
        (HERE / "holdout" / "pins.json", HERE / "holdout" / "package_spec.json"),
    ]:
        spec = build(pins_path)
        target.write_text(json.dumps({"packages": spec}, indent=1) + "\n")
        total = sum(len(e["closed_functions"]) for e in spec.values())  # type: ignore[index]
        print(target.relative_to(HERE), len(spec), "packages", total, "closed functions")


if __name__ == "__main__":
    main()
