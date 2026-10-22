"""`pyscriptc coverage`, `pyscriptc explain`, and the split annotation codes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

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
    assert report["schemaVersion"] == 1
    families = {entry["family"] for entry in report["fences"]}
    assert {"union-types", "generic-alias", "dynamic-annotation", "forward-reference"} <= families
    assert "except-as" in families
    assert report["summary"]["definitions"] == 7
    assert report["summary"]["fencedDefinitions"] == 5
    by_name = {item["name"]: item for item in report["definitions"]}
    assert "ok" not in by_name
    assert "opt" not in by_name
    assert by_name["body"]["bodyAnalyzed"] is True
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
    assert {"union-types", "forward-reference"} <= {entry["family"] for entry in report["fences"]}


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
