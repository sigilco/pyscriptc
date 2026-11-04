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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyscriptc.config import POLICY_TO_LANE, lane_for
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
LANE_REFUSED = "refused"

# The user-facing ladder. An import is answered `native` (compile from
# source), `native: fences (PS20xx)` (the coverage report knows the codes) or
# `island (requires libpython)` (the per-import policy route). Nothing else
# reaches the island lane: it is never guessed from a package name or wheel
# metadata, only selected by `[tool.pyscriptc.imports]`.
LADDER_NATIVE = "native"
LADDER_FENCED = "native: fences"
LADDER_ISLAND = "island (requires libpython)"

# The label coverage and binary metadata carry for the island lane.
REQUIRES_LIBPYTHON = "requires libpython"

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
_NATIVE_SUFFIXES = [".so", ".pyd", ".dylib", ".dll"]

# The source of a verdict: which of the three sources answered it.
SOURCE_TABLE = "stdlib-queue"
SOURCE_DATA = "native-packages"
SOURCE_METADATA = "metadata"
SOURCE_HEURISTIC = "heuristic"
SOURCE_RELATIVE = "relative"


def _ladder(lane: str) -> str:
    """The user-facing answer for a lane: native or the island floor."""

    if lane == LANE_ISLAND:
        return LADDER_ISLAND
    if lane == LANE_REFUSED:
        return LANE_REFUSED
    return LADDER_NATIVE


def _verdict(
    name: str,
    kind: str,
    lane: str,
    reason: str,
    distribution: str = "",
    pack: str = "",
    source: str = SOURCE_TABLE,
) -> Verdict:
    """One verdict, as the report holds it."""

    return Verdict(name, kind, lane, reason, [], distribution, pack, source)


@dataclass
class Verdict:
    """One import verdict: what the import is and which lane it takes.

    `sites` collects the import statements grouped under this verdict while
    the report builds; the record-to-dict boundary is `verdict_row`, which
    the report (and its JSON export) reads.
    """

    name: str
    kind: str
    lane: str
    reason: str
    sites: list[dict[str, Any]]
    distribution: str = ""
    pack: str = ""
    source: str = SOURCE_TABLE

    @property
    def ladder(self) -> str:
        return _ladder(self.lane)

    @property
    def requires_libpython(self) -> bool:
        return self.lane == LANE_ISLAND

    def with_lane(self, lane: str, reason: str) -> Verdict:
        return Verdict(self.name, self.kind, lane, reason, self.sites, self.distribution, self.pack, self.source)


def verdict_row(verdict: Verdict) -> dict[str, Any]:
    """The dict row of a verdict, the record-to-dict boundary.

    The report (and its JSON export) holds rows; the classifier works on the
    records. The keys match the report schema the tests and `assess` read.
    """

    row: dict[str, Any] = {}
    row["name"] = verdict.name
    row["kind"] = verdict.kind
    row["lane"] = verdict.lane
    row["ladder"] = verdict.ladder
    row["requiresLibpython"] = verdict.requires_libpython
    row["reason"] = verdict.reason
    row["distribution"] = verdict.distribution
    row["pack"] = verdict.pack
    row["source"] = verdict.source
    row["sites"] = verdict.sites
    return row


@lru_cache(maxsize=1)
def native_packages() -> tuple[dict[str, Any], ...]:
    """The known native-backed distributions, from the data file."""

    payload: dict[str, Any] = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = payload["packages"]
    found: list[dict[str, Any]] = []
    for row in rows:
        found.append(row)
    return tuple(found)


@lru_cache(maxsize=1)
def _native_by_import() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for package in native_packages():
        names: list[str] = package["imports"]
        for name in names:
            if name not in found:
                found[name] = package
    return found


