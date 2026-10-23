"""Project mode: one binary per `[project.scripts]` entry point.

`pyscriptc build` reads the pyproject's console scripts, resolves each
`module:attr` against the project's module roots (`src` by default), and
lowers the tree behind that entry into one standalone binary per script.
Two scripts of the same package compile independently; the shared modules
are lowered into each binary.

The entry contract is the console script's, not the module's: CPython runs
`module:attr` and exits with the attribute's return value, so the wrapper
the compiler synthesizes is

    def main() -> int:
        return attr()

A tree whose entry does not import a module with live script statements
lowers without the wrapper; the `importlib` machinery the real console
script uses has no lowered counterpart and is never emitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyscriptc import config
from pyscriptc.errors import IRError
from pyscriptc.package import lower_directory


class ProjectError(IRError):
    """A project-mode error: the pyproject, its scripts or a root is unusable."""


def discover_pyproject(directory: Path) -> Path:
    """The project root's pyproject.toml, walking up like config discovery."""

    start = directory.resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        path = candidate / "pyproject.toml"
        if path.is_file():
            return path
    raise ProjectError(f"no pyproject.toml above {directory}")


def script_entry(pyproject: Path, name: str) -> str:
    """The `module:attr` target of one console script."""

    target = config.entry_points(pyproject).get(name)
    if target is None:
        known = ", ".join(sorted(config.entry_points(pyproject)))
        raise ProjectError(f"{pyproject}: no console script {name!r}" + (f" (known: {known})" if known else ""))
    return target


def roots_note(pyproject: Path) -> str:
    return ", ".join(config.project_roots(pyproject))


def lower_script(
    pyproject: Path,
    name: str,
    bridge: Any = None,
) -> tuple[dict[str, Any], str]:
    """The IR for one console script's binary.

    `lower_directory` does the tree walk and the program shape; the console
    script's module plays the `__main__` role, so the walk starts there and
    the module bodies run in CPython's import order.

    The IR entry is a synthesized no-argument wrapper named `main` that
    calls the console script's `module:attr` and returns its value as the
    process exit code, the contract the generated console script has
    (`sys.exit(main())`). A module-level script body in the entry module is
    the fallback when the attribute is not a lowered function.
    """

    target = config.entry_points(pyproject)[name]
    module, attr = target.split(":")
    entry_file = script_file(pyproject, module)
    root = _tree_root(entry_file, module, pyproject)
    prefix = prefix_of(module, root)
    merged, _source = lower_directory(root, program_mode=True, bridge=bridge, entry=module, prefix=prefix)
    merged["sourceFile"] = str(entry_file)
    _retarget_entry(merged, module, attr)
    return merged, ""


def script_file(pyproject: Path, module: str) -> Path:
    """The entry module's file, resolved against the project's roots."""

    base = pyproject.parent
    for root in config.project_roots(pyproject):
        candidate = base / root / Path(*module.split(".")).with_suffix(".py")
        if candidate.is_file():
            return candidate
        package = base / root / Path(*module.split(".")) / "__init__.py"
        if package.is_file():
            return package
    raise ProjectError(f"{pyproject}: {module} not found under the project roots {roots_note(pyproject)}")


def prefix_of(module: str, root: Path) -> str:
    """The dotted package prefix the tree's modules carry.

    The walk root is the entry module's package (``src/mylib`` for
    ``mylib.hello``), so the prefix is that package's name; a top-level
    module walked from the project root carries no prefix.
    """

    parts = module.split(".")
    if len(parts) > 1:
        # The module sits in a package: the tree root is that package.
        return parts[-2] if parts[-1] != "__init__" else parts[-1]
    # A top-level module: the tree root is the module's own directory.
    return root.name if (root / "__init__.py").is_file() else ""


def _tree_root(entry_file: Path, module: str, pyproject: Path) -> Path:
    """The directory lower_directory walks: the entry's package root.

    A module file inside a package lowers with its package (relative imports
    keep working); a top-level module lowers with the project root.
    """

    base = pyproject.parent
    roots = config.project_roots(pyproject)
    parts = module.split(".")
    # A module inside a package lowers with its package; a package module
    # lowers with its own directory.
    tail = parts[:-1] if len(parts) > 1 else parts
    root = base / roots[0] / Path(*tail)
    if (root / "__init__.py").is_file():
        return root
    if len(parts) > 1:
        parent = base / roots[0] / Path(*parts[:-1])
        if (parent / "__init__.py").is_file():
            return parent
    return (base / roots[0]) if (base / roots[0]).is_dir() else base


def _retarget_entry(merged: dict[str, Any], module: str, attr: str) -> None:
    """Point the IR entry at the console script's contract.

    The lowered tree's entry is the entry module's script body (`__main__`
    in program mode). A console script runs the attribute and exits with
    its return value, so the entry becomes a synthesized `main` wrapper
    around `module.attr` when that function lowered. Without the function,
    the entry module's own script body is the binary: its live statements
    are the script the module runs at import time.
    """

    names = {fn["name"] for fn in merged.get("functions", [])}
    target = f"{module}.{attr}"
    if target not in names:
        return
    # CPython runs the entry module's body at import, then calls the
    # attribute. The synthesized `main` does both, in that order: the
    # module's own entry function (`__entry__` or `main`) first, then the
    # attribute, whose return value is the process exit code.
    span = {"file": merged["sourceFile"], "line": 1, "col": 1, "endLine": 1, "endCol": 1}
    body: list[dict[str, Any]] = []
    script_entry = merged.get("entry")
    script_fn = next((fn for fn in merged["functions"] if fn["name"] == script_entry), None)
    if script_fn is not None and script_entry != target:
        if script_fn["returnType"]["kind"] == "int":
            body.append(
                {
                    "kind": "expr",
                    "expr": {
                        "kind": "call",
                        "callee": {"kind": "function", "name": script_entry},
                        "args": [],
                        "type": {"kind": "int"},
                        "span": span,
                    },
                    "span": span,
                }
            )
        elif script_fn["returnType"]["kind"] == "none":
            body.append(
                {
                    "kind": "expr",
                    "expr": {
                        "kind": "call",
                        "callee": {"kind": "function", "name": script_entry},
                        "args": [],
                        "type": {"kind": "none"},
                        "span": span,
                    },
                    "span": span,
                }
            )
    body.append(
        {
            "kind": "expr",
            "expr": {
                "kind": "call",
                "callee": {"kind": "module", "module": "sys", "name": "exit"},
                "args": [
                    {
                        "kind": "call",
                        "callee": {"kind": "function", "name": target},
                        "args": [],
                        "type": {"kind": "int"},
                        "span": span,
                    }
                ],
                "type": {"kind": "none"},
                "span": span,
                "symbol": "exit",
            },
            "span": span,
        }
    )
    merged["functions"].append(
        {
            "name": "main",
            "params": [],
            "returnType": {"kind": "none"},
            "locals": [],
            "body": body,
        }
    )
    merged["entry"] = "main"
