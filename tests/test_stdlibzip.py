"""The bundled stdlib of a runtime pack: safe-trim zip, manifest check, minimal trim.

Refs #659. The unit tests here build a fake stdlib tree and never touch the
network; the real-fetch measurements live in the commit message and
`tests/test_embed.py`'s pack-gated end-to-end test.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from pyscriptc import stdlibzip


def _stdlib(tmp_path: Path) -> Path:
    """A small fake stdlib tree with one trimmed package and one module."""
    root = tmp_path / "lib" / "python3.14"
    (root / "json").mkdir(parents=True)
    (root / "json" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (root / "json" / "encoder.py").write_text("from json import decoder\nimport fractions\n", encoding="utf-8")
    (root / "json" / "decoder.py").write_text("value = 2\n", encoding="utf-8")
    (root / "fractions.py").write_text("import numbers\nvalue = 3\n", encoding="utf-8")
    (root / "numbers.py").write_text("value = 4\n", encoding="utf-8")
    (root / "idlelib").mkdir()
    (root / "idlelib" / "__init__.py").write_text("value = 5\n", encoding="utf-8")
    (root / "os.py").write_text("import codecs\nvalue = 6\n", encoding="utf-8")
    return root


def test_safe_trim_drops_the_deny_list_only(tmp_path: Path) -> None:
    root = _stdlib(tmp_path)
    kept = {path.relative_to(root).as_posix() for path in stdlibzip.safe_trim_modules(root)}
    assert "idlelib/__init__.py" not in kept
    assert "os.py" in kept and "json/encoder.py" in kept
    assert set(stdlibzip.SAFE_TRIM) >= {"test", "idlelib", "tkinter", "turtledemo", "ensurepip"}


def test_write_zip_carries_pyc_bodies(tmp_path: Path) -> None:
    root = _stdlib(tmp_path)
    zip_path = stdlibzip.write_zip(stdlibzip.safe_trim_modules(root), root, tmp_path / "python314.zip")
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert "json/__init__.pyc" in names and "fractions.pyc" in names
    assert "idlelib/__init__.pyc" not in names
    assert stdlibzip.zip_modules(zip_path) >= {"json", "json.encoder", "fractions", "os"}


def test_zip_bodies_start_with_the_pyc_magic(tmp_path: Path) -> None:
    root = _stdlib(tmp_path)
    zip_path = stdlibzip.write_zip([root / "os.py"], root, tmp_path / "python314.zip")
    with zipfile.ZipFile(zip_path) as archive:
        body = archive.read("os.pyc")
    assert body[:4] == importlib.util.MAGIC_NUMBER  # the running CPython's


def test_kept_module_names_covers_package_levels(tmp_path: Path) -> None:
    root = _stdlib(tmp_path)
    names = stdlibzip.kept_module_names(stdlibzip.safe_trim_modules(root), root)
    assert {"json", "json.encoder", "json.decoder", "fractions", "os"} <= names
    assert not any(name.startswith("idlelib") for name in names)


def test_manifest_check_passes_when_the_trim_keeps_the_imports(tmp_path: Path) -> None:
    carried = {"json", "json.decoder", "fractions", "os"}
    stdlibzip.manifest_check(carried, ["json", "json.encoder", "fractions", "os"])


def test_manifest_check_fails_closed_with_ps4301(tmp_path: Path) -> None:
    with pytest.raises(stdlibzip.StdlibError, match=r"PS4301.*idlelib"):
        stdlibzip.manifest_check({"json"}, ["json", "idlelib"], "safe")


def test_stdlib_sources_refuses_an_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(stdlibzip.StdlibError, match="embed-stdlib"):
        stdlibzip.stdlib_sources(_stdlib(tmp_path), "profile")


def test_stage_stdlib_replaces_the_tree_and_keeps_dynload(tmp_path: Path) -> None:
    # The staged lib dir already holds the copied tree (stage_bundle copies
    # first, then swaps it for the zip); the pack root next to it is the source.
    pack = _stdlib(tmp_path).parents[1]  # .../lib/python3.14 -> the pack root
    lib_dir = tmp_path / "out" / "lib"
    shutil.copytree(pack / "lib" / "python3.14", lib_dir / "python3.14")
    stdlibzip.stage_stdlib(lib_dir, pack, "3.14", "safe")
    assert (lib_dir / "python314.zip").is_file()
    assert (lib_dir / "python3.14" / "lib-dynload").is_dir()
    assert not (lib_dir / "python3.14" / "os.py").exists()


def test_minimal_modules_is_a_frozen_closure_over_the_host_stdlib(tmp_path: Path) -> None:
    modules = stdlibzip.minimal_modules()
    names = stdlibzip.kept_module_names(modules)
    # The seed travels whole: codecs reach encodings before the first program
    # import, and the allowlist's json is kept with it.
    assert "os" in names and "codecs" in names and "encodings" in names
    assert "encodings.utf_8" in names and "json" in names
    # The deny list stays out of the minimal trim too.
    assert not any(name.split(".")[0] in stdlibzip.SAFE_TRIM for name in names)
    # Frozen: the same tuple answers twice without the interpreter changing.
    assert stdlibzip.kept_module_names(stdlibzip.minimal_modules()) == names


def test_minimal_closure_keeps_imported_stdlib(tmp_path: Path) -> None:
    modules = stdlibzip.minimal_modules()
    names = stdlibzip.kept_module_names(modules)
    stdlibzip.manifest_check(names, ["logging", "datetime", "json", "argparse", "fractions"], "minimal")


@pytest.mark.skipif(
    not (Path.home() / ".cache" / "pyscriptc" / "runtime").is_dir() or not sys.version_info[:2] == (3, 14),
    reason="needs a fetched runtime pack and a 3.14 host",
)
def test_safe_trim_against_the_fetched_pack() -> None:
    from pyscriptc.embed import lock_pack, pack_dir

    root = pack_dir(lock_pack()) / "lib" / "python3.14"
    kept = stdlibzip.kept_module_names(stdlibzip.safe_trim_modules(root))
    for wanted in ("json", "fractions", "logging", "datetime", "argparse", "http", "email", "xml"):
        assert wanted in kept, f"the safe trim dropped {wanted}"
