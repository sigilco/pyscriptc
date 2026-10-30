"""The catalog memo (issue #424): a unit whose imports did not change is not re-lowered."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyscriptc.package import _catalog, _catalog_once


def _write(root: Path, name: str, source: str) -> Path:
    path = root / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def _package(root: Path, sources: dict[str, str]) -> list[tuple[str, Path, bool]]:
    """A one-package tree: `pkg.core`, `pkg.app` and `pkg` itself."""

    units: list[tuple[str, Path, bool]] = []
    for name, source in sources.items():
        path = _write(root, name, source)
        units.append(("pkg" if name == "__init__" else f"pkg.{name}", path, name == "__init__"))
    return units


def _catalog_plain(units: list[tuple[str, Path, bool]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The fixpoint without the memo, the way `_catalog` ran before."""

    exports: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    for _ in range(len(units) + 1):
        nxt, nxt_failed, _memo = _catalog_once(units, exports, program=False)
        if nxt == exports:
            break
        exports, failed = nxt, nxt_failed
    return exports, failed


def test_catalog_reuses_a_unit_whose_imports_did_not_change(tmp_path: Path) -> None:
    """A unit with no imports keeps its exports across the fixpoint rounds."""

    units = _package(
        tmp_path,
        {
            "__init__": "",
            "core": "def keep(value: int) -> int:\n    return value\n",
            "app": "from .core import keep\n\ndef show(value: int) -> int:\n    return keep(value)\n",
        },
    )

    exports, failed = _catalog(units, program=False)

    assert not failed
    assert exports["pkg.core"]["keep"]["kind"] == "func"
    assert exports["pkg.app"]["show"]["kind"] == "func"


def test_the_memo_tracks_the_export_entries_a_unit_read(tmp_path: Path) -> None:
    """The memo's dirty tracking names the export entry the unit read."""

    units = _package(
        tmp_path,
        {
            "__init__": "",
            "core": "def where() -> str:\n    return 'here'\n",
            "app": "from .core import where\n\ndef show() -> str:\n    return where()\n",
        },
    )

    exports, failed, memo = _catalog_once(units, {}, program=False)

    assert not failed
    assert memo["pkg.app"].reads == {("pkg.core", "where"): None}


def test_an_unchanged_table_reuses_the_memoized_result(tmp_path: Path) -> None:
    """With the same table, the memoized round reuses the unit's exports as they are."""

    units = _package(
        tmp_path,
        {
            "__init__": "",
            "core": "def where() -> str:\n    return 'here'\n",
            "app": "from .core import where\n\ndef show() -> str:\n    return where()\n",
        },
    )

    exports, _failed, memo = _catalog_once(units, {}, program=False)
    _second, _failed2, memo2 = _catalog_once(units, exports, program=False, memo=memo)
    # The table did not change in the third round, so the unit is not
    # lowered again: the memo hands back the very same exports.
    third, _failed3, _memo3 = _catalog_once(units, exports, program=False, memo=memo2)

    assert third["pkg.app"] is memo2["pkg.app"].exports
    assert third["pkg.core"] is memo2["pkg.core"].exports


def test_a_changed_export_entry_invalidates_the_reader(tmp_path: Path) -> None:
    """When the entry the unit read changes, the unit lowers again."""

    units = _package(
        tmp_path,
        {
            "__init__": "",
            "core": "def where() -> str:\n    return 'here'\n",
            "app": "from .core import where\n\ndef show() -> str:\n    return where()\n",
        },
    )

    exports, _failed, memo = _catalog_once(units, {}, program=False)
    _same, _failed2, memo2 = _catalog_once(units, exports, program=False, memo=memo)

    # The entry `pkg.app` read changed, so the memo no longer matches.
    changed = dict(exports)
    changed["pkg.core"] = {**exports["pkg.core"], "where": {"kind": "const", "value": "elsewhere"}}
    from pyscriptc.package import _catalog_entries_unchanged

    assert not _catalog_entries_unchanged(memo2["pkg.app"], changed)
    assert _catalog_entries_unchanged(memo2["pkg.core"], changed)


def test_the_memo_does_not_change_the_fixpoint_result(tmp_path: Path) -> None:
    """The memo is an optimization: the fixpoint result must not depend on it."""

    units = _package(
        tmp_path,
        {
            "__init__": "",
            "core": "def where() -> str:\n    return 'here'\n",
            "app": "from .core import where\n\ndef show() -> str:\n    return where()\n",
            "other": "from .core import where\n\ndef also() -> str:\n    return where()\n",
        },
    )

    memoized, memo_failed = _catalog(units, program=False)
    plain, plain_failed = _catalog_plain(units)

    assert memoized == plain
    assert memo_failed == plain_failed
