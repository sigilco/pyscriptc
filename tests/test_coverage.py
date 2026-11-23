"""`pyscriptc coverage`, `pyscriptc explain`, and the split annotation codes."""

from __future__ import annotations

import ast
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
        "from typing import Any, Awaitable, Optional, Union",
        "",
        "def ok(x: int) -> int:",
        "    return x",
        "",
        "def opt(x: Optional[int]) -> int:",
        "    return 1",
        "",
        "def uni(x: int | str | None) -> int:",
        "    return 1",
        "",
        "def alias(x: Awaitable[int]) -> int:",
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
        ("int | str | None", "PS1102"),
        ("Awaitable[int]", "PS1103"),
        ("Any", "PS1104"),
        ("'Later'", "PS1105"),
        ("Unknown", "PS1106"),
    ],
)
def test_annotation_fences_have_specific_codes(annotation: str, code: str) -> None:
    source = f"from typing import Any, Awaitable, Optional, Union\n\ndef f(x: {annotation}) -> int:\n    return 1\n"
    assert fence_codes(source) == {"f": code}


def test_union_annotation_erases_to_the_first_arm() -> None:
    # Issue #413: a multi-arm union without a `None` arm erases to its
    # first arm, so neither spelling fences any more.
    assert fence_codes("def f(x: int | str) -> int:\n    return 1\n") == {}
    assert fence_codes("from typing import Union\n\ndef f(x: Union[int, str]) -> int:\n    return 1\n") == {}


def test_forward_reference_resolves_at_module_end() -> None:
    # Issue #413: a quoted annotation parses at the signature pass, when
    # every class body is collected, so a name the module defines lowers.
    assert fence_codes("class Node:\n    a: int\n\ndef f() -> 'Node':\n    return Node()\n") == {}
    # One the module never defines keeps the fence, naming the text.
    assert fence_codes("def f(x: 'Later') -> int:\n    return 1\n") == {"f": "PS1105"}


def test_type_alias_binds_the_name_it_spells() -> None:
    # Issue #413: `X: TypeAlias = T` binds the alias name as an erased
    # annotation, so an annotation naming `X` lowers as `T`.
    source = (
        "from typing import TypeAlias\n\nIds: TypeAlias = list[int]\n\ndef f(xs: Ids) -> int:\n    return len(xs)\n"
    )
    assert fence_codes(source) == {}
    # The alias reads lazily, so it may name a class the module defines
    # after the alias statement.
    late = (
        "from typing import TypeAlias\n\n"
        "NodeAlias: TypeAlias = 'Node'\n\n"
        "class Node:\n    a: int\n\n"
        "def f(n: NodeAlias) -> int:\n    return n.a\n"
    )
    assert fence_codes(late) == {}
    # A cycle in the alias chain fences instead of recursing forever.
    cyclic = (
        "from typing import TypeAlias\n\nA: TypeAlias = 'B'\nB: TypeAlias = 'A'\n\ndef f(x: A) -> int:\n    return 1\n"
    )
    assert fence_codes(cyclic) == {"f": "PS1105"}


def test_object_erases_to_the_dynamic_value() -> None:
    # Issue #1226: `object` names a shape, and the tagged dynamic value is
    # that shape's only representation. It erases instead of fencing;
    # `Any` keeps the PS1104 fence (the unresolved slot).
    assert fence_codes("def f(x: object) -> int:\n    return 1\n") == {}
    assert fence_codes("def f(x: Any) -> int:\n    return 1\n") == {"f": "PS1104"}
    module = lower_source("def f(d: dict[str, object]) -> int:\n    return 1\n", "case.py")
    assert module["fences"] == []


