"""The per-import classifier: pure, curated, native-backed and stdlib.

Every case here is offline. The data file of known native-backed packages is
the only source of truth for the curated and native-backed verdicts, and the
wheel heuristic reads local metadata only.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from tests.test_gates import run_cli

from pyscriptc.imports import (
    KIND_CURATED,
    KIND_NATIVE,
    KIND_PURE,
    KIND_STDLIB,
    LADDER_ISLAND,
    LADDER_NATIVE,
    LANE_EXTERN,
    LANE_ISLAND,
    LANE_NATIVE,
    LANE_PACK,
    build_report,
    classify,
    collect_imports,
    curated_packs,
    export_reach,
    island_reach,
    island_statements,
    native_packages,
    pack_candidates,
    render_text,
)
from pyscriptc.stdlib_queue import QUEUE


def write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The data file


def test_data_file_lists_native_backed_packages() -> None:
    packages = native_packages()
    assert packages
    dists = {package["dist"] for package in packages}
    assert {"numpy", "orjson", "cryptography"} <= dists
    for package in packages:
        assert package["dist"]
        assert package["imports"]


def test_data_file_is_valid_json_with_a_stable_schema() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "pyscriptc"
    payload = json.loads((root / "native_packages.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert isinstance(payload["packages"], list)
    for entry in payload["packages"]:
        assert {"dist", "imports", "pack"} <= set(entry)


def test_curated_packs_cover_the_planned_candidates() -> None:
    packs = curated_packs()
    assert packs["orjson"] == "pyscriptc-orjson"
    assert "numpy" not in packs
    assert any(pack.startswith("pyscriptc-") for pack in packs.values())


# --------------------------------------------------------------------------
# Classification


def test_stdlib_import_is_classified_as_stdlib() -> None:
    verdict = classify("json")
    assert verdict["kind"] == KIND_STDLIB
    assert verdict["lane"] == LANE_NATIVE


def test_lowered_stdlib_is_ready_to_compile() -> None:
    verdict = classify("math")
    assert verdict["kind"] == KIND_STDLIB
    assert verdict["lane"] == LANE_NATIVE
    assert "lowered" in verdict["reason"]


def test_stdlib_outside_the_table_points_at_extern() -> None:
    verdict = classify("smtplib")
    assert verdict["kind"] == KIND_STDLIB
    assert verdict["lane"] == LANE_EXTERN
    assert "closed import table" in verdict["reason"]


def test_typing_is_an_annotation_only_module() -> None:
    verdict = classify("typing")
    assert verdict["kind"] == KIND_STDLIB
    assert verdict["lane"] == LANE_NATIVE
    assert "annotation-only" in verdict["reason"]


def test_native_backed_import_is_not_compiled_from_source() -> None:
    verdict = classify("numpy")
    assert verdict["kind"] == KIND_NATIVE
    assert verdict["lane"] == LANE_EXTERN
    assert verdict["distribution"] == "numpy"
    assert verdict["pack"] == ""


def test_curated_import_points_at_its_pack() -> None:
    verdict = classify("orjson")
    assert verdict["kind"] == KIND_CURATED
    assert verdict["lane"] == LANE_PACK
    assert verdict["pack"] == "pyscriptc-orjson"


def test_curated_import_matches_a_submodule() -> None:
    verdict = classify("cryptography.fernet")
    assert verdict["kind"] == KIND_CURATED
    assert verdict["lane"] == LANE_PACK
    assert verdict["pack"] == "pyscriptc-cryptography"


def test_unknown_third_party_import_is_pure_python() -> None:
    verdict = classify("requests")
    assert verdict["kind"] == KIND_PURE
    assert verdict["lane"] == LANE_NATIVE
    assert verdict["source"] == "heuristic"


def test_every_stdlib_queue_entry_classifies_as_stdlib() -> None:
    for name in QUEUE:
        assert classify(name)["kind"] == KIND_STDLIB


# --------------------------------------------------------------------------
# Import collection


def test_collect_imports_finds_every_statement(tmp_path: Path) -> None:
    path = write(
        tmp_path / "a.py",
        "import os\nimport json as j\nfrom pathlib import Path\nfrom . import sibling\n",
    )
    names = [site["name"] for site in collect_imports(path)]
    assert names == ["os", "json", "pathlib", ""]


def test_collect_imports_reads_the_module_not_the_bound_name(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "from cryptography.fernet import Fernet\n")
    assert [site["name"] for site in collect_imports(path)] == ["cryptography.fernet"]


def test_collect_imports_keeps_the_source_line(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "\nimport os\n")
    site = collect_imports(path)[0]
    assert site["line"] == 2
    assert site["statement"] == "import os"


def test_collect_imports_skips_a_file_it_cannot_parse(tmp_path: Path) -> None:
    write(tmp_path / "bad.py", "def f(:\n")
    write(tmp_path / "ok.py", "import os\n")
    names = [site["name"] for site in collect_imports(tmp_path)]
    assert names == ["os"]


# --------------------------------------------------------------------------
# The report


def test_report_groups_a_tree_by_import_root(tmp_path: Path) -> None:
    pkg = tmp_path / "tool"
    write(pkg / "__init__.py", "import os\n")
    write(pkg / "core.py", "import json\nimport orjson\nfrom . import helper\n")
    report = build_report(pkg)
    names = {item["name"] for item in report["imports"]}
    assert {"os", "json", "orjson"} <= names
    assert report["summary"]["byLane"][LANE_PACK] == 1


def test_report_counts_statements_and_imports(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import os\nimport os\nimport json\n")
    report = build_report(path)
    assert report["summary"]["statements"] == 3
    assert report["summary"]["imports"] == 2


def test_report_records_the_site_of_every_import(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import numpy\n")
    report = build_report(path)
    site = report["imports"][0]["sites"][0]
    assert site["line"] == 1
    assert site["statement"] == "import numpy"
    assert site["file"].endswith("a.py")


def test_report_groups_a_repeated_import_under_one_entry(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import numpy\nimport numpy.fft\n")
    report = build_report(path)
    assert len(report["imports"]) == 1
    assert len(report["imports"][0]["sites"]) == 2


def test_relative_imports_stay_inside_the_tree(tmp_path: Path) -> None:
    pkg = tmp_path / "tool"
    write(pkg / "__init__.py", "")
    write(pkg / "core.py", "from . import helper\n")
    report = build_report(pkg)
    relative = [item for item in report["imports"] if item["source"] == "relative"]
    assert relative
    assert relative[0]["lane"] == LANE_NATIVE


def test_report_schema_is_stable(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import os\n")
    report = build_report(path)
    assert report["schemaVersion"] == 2
    assert set(report) == {"schemaVersion", "target", "summary", "imports"}
    assert set(report["imports"][0]) == {
        "name",
        "kind",
        "lane",
        "ladder",
        "requiresLibpython",
        "reason",
        "distribution",
        "pack",
        "source",
        "sites",
        "capability",
    }
    assert set(report["imports"][0]["capability"]) == {
        "routing",
        "codes",
        "mechanisms",
        "files",
        "requires",
    }


def test_without_a_policy_no_import_reaches_the_island(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import numpy\nimport json\n")
    report = build_report(path)
    assert all(item["lane"] != LANE_ISLAND for item in report["imports"])
    assert report["summary"]["requiresLibpython"] is False
    assert report["summary"]["islandImports"] == []
    assert LADDER_ISLAND not in report["summary"]["byLadder"]


def test_the_policy_is_the_only_route_to_the_island(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import numpy\n")
    report = build_report(path, policy={"numpy": "island"})
    item = report["imports"][0]
    assert item["lane"] == LANE_ISLAND
    assert item["ladder"] == LADDER_ISLAND
    assert item["requiresLibpython"] is True
    assert "requires libpython" in item["reason"]
    assert report["summary"]["requiresLibpython"] is True
    assert report["summary"]["islandImports"] == ["numpy"]
    assert report["summary"]["byLadder"] == {LADDER_ISLAND: 1}


def test_the_policy_matches_a_full_module_name_before_its_root(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import cryptography.fernet\n")
    report = build_report(path, policy={"cryptography.fernet": "island"})
    assert report["imports"][0]["lane"] == LANE_ISLAND


def test_a_wildcard_policy_routes_every_unlisted_import_to_the_island(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\nimport json\n")
    report = build_report(path, policy={"json": "compile", "*": "island"})
    lanes = {item["name"]: item["lane"] for item in report["imports"]}
    assert lanes == {"json": LANE_NATIVE, "orjson": LANE_ISLAND}


def test_a_policy_entry_overrides_the_classifier_verdict(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\n")
    report = build_report(path, policy={"orjson": "island"})
    item = report["imports"][0]
    assert item["lane"] == LANE_ISLAND
    assert item["kind"] == KIND_CURATED


def test_a_native_import_keeps_the_native_rung(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import json\n")
    assert build_report(path)["imports"][0]["ladder"] == LADDER_NATIVE


def test_reach_is_island_statements_over_all_import_statements(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import json\nimport requests\nimport requests.sessions\n")
    report = build_report(path, policy={"requests": "island"})
    assert island_statements(report) == 2
    assert island_reach(report) == 0.6667
    assert report["summary"]["islandStatements"] == 2
    assert report["summary"]["reach"] == 0.6667


def test_reach_of_a_program_without_imports_is_zero(tmp_path: Path) -> None:
    report = build_report(write(tmp_path / "a.py", "x = 1\n"))
    assert island_reach(report) == 0.0


def test_export_reach_appends_to_the_boxed_cost_series(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import json\nimport requests\n")
    first = build_report(write(tmp_path / "b.py", "import json\nimport requests\n"), policy={"requests": "island"})
    target = tmp_path / "reach.json"
    assert export_reach(first, target) == {"boxed_only_reach": [0.5], "unit": "share"}
    second = build_report(write(tmp_path / "c.py", "import requests\n"), policy={"requests": "island"})
    assert export_reach(second, target)["boxed_only_reach"] == [0.5, 1.0]
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == {"boxed_only_reach": [0.5, 1.0], "unit": "share"}
    assert path.is_file()


def test_export_reach_refuses_a_file_that_is_not_a_reach_document(tmp_path: Path) -> None:
    target = tmp_path / "reach.json"
    target.write_text("[]", encoding="utf-8")
    report = build_report(write(tmp_path / "a.py", "import json\n"))
    with pytest.raises(ValueError, match="JSON object"):
        export_reach(report, target)


def test_render_text_marks_the_island_and_its_label(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import requests\n")
    text = render_text(build_report(path, policy={"requests": "island"}))
    assert "island (requires libpython)" in text
    assert "island: requires libpython" in text


def test_render_text_names_the_lane_of_every_import(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\nimport numpy\n")
    text = render_text(build_report(path))
    assert "orjson" in text
    assert "lane pack" in text
    assert "lane extern" in text


def test_report_of_an_empty_tree_has_no_imports(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "x = 1\n")
    report = build_report(path)
    assert report["summary"]["imports"] == 0
    assert report["summary"]["byLane"] == {}


def test_a_missing_target_does_not_raise(tmp_path: Path) -> None:
    report = build_report(tmp_path / "nope")
    assert report["summary"]["imports"] == 0


def test_pack_candidates_are_ordered_by_use(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\nimport orjson\nimport cryptography.fernet\n")
    found = pack_candidates(build_report(path))
    assert found[0]["import"] == "orjson"
    assert found[0]["statements"] == 2
    assert found[0]["pack"] == "pyscriptc-orjson"


# --------------------------------------------------------------------------
# The wheel heuristic


def _wheel(tmp_path: Path, name: str, dist: str, files: list[str]) -> Path:
    path = tmp_path / f"{name}-{dist}.whl"
    record = "".join(f"{item},,\n" for item in files)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{dist}.dist-info/RECORD", record)
        for item in files:
            archive.writestr(item, "")
    return path


def test_wheel_record_with_an_extension_is_native_backed() -> None:
    from pyscriptc.imports import _wheel_is_native

    files = ["pkg/__init__.py", "pkg/_core.cpython-314-x86_64-linux-gnu.so"]
    assert _wheel_is_native(files) is True


def test_wheel_record_without_an_extension_is_pure() -> None:
    from pyscriptc.imports import _wheel_is_native

    assert _wheel_is_native(["pkg/__init__.py", "pkg/core.py"]) is False


def test_wheel_record_reader_lists_the_files(tmp_path: Path) -> None:
    from pyscriptc.imports import _read_wheel_record

    path = _wheel(tmp_path, "demo", "demo", ["demo/__init__.py", "demo/_core.so"])
    files = _read_wheel_record(path)
    assert files is not None
    assert "demo/_core.so" in files


def test_wheel_record_reader_rejects_a_file_that_is_not_a_zip(tmp_path: Path) -> None:
    from pyscriptc.imports import _read_wheel_record

    path = tmp_path / "not-a-wheel.whl"
    path.write_text("plain text", encoding="utf-8")
    assert _read_wheel_record(path) is None


def test_distribution_is_native_is_unknown_when_not_installed() -> None:
    from pyscriptc.imports import distribution_is_native

    native, reason = distribution_is_native("a-package-that-is-not-installed")
    assert native is None
    assert "not installed" in reason


@pytest.mark.parametrize("dist", ["numpy", "pyyaml", "psutil"])
def test_a_known_native_distribution_reads_as_native(dist: str) -> None:
    verdict = classify("x", dist)
    assert verdict["kind"] == KIND_NATIVE
    assert verdict["lane"] == LANE_EXTERN
    assert verdict["source"] == "native-packages"


def test_a_distribution_hint_without_a_table_entry_uses_the_metadata() -> None:
    verdict = classify("x", "a-package-that-is-not-installed")
    assert verdict["kind"] == KIND_PURE
    assert verdict["lane"] == LANE_NATIVE


# --------------------------------------------------------------------------
# The CLI


def test_imports_cli_prints_the_lane_table(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\nimport numpy\nimport json\n")
    got = run_cli("imports", str(path))
    assert got.returncode == 0, got.stderr
    assert "orjson" in got.stdout
    assert "lane pack" in got.stdout
    assert "native-backed" in got.stdout


def test_imports_cli_json_matches_build_report(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", "import orjson\nimport json\n")
    got = run_cli("imports", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["schemaVersion"] == 2
    assert report["summary"]["imports"] == 2
    assert report["summary"]["byLane"][LANE_PACK] == 1


def test_imports_cli_walks_a_package_tree(tmp_path: Path) -> None:
    pkg = tmp_path / "tool"
    write(pkg / "__init__.py", "")
    write(pkg / "core.py", "import orjson\n")
    write(pkg / "extra.py", "import numpy\n")
    got = run_cli("imports", str(pkg), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["imports"] == 2


def test_imports_cli_exports_reach(tmp_path: Path) -> None:
    path = write(
        tmp_path / "a.py",
        "# /// script\n# [tool.pyscriptc.imports]\n# requests = 'island'\n# ///\nimport requests\nimport json\n",
    )
    reach_file = tmp_path / "reach.json"
    got = run_cli("imports", str(path), "--reach", str(reach_file))
    assert got.returncode == 0, got.stderr
    assert "island reach 0.5000 appended to" in got.stderr
    payload = json.loads(reach_file.read_text(encoding="utf-8"))
    assert payload == {"boxed_only_reach": [0.5], "unit": "share"}