@lru_cache(maxsize=1)
def _native_by_dist() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for package in native_packages():
        dist: str = package["dist"]
        found[dist.lower().replace("_", "-")] = package
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
    """The file list of a wheel RECORD, or None when it is not readable.

    A RECORD inside an unpacked `*.dist-info/` directory is read as a text
    file; a RECORD still inside a wheel archive (the cached-wheel case) is
    read from the zip.
    """

    if not path.is_file():
        return None
    if not zipfile.is_zipfile(str(path)):
        # An unpacked `*.dist-info/RECORD` is a csv text file. Refuse a plain
        # text file whose lines carry no RECORD separators, so a misnamed
        # non-record is not read as a one-file wheel.
        try:
            body = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            return None
        files: list[str] = []
        for line in body.splitlines():
            if not line.strip():
                continue
            files.append(line.split(",", 1)[0])
        has_separator: bool = False
        for line in body.splitlines():
            if "," in line:
                has_separator = True
        if not has_separator:
            return None
        return files
    if not zipfile.is_zipfile(str(path)):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names: list[str] = archive.namelist()
    except OSError, Exception:
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
            raw: bytes = archive.read(record)
            body: str = raw.decode("utf-8")
    except OSError, KeyError, Exception:
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

    patterns = ["*.c", "*.cc", "*.cpp", "*.cxx", "*.rs", "*.pyx", "*.so"]
    for pattern in patterns:
        for _ in root.rglob(pattern):
            return True
    return False


def _metadata_of_tree(root: Path) -> dict[str, Any]:
    """Read the metadata of an unpacked distribution directory."""

    found: dict[str, Any] = {}
    found["dist"] = ""
    found["root"] = root
    found["native"] = False
    if not root.is_dir():
        return found
    for meta in (root / "PKG-INFO", root / "METADATA"):
        if not meta.is_file():
            continue
        try:
            body = meta.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
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
                key = metadata["dist"].lower().replace("_", "-").replace(" ", "-")
                if key not in found:
                    found[key] = metadata
        for dist_info in sorted(root.glob("*.dist-info")):
            name = dist_info.name[: -len(".dist-info")]
            key = name.lower().replace("_", "-").replace(" ", "-")
            # A versioned directory name (`markupsafe-3.0.4.dist-info`) does
            # not match the distribution key the metadata loop built, so strip
            # a trailing version before looking the entry up.
            stripped = key.split("-")[0] if key.rsplit("-", 1)[-1][0].isdigit() else key
            metadata = found.get(stripped) or found.get(key)
            if metadata is None:
                metadata = {"dist": name, "root": dist_info, "native": False}
                found[stripped] = metadata
            wheel = dist_info / "RECORD"
            if wheel.is_file():
                files = _read_wheel_record(wheel)
                if files is not None and _wheel_is_native(files):
                    metadata["native"] = True
    return found


def distribution_is_native(name: str) -> tuple[bool, str]:
    """Is the installed distribution native-backed?

    Returns `(True, reason)` or `(False, reason)` for every name: the reason
    string distinguishes a distribution that is genuinely pure from one that
    is not installed or unreadable (the reason says "not installed"). Both
    false answers read the same to the compiler, so callers must treat a
    False with a not-installed reason as unknown.
    """

    key = name.lower().replace("_", "-").replace(" ", "-")
    found = _installed_distributions()
    metadata = found.get(key)
    if metadata is None and "-" in key and key.rsplit("-", 1)[-1][0].isdigit():
        # A versioned lookup (`nativelib-1.0`) matches the distribution key
        # the site metadata built (`nativelib`).
        metadata = found.get(key.rsplit("-", 1)[0])
    if metadata is None:
        return False, "not installed in this interpreter"
    flag: bool = metadata["native"]
    native: bool = bool(flag)
    if native:
        return True, "the installed distribution carries a compiled extension"
    return False, "the installed distribution has no compiled extension"


# --------------------------------------------------------------------------
# Classification


def _stdlib_verdict(name: str) -> Verdict | None:
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
    in_table: bool = module_status(canonical_module(name)) is not None
    if root not in sys.stdlib_module_names and not in_table:
        return None
    if root in _ERASED:
        return _verdict(
            name,
            KIND_STDLIB,
            LANE_NATIVE,
            "annotation-only module; its types steer the lowering and the import is erased",
            source=SOURCE_TABLE,
        )
    status: str = module_status(canonical_module(name))
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


