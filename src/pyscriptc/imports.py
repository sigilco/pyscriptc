"""Classify each import of a program or package tree into a compat lane.

The adoption plan gives every import one lane: `native` (compile the pure
Python source), `pack` (a curated replacement pack), `extern` (a C ABI
manifest), `island` (opt-in boxed CPython) or `absent`. This module answers
the question that decides which of those a given import can take, without
touching the compiler and without a network call.

Four kinds of import are told apart:

- `stdlib`: a module in the standard library. The closed lowering table in
  `pyscriptc.stdlib_queue` says whether it lowers today or is still queued.
- `native-backed`: the import reaches a compiled extension (C, C++ or Rust).
  It cannot be compiled from pure-Python source, so it needs a pack, an
  extern manifest or the island.
- `curated`: a third-party import with a planned replacement pack. The
  native code is replaced by our own self-contained port.
- `pure`: a third-party import that is pure Python, so the compiler can
  compile it from source.

The verdict comes from three sources, in order: the import table of the
compiler itself, the data file `native_packages.json` with the known
native-backed distributions, and the metadata of the installed distribution
when it can be read offline (the wheel `RECORD`, the sdist `PKG-INFO`, or the
package directory itself). Nothing here reaches the network.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import sysconfig
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyscriptc.reflection import mechanism_of, routes_to_island
from pyscriptc.stdlib_queue import PACK_MODULES, canonical_module, module_status

SCHEMA_VERSION = 2

# The lanes of the policy table in the adoption plan. `native` is the default:
# compile the import from source when it is pure Python. `pack` swaps the
# import for a curated replacement pack. `extern` goes through a C ABI
# manifest. `island` is the opt-in boxed CPython tier. `absent` compiles the
# ImportError branch.
LANE_NATIVE = "native"
LANE_PACK = "pack"
LANE_EXTERN = "extern"
LANE_ISLAND = "island"
LANE_ABSENT = "absent"

# The capability ladder: what this import means for the program as compiled.
# `native` lowers as it is, `fenced` lowers but has a fence in this tree, and
# `island` needs the boxed CPython tier (or an ABI manifest, which is not a
# lowering either).
CAP_NATIVE = "native"
CAP_FENCED = "fenced"
CAP_ISLAND = "island"

# The classification of one import.
KIND_STDLIB = "stdlib"
KIND_NATIVE = "native-backed"
KIND_CURATED = "curated-replacement"
KIND_PURE = "pure-python"

# Annotation-only modules the checker keeps, so an import of them is free.
_ERASED = frozenset({"typing", "typing_extensions"})

_DATA_FILE = Path(__file__).parent / "native_packages.json"

# Extension suffixes CPython uses for a compiled extension module.
_NATIVE_SUFFIXES = (".so", ".pyd", ".dylib", ".dll")

# The source of a verdict: which of the three sources answered it.
SOURCE_TABLE = "stdlib-queue"
SOURCE_DATA = "native-packages"
SOURCE_METADATA = "metadata"
SOURCE_HEURISTIC = "heuristic"
SOURCE_RELATIVE = "relative"


def _verdict(
    name: str,
    kind: str,
    lane: str,
    reason: str,
    distribution: str = "",
    pack: str = "",
    source: str = SOURCE_TABLE,
) -> dict[str, Any]:
    """One verdict, as the report holds it."""

    return {
        "name": name,
        "kind": kind,
        "lane": lane,
        "reason": reason,
        "distribution": distribution,
        "pack": pack,
        "source": source,
        "sites": [],
    }


@lru_cache(maxsize=1)
def native_packages() -> tuple[dict[str, Any], ...]:
    """The known native-backed distributions, from the data file."""

    payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    return tuple(payload["packages"])


@lru_cache(maxsize=1)
def _native_by_import() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for package in native_packages():
        for name in package["imports"]:
            if name not in found:
                found[name] = package
    return found


@lru_cache(maxsize=1)
def _native_by_dist() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for package in native_packages():
        found[package["dist"].lower().replace("_", "-")] = package
    return found


def curated_packs() -> dict[str, str]:
    """Import name to the pack that replaces it, for the native-backed set."""

    found: dict[str, str] = {}
    for name, package in _native_by_import().items():
        if package["pack"]:
            found[name] = package["pack"]
    return found


# --------------------------------------------------------------------------
# Import collection


def _relative_name(node: ast.ImportFrom, package: str) -> str:
    """Resolve `from . import x` and `from .mod import y` against the package."""

    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    if node.level > 1:
        keep = len(parts) - (node.level - 1)
        parts = parts[:keep] if keep > 0 else []
    if node.module:
        parts = parts + node.module.split(".")
    return ".".join(parts)


def collect_imports(target: Path) -> list[dict[str, Any]]:
    """Every import statement of a file or package tree, in source order.

    The `name` of a site is the module the statement reaches. For
    `from X import y` that is `X`, not `X.y`: the lane depends on the module
    that holds the code.
    """

    target = Path(target)
    sites: list[dict[str, Any]] = []
    for path in _source_files(target):
        package = _package_of(path, target)
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except OSError, SyntaxError, UnicodeDecodeError:
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    sites.append(_site(path, node, lines, alias.name, 0))
            elif isinstance(node, ast.ImportFrom):
                name = _relative_name(node, package)
                sites.append(_site(path, node, lines, name, node.level))
    sites.sort(key=lambda site: (site["file"], site["line"], site["name"]))
    return sites


def _site(path: Path, node: ast.AST, lines: list[str], name: str, level: int) -> dict[str, Any]:
    line = getattr(node, "lineno", 1)
    statement = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
    return {"file": str(path), "line": line, "statement": statement, "name": name, "level": level}


def _source_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(target.rglob("*.py")):
        relative = path.relative_to(target)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        found.append(path)
    return found


def _package_of(path: Path, target: Path) -> str:
    """The dotted package name of a file inside a tree, or empty for a script."""

    if target.is_file():
        return ""
    relative = path.relative_to(target)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _package_root(target: Path) -> str:
    """The dotted name a tree's own modules carry, so they are not third-party."""

    if target.is_file():
        return ""
    return target.name


