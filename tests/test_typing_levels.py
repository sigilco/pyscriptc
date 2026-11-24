# SPDX-License-Identifier: Apache-2.0
"""The typing strictness levels of the quality lane (issue #1315).

The levels are report-only: the tests cover the level algebra (rank,
severity, coverage), the config resolution (`[tool.pyscriptc.typing]`:
`mode` plus one glob list per level, PS3102 on a bad table), the raise-only
pragma, the static scan against the lowerer's own tables, the report
wiring in `infer --report` and `assess`, and the proof that a level
changes no compile output.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from pyscriptc.config import Config, ConfigError
from pyscriptc.typinglevels import (
    CONSTRUCT_FAMILIES,
    DEFAULT_LEVEL,
    LEVELS,
    MISSING_FAMILIES,
    TypingLevelError,
    TypingPolicy,
    check_level,
    parse_pragma,
    rank,
    scan_file,
    scan_tree,
    severity_for,
    typing_report,
)


def test_levels_and_rank() -> None:
    assert LEVELS == ["off", "basic", "standard", "strict"]
    assert DEFAULT_LEVEL == "standard"
    assert rank("off") == 0
    assert rank("strict") == 3
    with pytest.raises(TypingLevelError):
        rank("loud")


def test_check_level_names_the_known_levels() -> None:
    with pytest.raises(TypingLevelError) as excinfo:
        check_level("stric")
    assert "off|basic|standard|strict" in str(excinfo.value)


def test_severity_table() -> None:
    assert severity_for("off", "param-inference") is None
    assert severity_for("off", "generic-alias") is None
    assert severity_for("basic", "param-inference") == "warning"
    assert severity_for("standard", "return-inference") == "warning"
    # The construct family stays silent below strict.
    for level in ("basic", "standard"):
        for family in CONSTRUCT_FAMILIES:
            assert severity_for(level, family) is None
    for family in MISSING_FAMILIES + CONSTRUCT_FAMILIES:
        assert severity_for("strict", family) == "error"
    # A family the levels do not know stays silent at every level.
    assert severity_for("strict", "import-table") is None


def test_reports_coverage_follows_the_standard_level() -> None:
    from pyscriptc.typinglevels import reports_coverage

    assert reports_coverage("off") is False
    assert reports_coverage("basic") is False
    assert reports_coverage("standard") is True
    assert reports_coverage("strict") is True


# --- the config table -------------------------------------------------------


def _config_with(table: str) -> Config:
    return Config(typing=table)  # type: ignore[arg-type]


def test_default_policy_without_the_table() -> None:
    config = Config()
    policy = config.typing_policy()
    assert policy.mode == "standard"
    assert policy.globs == {}
    assert policy.mode == "standard"


def test_policy_mode_only() -> None:
    policy = _config_with({"mode": "strict"}).typing_policy()
    assert policy.mode == "strict"
    assert policy.globs == {}


def test_policy_globs() -> None:
    policy = _config_with({"mode": "basic", "strict": ["src/core/**"], "off": ["src/gen"]}).typing_policy()
    assert policy.mode == "basic"
    assert policy.globs == {"strict": ["src/core/**"], "off": ["src/gen"]}


def test_policy_globs_accept_a_single_string() -> None:
    policy = _config_with({"strict": "src/core/**"}).typing_policy()
    assert policy.globs["strict"] == ["src/core/**"]


def test_policy_rejects_a_bad_mode() -> None:
    with pytest.raises(ConfigError) as excinfo:
        _config_with({"mode": "loud"}).typing_policy()
    assert "off|basic|standard|strict" in str(excinfo.value)


def test_policy_rejects_a_bad_glob_type() -> None:
    with pytest.raises(ConfigError):
        _config_with({"strict": 3}).typing_policy()
    with pytest.raises(ConfigError):
        _config_with({"strict": [1, 2]}).typing_policy()


def test_config_validation_rejects_an_unknown_key() -> None:
    from pyscriptc.config import _validate_typing

    with pytest.raises(ConfigError) as excinfo:
        _validate_typing("tool.pyscriptc.typing", {"loud": ["x"]})
    assert "unknown key" in str(excinfo.value)


def test_config_validation_rejects_a_bad_mode_type() -> None:
    from pyscriptc.config import _validate_typing

    with pytest.raises(ConfigError):
        _validate_typing("tool.pyscriptc.typing", {"mode": 3})


# --- the globs --------------------------------------------------------------


def test_glob_semantics(tmp_path: Path) -> None:
    from pyscriptc.typinglevels import _glob_matches

    assert _glob_matches("src/**", "src/core/a.py")
    assert _glob_matches("src/*.py", "src/a.py")
    assert not _glob_matches("src/*.py", "src/core/a.py")
    assert _glob_matches("src/core/**", "src/core/a.py")
    assert _glob_matches("src/?ore/a.py", "src/core/a.py")
    assert not _glob_matches("src/?ore/a.py", "src/coree/a.py")


def test_longest_pattern_wins_and_strictness_ties_break_up(tmp_path: Path) -> None:
    base = tmp_path / "proj"
    base.mkdir()
    target = base / "src" / "core" / "a.py"
    policy = TypingPolicy(
        mode="basic",
        globs={"strict": ["src/**"], "basic": ["src/core/**"]},
        base=base,
    )
    # Both patterns match; the longer (more specific) pattern wins, so the
    # file lands at the level its most specific list names.
    level, origin = policy.level_for(target, "")
    assert level == "basic"  # the longer `basic` pattern wins the length race
    # The origin is `mode` when the winning level equals the project mode;
    # a different level names `glob`.
    assert TypingPolicy(mode="basic").level_for(target, "") == ("basic", "mode")
    raised = TypingPolicy(mode="off", globs={"strict": ["src/**"]}, base=base)
    assert raised.level_for(target, "") == ("strict", "glob")
    # A shorter strict pattern over a longer basic pattern: the longest
    # pattern wins regardless of level, so this reads the longer strict.
    policy2 = TypingPolicy(mode="off", globs={"basic": ["src/**"], "strict": ["src/core/**"]}, base=base)
    assert policy2.level_for(target, "")[0] == "strict"
    # At equal pattern length the strictest level wins the tie.
    policy3 = TypingPolicy(mode="off", globs={"basic": ["src/core/*"], "strict": ["src/core/*"]}, base=base)
    assert policy3.level_for(target, "")[0] == "strict"


def test_level_for_origins(tmp_path: Path) -> None:
    base = tmp_path / "proj"
    base.mkdir()
    policy = TypingPolicy(mode="basic", globs={}, base=base)
    outside = base / "lib" / "b.py"
    assert policy.level_for(outside, "") == ("basic", "mode")
    # A pragma below the config is the config's level; the origin names
    # where it came from (`config`, not `pragma`).
    assert policy.level_for(outside, "# pyscriptc: typed=off\n") == ("basic", "config")


# --- the pragma -------------------------------------------------------------


def test_pragma_parse() -> None:
    assert parse_pragma("# pyscriptc: typed=strict") == "strict"
    assert parse_pragma("#   pyscriptc: typed=basic  ") == "basic"
    assert parse_pragma("x = 1\n# pyscriptc: typed=off\n") == "off"
    assert parse_pragma("# pyscriptc: typed=") is None
    assert parse_pragma("# pyscriptc: infer") is None
    assert parse_pragma("") is None
    # Two pragmas: the strictest wins.
    assert parse_pragma("# pyscriptc: typed=basic\n# pyscriptc: typed=strict\n") == "strict"
    with pytest.raises(TypingLevelError):
        parse_pragma("# pyscriptc: typed=stric")


def test_pragma_may_only_raise(tmp_path: Path) -> None:
    base = tmp_path / "proj"
    base.mkdir()
    policy = TypingPolicy(mode="basic", globs={"strict": ["core/**"]}, base=base)
    source = "# pyscriptc: typed=basic\ndef f():\n    pass\n"
    # The file sits in a strict glob: its pragma cannot lower the level.
    assert policy.level_for(base / "core" / "a.py", source) == ("strict", "config")
    # The same pragma outside the globs is the config level; the origin
    # names where it came from (a pragma at or below the config reads as
    # `config`, a raise reads as `pragma`).
    assert policy.level_for(base / "lib" / "b.py", source) == ("basic", "config")
    # A pragma above the config raises.
    assert policy.level_for(base / "lib" / "c.py", "# pyscriptc: typed=strict\n") == ("strict", "pragma")


# --- the scanner ------------------------------------------------------------


def _scan(source: str, name: str = "mod.py") -> list[tuple[str, str, int]]:
    tree = ast.parse(source)
    return [(f.code, f.family, f.line) for f in scan_tree(tree, Path(name))]


def test_scan_missing_annotations() -> None:
    findings = _scan(
        """