def _policy_key(name: str, policy: dict[str, str]) -> tuple[str, str] | None:
    """The policy entry that answers an import, full name first then its root."""

    lane, key = lane_for(name, policy)
    if key == "default":
        lane, key = lane_for(_top_level(name), policy)
    if key == "default":
        return None
    lane_name: str = POLICY_TO_LANE[lane]
    return lane_name, key


def apply_policy(verdict: Verdict, policy: dict[str, str] | None) -> Verdict:
    """Override a verdict with the per-import policy table, when it names the import.

    The table is the only route to the `island` lane. A policy entry also
    overrides a classifier verdict (a `native-backed` package the user
    deliberately routes to a pack, an extern manifest or an `ImportError`
    branch), because the policy is what the compile will do.
    """

    if not policy:
        return verdict
    found = _policy_key(verdict.name, policy)
    if found is None:
        return verdict
    lane, key = found
    if lane == verdict.lane:
        return verdict
    chosen = f"per-import policy {key!r}: {lane}"
    if lane == LANE_ISLAND:
        chosen = f"per-import policy {key!r}: island ({REQUIRES_LIBPYTHON}); the binary links the host interpreter"
    return verdict.with_lane(lane, chosen)


def classify(name: str, distribution: str = "", policy: dict[str, str] | None = None) -> Verdict:
    """Classify one import name into a kind and a lane.

    `policy` is the `[tool.pyscriptc.imports]` table when the caller has one.
    Without it the island lane is unreachable: the classifier answers what
    an import can take, not the user's opt-in choice.
    """

    return apply_policy(_classify_base(name, distribution), policy)


