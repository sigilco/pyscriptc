"""Bundle pure-Python dependencies into a bundle runtime's site-packages.

Everything here is boxed-only code (the ledger counts this module whole; see
`.agents/docs/boxed-cost.md` and `AGENTS.md`).

A `--embed-python=bundle` island program runs against the pack's CPython, not
the compiler's interpreter, so a third-party module the program imports must
travel with the bundle or the run dies with `ModuleNotFoundError`. This module
copies the distributions behind the program's third-party import roots into
`lib/pythonX.Y/site-packages/` of the staged bundle (the directory the pack's
`site` initialization puts on `sys.path`).

A distribution that ships a compiled extension (`.so`, `.pyd` and friends) is
refused with `PS4302`, never silently dropped: a copied extension would have to
match the pack's ABI and platform, and one that does not fails at runtime in
ways the compiler cannot check. An import root no installed distribution
provides is reported on stderr and skipped: the island fails at runtime exactly
like CPython would, which is the honest answer for a dead-branch import.

Direct imports only (the roots the program's own statements name); a
dependency-of-dependency arrives through its importer's imports when the
program reaches it.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


class SiteError(Exception):
    """A user-facing site-packages bundle failure (no traceback)."""


def third_party_roots(source: Path, exclude: Sequence[str] = ()) -> list[str]:
    """The absolute third-party import roots of a program, in source order.

    Stdlib roots go to the stdlib zip (issue #659), relative imports stay in
    the program tree, and the program's own package root is not a dependency.
    `exclude` names roots the caller declares extern (provided by the host,
    not carried by the bundle): they are dropped with a warning, so a
    native-backed optional dependency can stay behind while the program
    still compiles (the self-compile of pyscriptc excludes its lazy mypy
    bridge that way).
    """

    from pyscriptc.imports import collect_imports

    source = Path(source)
    own = source.stem if source.is_file() else source.name
    dropped = sorted(set(exclude))
    found: list[str] = []
    for site in collect_imports(source):
        if site["level"] > 0 or not site["name"]:
            continue
        root = site["name"].split(".")[0]
        if root in sys.stdlib_module_names or root == own:
            continue
        if root in dropped:
            continue
        if root not in found:
            found.append(root)
    for root in dropped:
        print(
            f"warning: {root} declared extern for the bundle; it is not carried "
            "(the host must provide it, like CPython would for a dead import)",
            file=sys.stderr,
        )
    return sorted(found)


def distributions_of(root: str) -> list[str]:
    """The installed distribution names an import root belongs to.

    `importlib.metadata.packages_distributions()` first (a top_level.txt map
    the installer writes); when it does not know the root, the site metadata
    answers: a `*.dist-info` whose RECORD lists the root, or else a package
    directory of that name on a site path as its own distribution (how source
    trees and RECORD-only installs answer).
    """

    from importlib import metadata

    try:
        found = list(metadata.packages_distributions().get(root, []))
    except Exception:
        found = []
    if found:
        return found
    from pyscriptc import imports

    for site_dir in imports._site_packages():
        if not site_dir.is_dir():
            continue
        for dist_info in sorted(site_dir.glob("*.dist-info")):
            if root in _record_roots(dist_info / "RECORD"):
                return [dist_info.name[: -len(".dist-info")]]
        if (site_dir / root).is_dir() or (site_dir / f"{root}.py").is_file():
            return [root]
    return []


def check_pure(roots: list[str]) -> None:
    """Refuse (PS4302) when a dependency the program imports is native-backed.

    Called before anything is staged, so a native dependency costs no fetch
    and no partial bundle. A root with no installed distribution is not this
    check's business: `stage_site_packages` warns and skips it, and the run
    answers like CPython would.
    """

    from pyscriptc import imports

    for dist, dist_roots in _closure(roots).items():
        native, reason = imports.distribution_is_native(dist)
        if native:
            raise SiteError(
                f"PS4302: the dependency {dist} (import {', '.join(dist_roots)}) cannot be bundled: {reason}; "
                "a bundle carries pure Python only - compile on the target platform or use "
                "an extern manifest for the native part"
            )


def stage_site_packages(lib_dir: Path, version: str, roots: list[str]) -> list[str]:
    """Copy the pure-Python distributions behind `roots` into the bundle.

    The copy set is the transitive closure over `Requires-Dist`: a dependency
    of a dependency travels with it (`python-dateutil` needs `six`), resolved
    against what this interpreter has installed. Returns the staged
    distribution names. A native-backed distribution anywhere in the closure
    raises `SiteError` with `PS4302`; a root with no installed distribution is
    reported on stderr and skipped.
    """

    from pyscriptc import imports

    dest = lib_dir / f"python{version}" / "site-packages"
    dest.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    resolved = _closure(roots)
    for root in roots:
        if not distributions_of(root):
            print(
                f"warning: {root} is not installed in this interpreter; the bundle will not carry it",
                file=sys.stderr,
            )
    for dist, dist_roots in resolved.items():
        native, reason = imports.distribution_is_native(dist)
        if native:
            raise SiteError(
                f"PS4302: the dependency {dist} (import {', '.join(dist_roots)}) cannot be bundled: "
                f"{reason}; a bundle carries pure Python only - compile on the target platform or "
                "use an extern manifest for the native part"
            )
        copied = False
        for root in dist_roots:
            copied = _copy_distribution(root, dist, dest) or copied
        if copied:
            staged.append(dist)
        else:
            print(
                f"warning: {dist} is installed but its files were not found on any site path",
                file=sys.stderr,
            )
    return staged


def _closure(roots: list[str]) -> dict[str, list[str]]:
    """Distribution name to the import roots that reach it, dependencies included.

    The walk is over the installed metadata only (`Requires-Dist` with no
    environment marker evaluation beyond what `importlib.metadata` gives); an
    unsatisfied requirement still travels when its distribution is installed,
    and a missing one ends the branch.
    """

    from importlib import metadata

    resolved: dict[str, list[str]] = {}
    queue: list[str] = list(roots)
    seen: set[str] = set()
    while queue:
        root = queue.pop(0)
        if root in seen:
            continue
        seen.add(root)
        for dist in distributions_of(root):
            # A distribution is copied for the import roots that reach it, so
            # `six` stages `six.py` even though only `dateutil` names it.
            roots_of = resolved.setdefault(dist, [])
            if root not in roots_of:
                roots_of.append(root)
            if dist in seen:
                continue
            seen.add(dist)
            try:
                requires = metadata.requires(dist) or []
            except Exception:
                requires = []
            for requirement in requires:
                if ";" in requirement:
                    marker = requirement.split(";", 1)[1]
                    # extra markers (`extra == "..."`) are optional unless the
                    # program opts in by importing the extra's modules, and a
                    # failing marker means the requirement does not apply here.
                    if "extra" not in marker:
                        try:
                            import packaging.markers

                            if not packaging.markers.Marker(marker).evaluate():
                                continue
                        except Exception:
                            pass
                name = requirement.split(";", 1)[0].strip()
                for separator in " <>=!~[":
                    name = name.split(separator, 1)[0]
                name = name.strip()
                if name and name not in seen:
                    queue.append(name)
    return resolved


def _copy_distribution(root: str, dist: str, dest: Path) -> None:
    """Copy one distribution's importable files and metadata into `dest`.

    `root` is an import name; the files it resolves to are the package
    directory or module file of that name on any site path, plus every
    `*.dist-info` whose RECORD lists the root or whose distribution name
    matches. Unknown roots are the caller's warning, not a copy attempt.
    """

    from pyscriptc import imports

    dest.mkdir(parents=True, exist_ok=True)
    copied = False
    for site_dir in imports._site_packages():
        if not site_dir.is_dir():
            continue
        for candidate in (site_dir / root, site_dir / f"{root}.py"):
            if candidate.is_dir():
                shutil.copytree(candidate, dest / candidate.name, dirs_exist_ok=True)
                copied = True
            elif candidate.is_file():
                shutil.copy2(candidate, dest / candidate.name)
                copied = True
        for dist_info in site_dir.glob("*.dist-info"):
            recorded = _record_roots(dist_info / "RECORD")
            if root in recorded or dist_info.name[: -len(".dist-info")].lower().replace("_", "-") == dist:
                shutil.copytree(dist_info, dest / dist_info.name, dirs_exist_ok=True)
                copied = True
    return copied


def _record_roots(record: Path) -> set[str]:
    """The top-level path roots a wheel RECORD lists."""

    if not record.is_file():
        return set()
    found: set[str] = set()
    for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
        entry = line.split(",", 1)[0].strip()
        if entry and "/" in entry:
            found.add(entry.split("/", 1)[0])
        elif entry:
            found.add(entry.rsplit(".py", 1)[0])
    return found