# --------------------------------------------------------------------------
# Distribution metadata, offline


def _read_wheel_record(path: Path) -> list[str] | None:
    """The file list of a wheel, or None when it is not a readable wheel."""

    if not zipfile.is_zipfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except OSError, zipfile.BadZipFile:
        return None
    record = ""
    for item in names:
        if item.endswith(".dist-info/RECORD"):
            record = item
            break
    if not record:
        return names
    try:
        with zipfile.ZipFile(path) as archive:
            body = archive.read(record).decode("utf-8", errors="replace")
    except OSError, KeyError, zipfile.BadZipFile:
        return names
    files: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        files.append(line.split(",", 1)[0])
    return files


def _wheel_is_native(files: list[str]) -> bool:
    """True when a wheel file list holds a compiled extension."""

    for item in files:
        for suffix in _NATIVE_SUFFIXES:
            if item.endswith(suffix):
                return True
    return False


def _tree_has_extension(root: Path) -> bool:
    """True when a source tree holds a C, C++ or Rust source beside the Python."""

    patterns = ("*.c", "*.cc", "*.cpp", "*.cxx", "*.rs", "*.pyx", "*.so")
    for pattern in patterns:
        for _ in root.rglob(pattern):
            return True
    return False


def _metadata_of_tree(root: Path) -> dict[str, Any]:
    """Read the metadata of an unpacked distribution directory."""

    found = {"dist": "", "root": root, "native": False}
    if not root.is_dir():
        return found
    for meta in (root / "PKG-INFO", root / "METADATA"):
        if not meta.is_file():
            continue
        try:
            body = meta.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in body.splitlines():
            if line.lower().startswith("name:"):
                found["dist"] = line.split(":", 1)[1].strip()
                break
        found["native"] = _tree_has_extension(root)
        return found
    return found


