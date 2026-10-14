"""Fetch, verify and snapshot pinned package sources into compat2/corpus.

Run on the host: `python3 -m benchmarks.compat2.corpus.pin`. It only downloads and extracts
(never imports or runs) the fetched content. Each sdist goes into a fresh temp dir, is checked
against its sha256, and is extracted with the `data` tar filter.

The frozen v1 `compat/top100/pins.json` is only read. New pins go to `corpus/pins.json`
(visible set) and `corpus/holdout/pins.json` (held-out set).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_PINS = HERE.parent.parent / "compat" / "top100" / "pins.json"
FETCHED = "2026-10-14"

# project: (import_name, tree inside the sdist, tests inside the sdist or "", license file, rank)
# A tree ending in .py is a single module. Test dirs found inside a tree are moved out of it.
SPECS: dict[str, tuple[str, str, str, str, int]] = {
    "six": ("six", "six.py", "test_six.py", "LICENSE", 15),
    "typing-extensions": ("typing_extensions", "src/typing_extensions.py", "src/test_typing_extensions.py", "LICENSE", 3),
    "annotated-types": ("annotated_types", "annotated_types", "tests", "LICENSE", 26),
    "sniffio": ("sniffio", "sniffio", "", "LICENSE.MIT", 72),
    "pluggy": ("pluggy", "src/pluggy", "testing", "LICENSE", 16),
    "tenacity": ("tenacity", "tenacity", "tests", "LICENSE", 81),
    "markupsafe": ("markupsafe", "src/markupsafe", "tests", "LICENSE.txt", 34),
    "pyparsing": ("pyparsing", "pyparsing", "tests", "LICENSE", 90),
    "soupsieve": ("soupsieve", "soupsieve", "tests", "LICENSE.md", 98),
    "tomlkit": ("tomlkit", "tomlkit", "tests", "LICENSE", 53),
    "markdown-it-py": ("markdown_it", "markdown_it", "", "LICENSE", 54),
    "referencing": ("referencing", "referencing", "", "COPYING", 56),
    "attrs": ("attr", "src/attr", "tests", "LICENSE", 29),
    "click": ("click", "src/click", "tests", "LICENSE.txt", 20),
    "python-multipart": ("python_multipart", "python_multipart", "tests", "LICENSE.txt", 83),
    "annotated-doc": ("annotated_doc", "src/annotated_doc", "tests", "LICENSE", 69),
    "typing-inspection": ("typing_inspection", "src/typing_inspection", "tests", "LICENSE", 28),
    "aiohappyeyeballs": ("aiohappyeyeballs", "src/aiohappyeyeballs", "tests", "LICENSE", 67),
    "aiosignal": ("aiosignal", "aiosignal", "tests", "LICENSE", 66),
    "pyasn1": ("pyasn1", "pyasn1", "tests", "LICENSE.rst", 65),
    "pycparser": ("pycparser", "pycparser", "tests", "LICENSE", 19),
    "wheel": ("wheel", "src/wheel", "tests", "LICENSE.txt", 80),
    "importlib-metadata": ("importlib_metadata", "importlib_metadata", "tests", "LICENSE", 78),
    "jsonschema-specifications": ("jsonschema_specifications", "jsonschema_specifications", "", "COPYING", 57),
    "python-dateutil": ("dateutil", "src/dateutil", "tests", "LICENSE", 14),
    "tqdm": ("tqdm", "tqdm", "tests", "LICENCE", 62),
}

# Held out (about 20%): scored only at epic boundaries, never shown to the generator agent.
HOLDOUT = frozenset({"annotated-types", "tenacity", "six", "pycparser", "aiosignal"})
INNER_TEST_DIRS = {"tests", "_tests", "test", "testing"}


def _resolve(project: str) -> dict[str, object]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/json", timeout=30) as resp:
        data = json.load(resp)
    sdist = next(u for u in data["urls"] if u["packagetype"] == "sdist")
    info = data["info"]
    return {
        "version": info["version"],
        "filename": sdist["filename"],
        "sha256": sdist["digests"]["sha256"],
        "url": sdist["url"],
        "size": sdist["size"],
        "license": info.get("license_expression") or info.get("license") or "",
        "requires": [r for r in (info.get("requires_dist") or []) if "extra ==" not in r],
    }


def _extract(meta: dict[str, object], dest: Path) -> Path:
    with urllib.request.urlopen(str(meta["url"]), timeout=120) as resp:
        blob = resp.read()
    if hashlib.sha256(blob).hexdigest() != meta["sha256"]:
        raise SystemExit(f"sha256 mismatch for {meta['filename']}")
    archive = dest / str(meta["filename"])
    archive.write_bytes(blob)
    out = dest / "x"
    out.mkdir()
    if archive.suffix == ".zip":
        zipfile.ZipFile(archive).extractall(out)
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(out, filter="data")
    (root,) = out.iterdir()
    return root


def _copy_tree(src: Path, dst: Path, moved_tests: Path | None) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so", "*.c"))
    for inner in sorted(dst.rglob("*")):
        if inner.is_dir() and inner.name in INNER_TEST_DIRS and moved_tests is not None:
            target = moved_tests / inner.relative_to(dst)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(inner), target)


def snapshot(project: str, base: Path) -> dict[str, object]:
    import_name, tree, tests, license_name, rank = SPECS[project]
    meta = _resolve(project)
    with tempfile.TemporaryDirectory(prefix="corpus-pin-") as tmp:
        root = _extract(meta, Path(tmp))
        tests_dir = base / "tests" / project
        dest_name = import_name + (".py" if tree.endswith(".py") else "")
        _copy_tree(root / tree, base / "src" / dest_name, tests_dir)
        if tests and (root / tests).exists():
            _copy_tree(root / tests, tests_dir / Path(tests).name, None)
        lic = base / "licenses" / f"{project}-{license_name}"
        lic.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / license_name, lic)
    has_tests = tests_dir.exists() and any(tests_dir.rglob("*.py"))
    return {
        "project": project,
        "import_name": import_name,
        "rank": rank,
        **meta,
        "tree": f"src/{dest_name}",
        "license_file": lic.name,
        "tests": f"tests/{project}" if has_tests else "",
    }


def main() -> None:
    v1 = json.loads(V1_PINS.read_text(encoding="utf-8"))["packages"]
    visible = {name: {**pin, "origin": "v1", "tree": f"../../compat/top100/{pin['tree']}"} for name, pin in v1.items()}
    held: dict[str, object] = {}
    for project in SPECS:
        base = HERE / "holdout" if project in HOLDOUT else HERE
        pin = snapshot(project, base)
        pin["origin"] = "v2"
        (held if project in HOLDOUT else visible)[project] = pin
        print(project, pin["version"])
    head = {"fetched": FETCHED, "index": "https://pypi.org/pypi"}
    note = "Pinned sdists. tree is relative to this file's directory. tests holds the upstream tests, usable as oracles only inside the Linux container."
    (HERE / "pins.json").write_text(json.dumps({**head, "note": note, "packages": visible}, indent=2) + "\n")
    (HERE / "holdout" / "pins.json").write_text(json.dumps({**head, "note": note, "packages": held}, indent=2) + "\n")


if __name__ == "__main__":
    main()