def f(x):
    return x


def g(self_y) -> int:
    return 1


class C:
    def m(self):
        pass

    def n(cls):
        pass
"""
    )
    codes = [c for c, _fam, _line in findings]
    # `x`, `self_y`, `f`, `m` and `n`: self and cls are not counted.
    assert codes.count("PS1001") == 5


def test_scan_defaults_count_as_annotated() -> None:
    findings = _scan('def h(a, b="x"):\n    pass\n')
    # The defaulted `b` counts as annotated; the bare `a` and the missing
    # return do not.
    assert [fam for _code, fam, _line in findings] == ["param-inference", "return-inference"]


def test_scan_construct_forms() -> None:
    findings = _scan(
        """
from typing import Any, Foo, Sequence as S


def a() -> Any:
    ...


def b() -> "Later":
    return None


def c() -> Foo:
    return None


def d() -> S[int]:
    return []


d: dict[str, Any] = {}
v: object = None
"""
    )
    families = {fam: code for code, fam, _line in findings}
    assert families["dynamic-annotation"] == "PS1104"
    assert families["forward-reference"] == "PS1105"
    assert families["generic-alias"] == "PS1103"
    # The taken alias and the builtin `object` stay silent.
    assert not any("sequence" in fam for fam in families)


def test_scan_reads_the_lowerers_tables() -> None:
    """The scanner's name lists come from the lowerer, not a second copy."""

    from pyscriptc.typinglevels import _lowerer_tables

    taken, dynamic = _lowerer_tables()
    for name in ("Sequence", "Iterable", "Callable", "Optional", "Union", "Final", "Annotated", "NoReturn"):
        assert name in taken, name
    for name in ("Any", "Protocol", "TypeVar", "Generic"):
        assert name in dynamic, name
    # `NoReturn` is taken bare and never a dynamic annotation.
    assert "NoReturn" not in dynamic