def _classify_base(name: str, distribution: str = "") -> Verdict:
    """The classifier verdict before the per-import policy is applied."""

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
        pack: str = known["pack"]
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
        dist_name: str = known["dist"]
        return _verdict(
            name,
            KIND_NATIVE,
            LANE_EXTERN,
            f"native-backed distribution {dist_name} (C, C++ or Rust extension)",
            dist_name,
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


def _group_key(verdict: Verdict) -> str:
    """The name a verdict is grouped under: the import root, not the submodule."""

    return _top_level(verdict.name)


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

    raw_family: str = entry["family"]
    family = str(raw_family)
    if family.startswith("stdlib:"):
        parts_all: list[str] = family.split(":")
        tail: str = parts_all[1]
        root: str = tail.split(".")[0]
        if root in roots:
            return root
        return None
    raw_message: str = entry["message"]
    message = str(raw_message)
    for candidate in roots:
        if re.search(rf"\b{re.escape(candidate)}\b", message):
            return candidate
    return None


def _capability_of(lane: str, codes: list[str], reflection: bool) -> str:
    if reflection:
        return CAP_ISLAND
    if codes:
        return CAP_FENCED
    if lane in {LANE_ISLAND, LANE_EXTERN}:
        return CAP_ISLAND
    return CAP_NATIVE


def _capability_ladder(capability: dict[str, Any]) -> str:
    routing: str = capability["routing"]
    if routing == CAP_ISLAND:
        return "island (requires libpython)"
    if routing == CAP_FENCED:
        raw_codes: list[str] = capability["codes"]
        codes = ", ".join(raw_codes)
        return f"native: fences ({codes})" if codes else "native: fences"
    return "native"


def _sorted_counts(counts: dict[str, int]) -> dict[str, int]:
    """The count table as a sorted dict, the report-summary shape."""

    found: dict[str, int] = {}
    for key in sorted(counts.keys()):
        found[key] = counts[key]
    return found


def build_report(
    target: Path,
    distributions: dict[str, str] | None = None,
    policy: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Classify every import of a file or package tree.

    `distributions` maps an import root to the distribution that provides it,
    so a caller that resolved the dependency graph can pass it in. Without it
    the classifier uses the data file and the metadata of the running
    interpreter. `policy` is the `[tool.pyscriptc.imports]` table; it is the
    only way an import reaches the island lane.
    """

    sites = collect_imports(target)
    dist_map: dict[str, str] = {}
    if distributions is not None:
        dist_map = distributions

    grouped: dict[str, Verdict] = {}
    root_name = _package_root(target)
    for site in sites:
        name: str = site["name"]
        level: int = site["level"]
        if level > 0 or not name or _top_level(name) == root_name:
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
            verdict = classify(name, dist_map.get(_top_level(name), ""), policy)
        key = _group_key(verdict)
        existing = verdict
        if key in grouped:
            existing = grouped[key]
            # A native-backed root wins over a pure guess for the same root.
            if existing.kind == KIND_PURE and verdict.kind != KIND_PURE:
                sites_json: list[dict[str, Any]] = existing.sites
                grouped[key] = verdict
                existing = verdict
                existing.sites = sites_json
        else:
            grouped[key] = verdict
        site_row: dict[str, Any] = {}
        site_file: str = site["file"]
        site_line: int = site["line"]
        site_statement: str = site["statement"]
        site_row["file"] = site_file
        site_row["line"] = site_line
        site_row["statement"] = site_statement
        site_row["import"] = name if name else "<package>"
        verdict_sites: list[dict[str, Any]] = existing.sites
        verdict_sites.append(site_row)

    rows: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        group: Verdict = grouped[key]
        row_value: dict[str, Any] = verdict_row(group)
        rows.append(row_value)
    fences = _fence_entries(target)
    roots: list[str] = []
    for item in rows:
        item_name: str = item["name"]
        roots.append(item_name)
    attributed: dict[str, list[dict[str, Any]]] = {}
    for root in roots:
        attributed[root] = []
    for fence in fences:
        found_root = _fence_root(fence, roots)
        if found_root is not None:
            root: str = found_root
            root_fences: list[dict[str, Any]] = attributed[root]
            root_fences.append(fence)

    by_lane: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_ladder: dict[str, int] = {}
    island_imports: list[str] = []
    island_statements = 0
    by_capability: dict[str, int] = {}
    for item in rows:
        item_name: str = item["name"]
        item_lane: str = item["lane"]
        item_kind: str = item["kind"]
        item_ladder: str = item["ladder"]
        item_needs: bool = item["requiresLibpython"]
        item_sites: list[dict[str, Any]] = item["sites"]
        empty_fences: list[dict[str, Any]] = []
        item_fences: list[dict[str, Any]] = attributed.get(item_name, empty_fences)
        code_set: dict[str, str] = {}
        for fence in item_fences:
            code_text: str = fence["code"]
            code_set[code_text] = code_text
        keys_list: list[str] = code_set.keys()
        codes: list[str] = sorted(keys_list)
        reflection: bool = item_needs
        for fence in item_fences:
            message_text: str = fence["message"]
            if routes_to_island(str(message_text)):
                reflection = True
        routing = _capability_of(item_lane, codes, reflection)
        mechanism_set: dict[str, str] = {}
        for fence in item_fences:
            message_text: str = fence["message"]
            found_mechanism = mechanism_of(str(message_text))
            if found_mechanism is not None:
                mechanism_set[found_mechanism] = found_mechanism
        file_set: dict[str, str] = {}
        for fence in item_fences:
            file_text: str = fence["file"]
            file_set[file_text] = file_text
        capability_row: dict[str, Any] = {}
        capability_row["routing"] = routing
        capability_row["codes"] = codes
        capability_row["mechanisms"] = sorted(mechanism_set.keys())
        capability_row["files"] = sorted(file_set.keys())
        capability_row["requires"] = "libpython" if routing == CAP_ISLAND else ""
        item["capability"] = capability_row
        by_lane[item_lane] = by_lane.get(item_lane, 0) + 1
        by_kind[item_kind] = by_kind.get(item_kind, 0) + 1
        answer: str = item_ladder
        if item_needs:
            answer = LADDER_ISLAND
            island_imports.append(item_name)
            island_statements = island_statements + len(item_sites)
        by_ladder[answer] = by_ladder.get(answer, 0) + 1
        by_capability[routing] = by_capability.get(routing, 0) + 1

    summary: dict[str, Any] = {}
    summary["imports"] = len(rows)
    summary["statements"] = len(sites)
    summary["byLane"] = _sorted_counts(by_lane)
    summary["byKind"] = _sorted_counts(by_kind)
    summary["byLadder"] = _sorted_counts(by_ladder)
    summary["requiresLibpython"] = bool(island_imports)
    summary["islandImports"] = island_imports
    summary["islandStatements"] = island_statements
    summary["reach"] = round(island_statements / len(sites), 4) if sites else 0.0
    summary["byCapability"] = _sorted_counts(by_capability)
    report: dict[str, Any] = {}
    report["schemaVersion"] = SCHEMA_VERSION
    report["target"] = str(target)
    report["summary"] = summary
    report["imports"] = rows
    return report


def render_text(report: dict[str, Any]) -> str:
    """The capability ladder, one line per import."""

    summary: dict[str, Any] = report["summary"]
    caps: dict[str, int] = summary["byCapability"]
    capability = ", ".join(f"{name} {caps[name]}" for name in (CAP_NATIVE, CAP_FENCED, CAP_ISLAND) if name in caps)
    if not capability:
        capability = "none"
    target_name: str = report["target"]
    import_count: int = summary["imports"]
    statement_count: int = summary["statements"]
    island_used: bool = summary["requiresLibpython"]
    parts: list[str] = [
        f"{target_name}: {import_count} imports ({statement_count} statements) by capability {capability}",
        f"island: {'requires libpython' if island_used else 'not used'}",
    ]
    report_imports: list[dict[str, Any]] = report["imports"]
    for item in report_imports:
        item_name: str = item["name"]
        item_kind: str = item["kind"]
        item_lane: str = item["lane"]
        item_reason: str = item["reason"]
        item_capability: dict[str, Any] = item["capability"]
        parts.append(f"  {item_name}: {_capability_ladder(item_capability)} [{item_kind}, lane {item_lane}]")
        parts.append(f"    {item_reason}")
    return "\n".join(parts)


def island_statements(report: dict[str, Any]) -> int:
    """Import statements that reach the island lane, from a report."""

    total: int = 0
    report_imports: list[dict[str, Any]] = report["imports"]
    for item in report_imports:
        sites: list[dict[str, Any]] = item["sites"]
        needs: bool = item["requiresLibpython"]
        if needs:
            total = total + len(sites)
    return total


def island_reach(report: dict[str, Any]) -> float:
    """Island statements as a share of all import statements, rounded to 4 places."""

    summary: dict[str, Any] = report["summary"]
    total: int = summary["statements"]
    if not total:
        return 0.0
    return round(island_statements(report) / total, 4)


def export_reach(report: dict[str, Any], path: Path) -> dict[str, Any]:
    """Append the island reach of a report to a `boxed_cost.py --reach` file.

    The file holds `{"boxed_only_reach": [oldest, ..., newest], "unit": "share"}`.
    A missing file starts the series; an unreadable one is an error, so a typo
    never silently resets the history.
    """

    initial_series: list[Any] = []
    payload: dict[str, Any] = {}
    payload["boxed_only_reach"] = initial_series
    payload["unit"] = "share"
    if path.is_file():
        existing: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError(f"{path}: the reach file must hold a JSON object")
        payload = existing
        payload["unit"] = "share"
    reach_series: list[Any] = payload["boxed_only_reach"]
    reach_series.append(island_reach(report))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def pack_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The imports that a curated pack could replace, most used first."""

    found: list[dict[str, Any]] = []
    report_imports: list[dict[str, Any]] = report["imports"]
    for item in report_imports:
        item_pack: str = item["pack"]
        if not item_pack:
            continue
        item_name: str = item["name"]
        item_distribution: str = item["distribution"]
        row: dict[str, Any] = {}
        row["import"] = item_name
        row["pack"] = item_pack
        row["distribution"] = item_distribution
        row_sites: list[dict[str, Any]] = item["sites"]
        row["statements"] = len(row_sites)
        found.append(row)
    found.sort(key=lambda item: (-item["statements"], item["import"]))
    return found