def test_coverage_json_reports_each_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "mix.py", SOURCE)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["schemaVersion"] == 3
    families = {entry["family"] for entry in report["fences"]}
    assert {"optional-types", "generic-alias", "dynamic-annotation", "forward-reference"} <= families
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
    path = write_case(tmp_path, "one.py", "def f(x: int | str | None) -> int:\n    return 1\n")
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert "PS1102" in got.stdout
    assert "family: optional-types" in got.stdout
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
    (pkg / "a.py").write_text("def f(x: int | str | None) -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("def g(x: 'B') -> int:\n    return 1\n", encoding="utf-8")
    got = run_cli("coverage", str(pkg), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["files"] == 3
    assert {"optional-types", "forward-reference"} <= {entry["family"] for entry in report["fences"]}


def test_compile_still_fails_closed_on_a_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "opt.py", "def f(x: int | str | None) -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "opt.ir.json"))
    assert got.returncode == 3
    assert "PS1102" in got.stderr


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


def test_coverage_survives_a_real_package_tree(tmp_path: Path) -> None:
    """Issue #730: unparsable files are listed, not fatal; issue #764: unnameable ones are skipped."""
    pkg = tmp_path / "pkg"
    (pkg / "migrations").mkdir(parents=True)
    (pkg / "nspkg").mkdir()
    (pkg / "__init__.py").write_text("from . import a\n", encoding="utf-8")
    (pkg / "a.py").write_text("from . import b\n\n\ndef f() -> int:\n    return b.g()\n", encoding="utf-8")
    (pkg / "b.py").write_text("from . import a\n\n\ndef g() -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "migrations" / "0001_initial.py").write_text("def mig() -> int:\n    return 7\n", encoding="utf-8")
    (pkg / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (pkg / "nspkg" / "mod.py").write_text("def h() -> int:\n    return 3\n", encoding="utf-8")
    got = run_cli("coverage", str(pkg), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["files"] == 5
    assert report["summary"]["filesFailed"] == 1
    assert report["summary"]["filesSkipped"] == 1
    failed = {item["file"].rsplit("/", 1)[-1]: item["reason"] for item in report["failedFiles"]}
    assert "invalid syntax" in failed["broken.py"]
    skipped = {item["file"].rsplit("/", 1)[-1]: item["reason"] for item in report["skippedFiles"]}
    assert skipped["0001_initial.py"] == "not an importable module name"
    analysed = {item["file"].rsplit("/", 1)[-1] for item in report["capabilities"]["byFile"]}
    assert "mod.py" in analysed
    assert "0001_initial.py" not in analysed


def test_coverage_skips_a_pyinstaller_hook_file(tmp_path: Path) -> None:
    """Issue #764: `__pyinstaller/hook-pk.py` is skipped with a note, not a failure."""
    pkg = tmp_path / "pk"
    (pkg / "__pyinstaller").mkdir(parents=True)
    (pkg / "__init__.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "__pyinstaller" / "hook-pk.py").write_text("hook = 'pk'\n", encoding="utf-8")
    got = run_cli("coverage", str(pkg), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["failedFiles"] == []
    assert report["summary"]["filesSkipped"] == 1
    skipped = report["skippedFiles"]
    assert len(skipped) == 1
    assert skipped[0]["file"].endswith("__pyinstaller/hook-pk.py")
    assert skipped[0]["reason"] == "not an importable module name"
    assert "note: skipped pk/__pyinstaller/hook-pk.py" in skipped[0]["note"]

    text = run_cli("coverage", str(pkg))
    assert text.returncode == 0, text.stderr
    assert "could not analyse" not in text.stdout
    assert "note: skipped pk/__pyinstaller/hook-pk.py; not an importable module name" in text.stdout


def test_coverage_text_lists_unanalysed_files(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ok.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    got = run_cli("coverage", str(pkg))
    assert got.returncode == 0, got.stderr
    assert "could not analyse 1 file(s)" in got.stdout
    assert "broken.py: invalid syntax" in got.stdout


def test_coverage_single_file_parse_failure(tmp_path: Path) -> None:
    bad = write_case(tmp_path, "bad.py", "def broken(:\n")
    got = run_cli("coverage", str(bad), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["files"] == 1
    assert report["summary"]["filesFailed"] == 1
    assert "invalid syntax" in report["failedFiles"][0]["reason"]


def test_coverage_survives_a_unit_lowerer_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit that raises inside the lowering is listed, not fatal (issue #730)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ok.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "boom.py").write_text("def g() -> int:\n    return 2\n", encoding="utf-8")

    import pyscriptc.package as package

    real_module = package.Lowerer.module

    def flaky(self: object, tree: ast.Module) -> dict:
        if self.filename.endswith("boom.py"):
            raise RuntimeError("synthetic crash")
        return real_module(self, tree)

    monkeypatch.setattr(package.Lowerer, "module", flaky)
    report = coverage.build_report(pkg)
    assert report["summary"]["files"] == 2
    assert report["summary"]["filesFailed"] == 1
    assert report["failedFiles"] == [
        {"file": str((pkg / "boom.py").resolve()), "reason": "RuntimeError: synthetic crash"}
    ]


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
    # N2 (issue #1308): the island routing is a note, not a fence.
    assert not [e for e in report["fences"] if "requests" in e["message"]]
    notes = [n for n in report["notes"] if "requests" in n["name"]]
    assert notes
    assert "island lane" in notes[0]["message"]


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
    assert not [e for e in report["fences"] if "requests" in e["message"]]
    assert any("requests" in n["name"] for n in report["notes"])


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
    assert not [e for e in report["fences"] if "scipy" in e["message"]]
    assert any("scipy" in n["name"] for n in report["notes"])


def test_bare_isinstance_target_is_a_report_not_a_traceback(tmp_path: Path) -> None:
    """Issue #484: `isinstance(x, dict)` on a non-dict must not crash the report.

    The want record a bare `dict` isinstance target carries is a shape only
    (`{"kind": "dict"}`): when the object's kind differed, `_isinstance_holds`
    fell through to `types_equal` and `type_key` indexed `typ["key"]` on a
    record that has none. The langchain-core tree tripped it. The pin is the
    minimal shape plus the lowered result: a kind mismatch folds to False.
    """
    from pyscriptc.ir import type_key

    assert type_key({"kind": "dict", "bare": True}) == "dict[?]"
    source = "\n".join(
        [
            "def f(x: str) -> bool:",
            "    return isinstance(x, dict)",
            "",
            "def g(items: list[int]) -> bool:",
            "    return isinstance(items, dict)",
            "",
            "def h(d: dict[str, int]) -> bool:",
            "    return isinstance(d, dict)",
        ]
    )
    module = lower_source(source, "case.py")
    assert module["fences"] == []
    path = write_case(tmp_path, "bare_isinstance.py", source)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["fences"] == 0
    assert report["summary"]["nativeDefinitions"] == 3
    emitted = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "bare.ir.json"))
    assert emitted.returncode == 0, emitted.stderr


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
    got = run_cli("explain", "ps1102")
    assert got.returncode == 0
    assert "PS1102" in got.stdout
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


CLASS_BASE_SOURCE = "\n".join(
    [
        "from typing import NamedTuple, TypedDict",
        "from enum import StrEnum",
        "import threading",
        "",
        "class A(NamedTuple):",
        "    x: int",
        "",
        "class B(TypedDict):",
        "    x: int",
        "",
        "class C(StrEnum):",
        "    A = 'a'",
        "",
        "class D(threading.Thread):",
        "    pass",
        "",
        "class E(Unknown):",
        "    pass",
        "",
    ]
)


def test_coverage_census_counts_the_class_bases_by_kind(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bases.py", CLASS_BASE_SOURCE)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    census = report["summary"]["classBases"]
    assert report["summary"]["classBaseTotal"] == 5
    assert census["namedtuple"]["fences"] == 1
    assert census["namedtuple"]["bases"] == ["NamedTuple"]
    assert census["typeddict"]["fences"] == 1
    assert census["typeddict"]["bases"] == ["TypedDict"]
    assert census["enum"]["fences"] == 1
    assert census["enum"]["bases"] == ["StrEnum"]
    assert census["stdlib"]["fences"] == 1
    assert census["stdlib"]["bases"] == ["threading.Thread"]
    assert census["other"]["fences"] == 1
    assert census["other"]["bases"] == ["Unknown"]


def test_coverage_census_names_a_library_base(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from sqlalchemy.orm import DeclarativeBase",
            "",
            "class C(DeclarativeBase):",
            "    pass",
            "",
        ]
    )
    path = write_case(tmp_path, "lib.py", source)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    census = report["summary"]["classBases"]
    assert census["library"]["fences"] == 1
    assert census["library"]["bases"] == ["DeclarativeBase"]
    entry = next(item for item in report["fences"] if item.get("baseKind"))
    assert entry["baseExpression"] == "DeclarativeBase"
    assert entry["baseModule"] == "sqlalchemy.orm"


def test_coverage_census_text_lists_the_kinds(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bases.py", CLASS_BASE_SOURCE)
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert "class bases by kind: 5 fenced" in got.stdout
    assert "namedtuple: 1 (NamedTuple)" in got.stdout
    assert "typeddict: 1 (TypedDict)" in got.stdout
    assert "enum: 1 (StrEnum)" in got.stdout
    assert "stdlib: 1 (threading.Thread)" in got.stdout
    assert "other: 1 (Unknown)" in got.stdout


def test_coverage_census_is_empty_without_a_class_base(tmp_path: Path) -> None:
    path = write_case(tmp_path, "plain.py", "def f(x: int) -> int:\n    return x\n")
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert report["summary"]["classBases"] == {}
    assert report["summary"]["classBaseTotal"] == 0


def test_the_compile_fence_is_unchanged_by_the_census(tmp_path: Path) -> None:
    """The census is a report: the fence itself still refuses the compile."""

    path = write_case(tmp_path, "bases.py", CLASS_BASE_SOURCE)
    got = run_cli("compile", str(path))
    assert got.returncode != 0
    assert "PS2002" in got.stderr


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


_IR_KEYS = frozenset({"kind", "functions", "classes", "body", "span"})
_SRC = Path(__file__).resolve().parents[1] / "src" / "pyscriptc"


def _named_ir_reads(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in _IR_KEYS:
            count += 1
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _IR_KEYS
        ):
            count += 1
    return count


def test_coverage_has_no_raw_dict_ir_reads() -> None:
    assert _named_ir_reads(_SRC / "coverage.py") == 0