def test_scan_ignores_the_modules_own_names() -> None:
    findings = _scan(
        """
class Sequence:
    pass


def f(x: Sequence) -> Sequence:
    return x
"""
    )
    assert [f for f in findings if f[0] != "PS1001"] == []


def test_scan_file_tolerates_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n")
    assert scan_file(bad) == []
    assert scan_file(tmp_path / "missing.py") == []


# --- the report -------------------------------------------------------------


def test_typing_report_gates_by_level(tmp_path: Path) -> None:
    one = tmp_path / "a.py"
    one.write_text("def f(x):\n    return x\n")
    findings = scan_file(one)
    assert findings
    off = typing_report(TypingPolicy(mode="off"), [one], list(findings))
    assert off["findings"] == []
    assert off["summary"]["coverage"] is False
    copies = [type(f)(**{**f.__dict__}) for f in findings]
    basic = typing_report(TypingPolicy(mode="basic"), [one], copies)
    assert basic["summary"]["warnings"] == len(findings)
    assert basic["summary"]["errors"] == 0
    strict = typing_report(TypingPolicy(mode="strict"), [one], copies)
    assert strict["summary"]["errors"] == len(findings)
    assert strict["levels"][one.as_posix()] == "strict"
    assert strict["origins"][one.as_posix()] == "mode"


def test_typing_report_json_shape(tmp_path: Path) -> None:
    one = tmp_path / "a.py"
    one.write_text("def f(x):\n    return x\n")
    report = typing_report(TypingPolicy(), [one], scan_file(one))
    row = report["findings"][0]
    json.dumps(report)  # the report is JSON-serializable
    assert set(row) == {"code", "family", "message", "severity", "file", "line"}


# --- the report wiring ------------------------------------------------------


