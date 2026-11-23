# SPDX-License-Identifier: Apache-2.0
"""Module roots: which directory owns an import name (issue #403).

The compiler's module universe was one root directory; every later item in
the library track needs the answer to "which root owns the import name
`certifi`". `RootSet` is that seam: pure data plus one lookup, built from
directories, never from the lowerer, so it carries no compiler imports and
no import cycle with `package.py`.

Two root shapes exist, mirroring `package.py`'s discovery:

- A **package** root owns a fixed dotted namespace: its `name` and every
  dotted submodule (`pkg`, `pkg.sub`). This is the directory-as-package
  shape, where the directory name is the module root.
- A **program** or **dependency** root is a `sys.path`-style entry: its
  top-level modules and packages import by their plain names, exactly the
  way `sys.path[0]` or an unpacked wheel's source root resolves them. A
  path root claims an import when the top-level segment names a ``*.py``
  file or a directory inside the root (a directory is a namespace package,
  the way CPython resolves it).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RootKind = Literal["program", "dependency", "package"]


@dataclass(frozen=True)
class ModuleRoot:
    """One module root.

    ``name`` is the root's dotted identity: for a package root the namespace
    its modules import under, for a path root the directory name (a label,
    since its modules import by their own plain names). ``path`` is the
    directory the modules live in. ``kind`` is ``"program"``,
    ``"dependency"`` or ``"package"``.
    """

    name: str
    path: Path
    kind: RootKind


class RootSet:
    """An ordered set of module roots with one import-name lookup."""

    def __init__(self, roots: Iterable[ModuleRoot] = ()) -> None:
        self._roots = tuple(roots)

    @property
    def roots(self) -> tuple[ModuleRoot, ...]:
        return self._roots

    def resolve(self, import_name: str) -> ModuleRoot | None:
        """The root that owns ``import_name``, or None.

        Package claims are more specific than path claims and win; among
        package claims the longest root name wins, and among path claims
        the first root in the set wins, the way ``sys.path`` order resolves.
        """
        claimants = self._claimants(import_name)
        return claimants[0] if claimants else None

    def roots_for(self, import_name: str) -> list[str]:
        """The names of every root that claims ``import_name``.

        Ordered like `resolve`: the winning root first, then the remaining
        claimants from most to least specific.
        """
        names: list[str] = []
        for root in self._claimants(import_name):
            if root.name not in names:
                names.append(root.name)
        return names

    def _claimants(self, import_name: str) -> list[ModuleRoot]:
        named = [r for r in self._roots if r.kind == "package" and _namespaced(r, import_name)]
        named.sort(key=lambda r: len(r.name), reverse=True)
        pathed = [r for r in self._roots if r.kind != "package" and _on_path(r, import_name)]
        return [*named, *pathed]


def roots_for_directory(path: Path) -> RootSet:
    """The one-root set for a compile directory.

    Mirrors ``_discover``'s identity rule: a program directory (a root
    ``__main__.py`` or ``main.py`` and no ``__init__.py``) is a path-style
    root; any other directory is a package whose modules import under the
    directory name.
    """

    root = Path(path).resolve()
    kind: RootKind = "program" if _program_shape(root) else "package"
    return RootSet([ModuleRoot(name=root.name, path=root, kind=kind)])


def _program_shape(root: Path) -> bool:
    """``_is_program_dir``'s rule, kept import-free to avoid the cycle."""

    if (root / "__init__.py").is_file():
        return False
    return (root / "__main__.py").is_file() or (root / "main.py").is_file()


def _namespaced(root: ModuleRoot, import_name: str) -> bool:
    """A package root claims its name and every dotted submodule."""

    return import_name == root.name or import_name.startswith(root.name + ".")


def _on_path(root: ModuleRoot, import_name: str) -> bool:
    """A path root claims ``import_name`` when its top level exists inside."""

    head = import_name.split(".", 1)[0]
    if not head.isidentifier():
        return False
    return (root.path / f"{head}.py").is_file() or (root.path / head).is_dir()
