"""`pyscriptc coverage`, `pyscriptc explain`, and the split annotation codes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

import pyscriptc.coverage as coverage
from pyscriptc.coverage import family_of
from pyscriptc.diagnostics import DIAGNOSTICS
from pyscriptc.lower import lower_source

SOURCE = "\n".join(
    [
        "from typing import Any, Mapping, Optional, Union",
        "",
        "def ok(x: int) -> int:",
        "    return x",
        "",
        "def opt(x: Optional[int]) -> int:",
        "    return 1",
        "",
        "def uni(x: Union[int, str]) -> int:",
        "    return 1",
        "",
        "def alias(x: Mapping[str, int]) -> int:",
        "    return 1",
        "",
        "def dyn(x: Any) -> int:",
        "    return 1",
        "",
        "def fwd(x: 'Later') -> int:",
        "    return 1",
        "",
        "def body(x: int) -> int:",
        "    a = [i for i in range(x)]",
        "    exc: int = x",
        "    try:",
        "        pass",
        "    except ValueError as exc:",
        "        pass",
        "    return x",
        "",
    ]
)


def fence_codes(source: str) -> dict[str, str]:
    module = lower_source(source, "case.py")
    return {fence["function"]: fence["code"] for fence in module["fences"]}


@pytest.mark.parametrize(
    ("annotation", "code"),
    [
        ("int | str", "PS1101"),
        ("Union[int, str]", "PS1101"),
        ("Mapping[str, int]", "PS1103"),
        ("Any", "PS1104"),
        ("object", "PS1104"),
        ("'Later'", "PS1105"),
        ("Unknown", "PS1106"),
    ],
)
def test_annotation_fences_have_specific_codes(annotation: str, code: str) -> None:
    source = f"from typing import Any, Mapping, Optional, Union\n\ndef f(x: {annotation}) -> int:\n    return 1\n"
    assert fence_codes(source) == {"f": code}


def test_coverage_json_reports_each_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "mix.py", SOURCE)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["schemaVersion"] == 2
    families = {entry["family"] for entry in report["fences"]}
    assert {"union-types", "generic-alias", "dynamic-annotation", "forward-reference"} <= families
    assert "except-as" in families
    assert report["summary"]["definitions"] == 7
    assert report["summary"]["nativeDefinitions"] == 2
    assert report["summary"]["fencedDefinitions"] == 5
    by_name = {item["name"]: item for item in report["definitions"]}
    assert "ok" not in by_name
    assert "opt" not in by_name
    assert by_name["body"]["bodyAnalyzed"] is True
    assert by_name["body"]["capability"] == "fenced"
    assert by_name["body"]["rootCause"] == by_name["body"]["fences"][0]
    caps = report["capabilities"]
    assert caps["totals"]["definitions"] == 7
    assert caps["totals"]["native"] == 2
    assert caps["totals"]["fenced"] == 5
    assert caps["totals"]["island"] == 0
    assert caps["byFile"][0]["file"].endswith("mix.py")
    first = report["fences"][0]
    assert {"code", "message", "family", "file", "line", "col", "frame", "hint", "definition"} <= set(first)
    assert report["chain"][0]["definitionsUnblocked"] >= 1


def test_coverage_text_has_frame_and_hint(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", "def f(x: int | str) -> int:\n    return 1\n")
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert "PS1101" in got.stdout
    assert "family: union-types" in got.stdout
    assert "hint:" in got.stdout
    assert "^" in got.stdout
    assert "(0 native)" in got.stdout


def test_coverage_native_definitions_count_and_text(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "def native_one(x: int) -> int:",
            "    return x",
            "",
            "def native_two(y: int) -> int:",
            "    return y + 1",
            "",
            "def fenced_fn(z: 'Unlowered') -> int:",
            "    return 1",
        ]
    )
    path = write_case(tmp_path, "sample.py", source)
    report = coverage.build_report(path)
    assert report["summary"]["definitions"] == 3
    assert report["summary"]["nativeDefinitions"] == 2
    assert report["summary"]["fencedDefinitions"] == 1
    text = coverage.render_text(report)
    assert "in 1 of 3 definitions (2 native)" in text

    # Fully native program (no fences)
    clean_path = write_case(tmp_path, "clean.py", "def a() -> int:\n    return 1\n")
    clean_report = coverage.build_report(clean_path)
    assert clean_report["summary"]["definitions"] == 1
    assert clean_report["summary"]["nativeDefinitions"] == 1
    assert clean_report["summary"]["fencedDefinitions"] == 0
    clean_text = coverage.render_text(clean_report)
    assert "in 0 of 1 definitions (1 native)" in clean_text


def test_coverage_walks_a_directory(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f(x: int | str) -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("def g(x: 'B') -> int:\n    return 1\n", encoding="utf-8")
    got = run_cli("coverage", str(pkg), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["files"] == 3
    assert {"union-types", "forward-reference"} <= {entry["family"] for entry in report["fences"]}


def test_compile_still_fails_closed_on_a_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "opt.py", "def f(x: int | str) -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "opt.ir.json"))
    assert got.returncode == 3
    assert "PS1101" in got.stderr


def test_coverage_walks_a_program_graph(tmp_path: Path) -> None:
    prog = tmp_path / "prog"
    (prog / "pkg").mkdir(parents=True)
    (prog / "__main__.py").write_text("import util\n\nprint(util.f(1))\n", encoding="utf-8")
    (prog / "util.py").write_text("def f(x: int | str) -> int:\n    return 1\n", encoding="utf-8")
    (prog / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (prog / "pkg" / "a.py").write_text("def g(x: 'B') -> int:\n    return 1\n", encoding="utf-8")
    got = run_cli("coverage", str(prog), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["files"] == 4
    assert report["summary"]["definitions"] == 2


def test_coverage_marks_island_imports_from_the_policy_table(tmp_path: Path) -> None:
    script = write_case(
        tmp_path,
        "island_cov.py",
        "\n".join(
            [
                "# /// script",
                "# [tool.pyscriptc.imports]",
                '# requests = "island"',
                "# ///",
                "import requests",
                "",
                "x = 1",
            ]
        ),
    )
    report = coverage.build_report(script)
    assert report["summary"]["requiresLibpython"] is True
    assert report["summary"]["islandImports"] == ["requests"]
    fenced = [e for e in report["fences"] if e["code"] == "PS2002" and "requests" in e["message"]]
    assert fenced
    assert "island lane" in fenced[0]["message"]
    assert "requires libpython" in fenced[0]["message"]
    assert fenced[0]["dependency"]["lane"] == "island"
    assert fenced[0]["dependency"]["policy"] == "requests"


def test_coverage_pulls_island_policy_from_a_pyproject(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pyscriptc.imports]\nrequests = "island"\n',
        encoding="utf-8",
    )
    script = root / "main.py"
    script.write_text("import requests\n\nx = 1\n", encoding="utf-8")
    report = coverage.build_report(script)
    assert report["summary"]["islandImports"] == ["requests"]
    assert any("island lane" in e["message"] for e in report["fences"] if "requests" in e["message"])


def test_coverage_names_a_wildcard_island_policy(tmp_path: Path) -> None:
    script = write_case(
        tmp_path,
        "wildcard_cov.py",
        "\n".join(
            [
                "# /// script",
                "# [tool.pyscriptc.imports]",
                '# "*" = "island"',
                "# ///",
                "import scipy",
                "",
                "x = 1",
            ]
        ),
    )
    report = coverage.build_report(script)
    assert report["summary"]["requiresLibpython"] is True
    assert report["summary"]["islandImports"] == ["scipy"]
    assert any("island lane" in e["message"] for e in report["fences"] if "scipy" in e["message"])


def test_a_cycle_fence_carries_its_own_family(tmp_path: Path) -> None:
    prog = tmp_path / "cyc"
    prog.mkdir()
    (prog / "__main__.py").write_text("import a\n\nprint(a.f())\n", encoding="utf-8")
    (prog / "a.py").write_text("import b\n\n\ndef f() -> int:\n    return 1\n", encoding="utf-8")
    (prog / "b.py").write_text("import a\n\n\ndef g() -> int:\n    return 2\n", encoding="utf-8")
    got = run_cli("coverage", str(prog))
    assert got.returncode == 0, got.stderr
    assert "circular import: a -> b -> a" in got.stdout
    assert "family: import-cycle" in got.stdout
    assert "Break the cycle" in got.stdout


def test_explain_known_and_unknown_code() -> None:
    got = run_cli("explain", "ps1101")
    assert got.returncode == 0
    assert "PS1101" in got.stdout
    assert "Fix:" in got.stdout
    missing = run_cli("explain", "PS9999")
    assert missing.returncode == 2


def test_every_emitted_code_is_registered() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "pyscriptc"
    emitted = set()
    for path in src.glob("*.py"):
        emitted.update(re.findall(r'"(PS\d{4})"', path.read_text(encoding="utf-8")))
    emitted.discard("PS0001")
    assert emitted - set(DIAGNOSTICS) == set()


def test_family_rules() -> None:
    assert family_of("PS2002", "x is not exported by this package") == "export-cascade"
    assert family_of("PS2001", "re.search is not in the lowered subset of re") == "stdlib:re"
    assert family_of("PS1001", "parameter a needs an annotation") == "param-inference"


def test_generator_and_contextmanager_families() -> None:
    assert family_of("PS2002", "a bare yield needs an Optional element type") == "generators"
    assert family_of("PS2002", "yield as an expression (send) is not lowered yet") == "generators"
    assert family_of("PS2002", "a nested def or class in a resumable is not lowered yet") == "generators"
    assert family_of("PS2002", "a generic resumable is not lowered yet") == "generators"
    assert family_of("PS2002", "return inside try or with in a resumable is not lowered yet") == "generators"
    assert family_of("PS2002", "contextlib.contextmanager is not a function") == "contextmanager"
    assert family_of("PS2002", "walrus assignment (:=) is not lowered") == "construct-other"


def test_island_call_emits_requires_libpython_metadata() -> None:
    from pyscriptc.emit.llvm import emit_llvm

    ir_native = lower_source("def main() -> int:\n    return 0\n")
    ll_native = emit_llvm(ir_native)
    assert "@.pyscriptc.requires_libpython" not in ll_native

    ir_island = lower_source("import socket\ndef main() -> str:\n    return socket.gethostname()\n")
    ll_island = emit_llvm(ir_island)
    assert "@.pyscriptc.requires_libpython" in ll_island
    assert 'c"pyscriptc:requires-libpython\\00"' in ll_island
    assert "@llvm.compiler.used" in ll_island

    assert family_of("PS2002", "super is not lowered") == "construct-other"