def _site_packages() -> list[Path]:
    """Where distributions live, from the running interpreter only."""

    found: list[Path] = []
    for key in ("purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            found.append(Path(path))
    for path in sys.path:
        if not path:
            continue
        candidate = Path(path)
        if candidate.name in ("site-packages", "dist-packages"):
            found.append(candidate)
    return found


@lru_cache(maxsize=1)
def _installed_distributions() -> dict[str, dict[str, Any]]:
    """Distribution name to metadata, for what the running interpreter has.

    A distribution is native-backed when the wheel RECORD lists a compiled
    extension, when the unpacked tree holds a C, C++ or Rust source next to
    the Python, or when the data file says so.
    """

    found: dict[str, dict[str, Any]] = {}
    for root in _site_packages():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            metadata = _metadata_of_tree(entry)
            if metadata["dist"]:
                key = metadata["dist"].lower().replace("_", "-")
                if key not in found:
                    found[key] = metadata
        for dist_info in sorted(root.glob("*.dist-info")):
            name = dist_info.name[: -len(".dist-info")]
            key = name.lower().replace("_", "-")
            metadata = found.get(key)
            if metadata is None:
                metadata = {"dist": name, "root": dist_info, "native": False}
                found[key] = metadata
            wheel = dist_info / "RECORD"
            if wheel.is_file():
                files = _read_wheel_record(wheel)
                if files is not None and _wheel_is_native(files):
                    metadata["native"] = True
    return found


def distribution_is_native(name: str) -> tuple[bool | None, str]:
    """Is the installed distribution native-backed?

    Returns `(True, reason)` or `(False, reason)` when the distribution is
    installed and readable, and `(None, reason)` when it is not installed or
    its metadata cannot be read. The caller must not read `None` as pure.
    """

    key = name.lower().replace("_", "-")
    metadata = _installed_distributions().get(key)
    if metadata is None:
        return None, "not installed in this interpreter"
    if metadata["native"]:
        return True, "the installed distribution carries a compiled extension"
    return False, "the installed distribution has no compiled extension"


# --------------------------------------------------------------------------
# Classification


def _stdlib_verdict(name: str) -> dict[str, Any] | None:
    """Classify a standard-library import, or None when it is not stdlib.

    The closed import table wins over `sys.stdlib_module_names`, so a
    lowered module the interpreter does not ship (the dotenv config pack)
    still classifies by its table status.
    """

    root = _top_level(name)
    if canonical_module(name) in PACK_MODULES or root in PACK_MODULES:
        # A pack module rides the curated-pack lane, not the stdlib table,
        # even though the queue lists it so the lowering binds it.
        return None
    in_table = module_status(canonical_module(name)) is not None
    if root not in sys.stdlib_module_names and not in_table:
        return None
    if root in _ERASED:
        return _verdict(
            name,
            KIND_STDLIB,
            LANE_NATIVE,
            "annotation-only module; the checker keeps it and the lowering erases it",
            source=SOURCE_TABLE,
        )
    status = module_status(canonical_module(name))
    if status == "lowered":
        return _verdict(
            name,
            KIND_STDLIB,
            LANE_NATIVE,
            "standard library, lowered by the compiler today",
            source=SOURCE_TABLE,
        )
    if status == "queued":
        return _verdict(
            name,
            KIND_STDLIB,
            LANE_NATIVE,
            "standard library, queued for lowering",
            source=SOURCE_TABLE,
        )
    return _verdict(
        name,
        KIND_STDLIB,
        LANE_EXTERN,
        "standard library, outside the closed import table; try a C ABI extern manifest",
        source=SOURCE_TABLE,
    )


def classify(name: str, distribution: str = "") -> dict[str, Any]:
    """Classify one import name into a kind and a lane."""

    if not name:
        return _verdict(
            name,
            KIND_PURE,
            LANE_NATIVE,
            "empty import name",
            source=SOURCE_HEURISTIC,
        )

    stdlib = _stdlib_verdict(name)
    if stdlib is not None:
        return stdlib

    root = _top_level(name)
    known = _native_by_import().get(name)
    if known is None:
        known = _native_by_import().get(root)
    if known is None and distribution:
        known = _native_by_dist().get(distribution.lower().replace("_", "-"))
    if known is not None:
        pack = known["pack"]
        if pack:
            return _verdict(
                name,
                KIND_CURATED,
                LANE_PACK,
                f"curated replacement pack {pack} covers this import",
                known["dist"],
                pack,
                SOURCE_DATA,
            )
        return _verdict(
            name,
            KIND_NATIVE,
            LANE_EXTERN,
            f"native-backed distribution {known['dist']} (C, C++ or Rust extension)",
            known["dist"],
            "",
            SOURCE_DATA,
        )

    if distribution:
        native, reason = distribution_is_native(distribution)
        if native:
            return _verdict(
                name,
                KIND_NATIVE,
                LANE_EXTERN,
                f"native-backed distribution {distribution}: {reason}",
                distribution,
                "",
                SOURCE_METADATA,
            )

    return _verdict(
        name,
        KIND_PURE,
        LANE_NATIVE,
        "third-party import with no known compiled extension; compile it from source",
        "",
        "",
        SOURCE_HEURISTIC,
    )


def _group_key(verdict: dict[str, Any]) -> str:
    """The name a verdict is grouped under: the import root, not the submodule."""

    return _top_level(verdict["name"])


def _fence_entries(target: Path) -> list[dict[str, Any]]:
    """The lowered fences of the target, or an empty list when it cannot lower.

    The classifier itself stays compiler-free; this seam borrows the coverage
    report so an import entry can say whether the code that uses it is fenced
    or island-routed. A target that does not lower is not an import error.
    """

    try:
        from pyscriptc.coverage import build_report as coverage_report

        return coverage_report(target)["fences"]
    except Exception:
        return []


def _fence_root(entry: dict[str, Any], roots: list[str]) -> str | None:
    """The import root a fence belongs to, or None when it belongs to no import."""

    family = str(entry.get("family", ""))
    if family.startswith("stdlib:"):
        root = family.split(":", 1)[1].split(".")[0]
        return root if root in roots else None
    message = str(entry.get("message", ""))
    for root in roots:
        if re.search(rf"\b{re.escape(root)}\b", message):
            return root
    return None


def _capability_of(lane: str, codes: list[str], reflection: bool) -> str:
    if reflection:
        return CAP_ISLAND
    if codes:
        return CAP_FENCED
    if lane in {LANE_ISLAND, LANE_EXTERN}:
        return CAP_ISLAND
    return CAP_NATIVE


def _ladder(capability: dict[str, Any]) -> str:
    routing = capability["routing"]
    if routing == CAP_ISLAND:
        return "island (requires libpython)"
    if routing == CAP_FENCED:
        codes = ", ".join(capability["codes"])
        return f"native: fences ({codes})" if codes else "native: fences"
    return "native"


def build_report(target: Path, distributions: dict[str, str] | None = None) -> dict[str, Any]:
    """Classify every import of a file or package tree.

    `distributions` maps an import root to the distribution that provides it,
    so a caller that resolved the dependency graph can pass it in. Without it
    the classifier uses the data file and the metadata of the running
    interpreter.
    """

    target = Path(target)
    sites = collect_imports(target)
    if distributions is None:
        distributions = {}

    grouped: dict[str, dict[str, Any]] = {}
    root_name = _package_root(target)
    for site in sites:
        name = site["name"]
        if site["level"] > 0 or not name or _top_level(name) == root_name:
            # A relative import stays inside the tree, so it compiles from source.
            verdict = _verdict(
                name if name else "<package>",
                KIND_PURE,
                LANE_NATIVE,
                "relative import inside the tree; compile it from source",
                "",
                "",
                SOURCE_RELATIVE,
            )
        else:
            verdict = classify(name, distributions.get(_top_level(name), ""))
        key = _group_key(verdict)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = verdict
            existing = verdict
        else:
            # A native-backed root wins over a pure guess for the same root.
            if existing["kind"] == KIND_PURE and verdict["kind"] != KIND_PURE:
                sites_json = existing["sites"]
                grouped[key] = verdict
                existing = verdict
                existing["sites"] = sites_json
        existing["sites"].append(
            {
                "file": site["file"],
                "line": site["line"],
                "statement": site["statement"],
                "import": name if name else "<package>",
            }
        )

    imports = [grouped[key] for key in sorted(grouped)]
    fences = _fence_entries(target)
    roots = [item["name"] for item in imports]
    attributed: dict[str, list[dict[str, Any]]] = {root: [] for root in roots}
    for fence in fences:
        root = _fence_root(fence, roots)
        if root is not None:
            attributed[root].append(fence)

    by_lane: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_capability: dict[str, int] = {}
    for item in imports:
        item_fences = attributed.get(item["name"], [])
        codes = sorted({str(fence["code"]) for fence in item_fences})
        reflection = any(routes_to_island(str(fence.get("message", ""))) for fence in item_fences)
        routing = _capability_of(str(item["lane"]), codes, reflection)
        item["capability"] = {
            "routing": routing,
            "codes": codes,
            "mechanisms": sorted(
                {found for fence in item_fences if (found := mechanism_of(str(fence.get("message", "")))) is not None}
            ),
            "files": sorted({str(fence["file"]) for fence in item_fences}),
            "requires": "libpython" if routing == CAP_ISLAND else "",
        }
        by_lane[item["lane"]] = by_lane.get(item["lane"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_capability[routing] = by_capability.get(routing, 0) + 1

    return {
        "schemaVersion": SCHEMA_VERSION,
        "target": str(target),
        "summary": {
            "imports": len(imports),
            "statements": len(sites),
            "byLane": dict(sorted(by_lane.items())),
            "byKind": dict(sorted(by_kind.items())),
            "byCapability": dict(sorted(by_capability.items())),
        },
        "imports": imports,
    }


def render_text(report: dict[str, Any]) -> str:
    """The capability ladder, one line per import."""

    summary = report["summary"]
    caps = summary["byCapability"]
    capability = ", ".join(f"{name} {caps[name]}" for name in (CAP_NATIVE, CAP_FENCED, CAP_ISLAND) if name in caps)
    if not capability:
        capability = "none"
    parts = [
        f"{report['target']}: {summary['imports']} imports ({summary['statements']} statements) "
        f"by capability {capability}"
    ]
    for item in report["imports"]:
        parts.append(f"  {item['name']}: {_ladder(item['capability'])} [{item['kind']}, lane {item['lane']}]")
        parts.append(f"    {item['reason']}")
    return "\n".join(parts)


def pack_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The imports that a curated pack could replace, most used first."""

    found: list[dict[str, Any]] = []
    for item in report["imports"]:
        if not item["pack"]:
            continue
        found.append(
            {
                "import": item["name"],
                "pack": item["pack"],
                "distribution": item["distribution"],
                "statements": len(item["sites"]),
            }
        )
    found.sort(key=lambda item: (-item["statements"], item["import"]))
    return found