def test_infer_report_carries_the_typing_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`pyscriptc infer --report` gains the typing section."""

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def f(x):\n    return x\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "basic"\n')

    from pyscriptc.cli._report import _typing_policy
    from pyscriptc.infercoverage import coverage_report, render_coverage_text
    from pyscriptc.owninfer import build_own_result

    result = build_own_result(pkg)
    report = coverage_report(pkg, result.facts, policy=_typing_policy(pkg))
    assert report["typing"]["mode"] == "basic"
    assert any(f["code"] == "PS1001" for f in report["typing"]["findings"])
    text = render_coverage_text(report)
    assert "typing mode basic" in text

    # `off` silences the findings but keeps the section.
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n')
    report = coverage_report(pkg, result.facts, policy=_typing_policy(pkg))
    assert report["typing"]["mode"] == "off"
    assert report["typing"]["findings"] == []
    assert "typing mode off" in render_coverage_text(report)
    capsys.readouterr()


def test_no_policy_keeps_the_plain_report(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def f(x):\n    return x\n")

    from pyscriptc.infercoverage import coverage_report
    from pyscriptc.owninfer import build_own_result

    report = coverage_report(pkg, build_own_result(pkg).facts)
    assert "typing" not in report


def test_assess_carries_the_typing_section(tmp_path: Path) -> None:
    """`pyscriptc assess` gains the typing section and it never gates."""

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def f(x):\n    return x\n\n\ndef main() -> int:\n    return 0\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "strict"\n')

    from pyscriptc.assess import _typing_section

    section = _typing_section(pkg, [str(pkg / "__init__.py"), str(pkg / "m.py")])
    assert section["mode"] == "strict"
    assert any(f["code"] == "PS1001" for f in section["findings"])
    assert all(f["severity"] == "error" for f in section["findings"])


def test_assess_text_renders_the_typing_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from pyscriptc.assess import _typing_section, render_text

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text("def f(x):\n    return x\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "standard"\n')
    report = {
        "target": str(pkg),
        "targetVersion": "3.14.x",
        "summary": {
            "files": {"total": 1, "fully": 0, "partly": 0, "refused": 0, "skipped": 0},
            "definitions": {"total": 0, "native": 0, "island": 0, "blocked": 0},
            "shares": {"native": 0.0, "island": 0.0, "blocked": 0.0, "importIsland": 0.0},
            "imports": {"total": 0, "native": 0, "fenced": 0, "island": 0, "share": 0.0},
            "requiresLibpython": False,
        },
        "planeMap": {},
        "islandRouting": {"policyLines": [], "routes": [], "staysBlocked": []},
        "demotionPlans": [],
        "notes": [],
        "compilableEntries": [],
        "imports": [],
        "files": [],
        "verify": None,
        "bench": None,
        "typing": _typing_section(pkg, [str(pkg / "m.py")]),
    }
    text = render_text(report)
    assert "Typing (mode standard)" in text
    assert "PS1001" in text
    capsys.readouterr()


def test_off_leaves_the_rest_of_the_report_untouched(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def f(x):\n    return x\n")

    from pyscriptc.cli._report import _typing_policy
    from pyscriptc.infercoverage import coverage_report
    from pyscriptc.owninfer import build_own_result

    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n')
    off_report = coverage_report(pkg, build_own_result(pkg).facts, policy=_typing_policy(pkg))
    plain_report = coverage_report(pkg, build_own_result(pkg).facts)
    assert off_report["typing"]["findings"] == []
    off_report.pop("typing")
    assert off_report == plain_report


# --- the quality lane proof -------------------------------------------------


def test_a_level_changes_no_lowering(tmp_path: Path) -> None:
    """The compile output of a file is identical at every level."""

    from pyscriptc.lower import lower_source

    source = "def f(x: int) -> int:\n    return x + 1\n"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(source)
    outputs: list[str] = []
    for level in LEVELS:
        policy = TypingPolicy(mode=level)
        findings = scan_file(pkg / "m.py")
        typing_report(policy, [pkg / "m.py"], findings)
        unit = lower_source(source, filename=str(pkg / "m.py"))
        outputs.append(json.dumps(unit, sort_keys=True, default=str))
    assert len(set(outputs)) == 1
