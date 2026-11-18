# SPDX-License-Identifier: Apache-2.0
"""The typing strictness levels (issue #1315).

The contract these tests hold:

- the four levels are `off`, `basic`, `standard`, `strict`; `standard` is
  the default, so an unconfigured project is unchanged;
- a level only configures the report: which typing findings surface and at
  what severity; compilation, lowering and exit codes are untouched;
- the per-level glob lists in `[tool.pyscriptc.typing]` set the level of
  the paths they match;
- the per-file pragma `# pyscriptc: typed=<level>` may only raise the
  level over the config, never lower it (the Pyright ratchet rule);
- `pyscriptc infer --report` carries the typing section and a level per
  module row;
- `pyscriptc assess` carries the typing section in JSON and in text;
- undecorated/default behaviour is unchanged: a project without the table
  gets the standard mode and the plain report shape stays.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc.config import ConfigError, load_config
from pyscriptc.typinglevels import (
    DEFAULT_LEVEL,
    LEVELS,
    TypingLevelError,
    TypingPolicy,
    gate,
    parse_pragma,
    scan_file,
    severity_for,
)

# ---------------------------------------------------------------------------
# levels and severities
# ---------------------------------------------------------------------------


def test_levels_are_the_four_named_modes() -> None:
    assert LEVELS == ["off", "basic", "standard", "strict"]
    assert DEFAULT_LEVEL == "standard"


def test_off_suppresses_every_typing_family() -> None:
    for family in ("param-inference", "local-inference", "return-inference", "generic-alias", "dynamic-annotation"):
        assert severity_for("off", family) is None


def test_basic_and_standard_report_missing_annotations_as_warnings() -> None:
    for level in ("basic", "standard"):
        assert severity_for(level, "param-inference") == "warning"
        assert severity_for(level, "return-inference") == "warning"


def test_basic_and_standard_suppress_the_construct_family() -> None:
    for level in ("basic", "standard"):
        for family in ("generic-alias", "dynamic-annotation", "forward-reference", "unlowered-type-name"):
            assert severity_for(level, family) is None


def test_strict_reports_both_families_as_errors() -> None:
    for family in ("param-inference", "return-inference", "generic-alias", "dynamic-annotation"):
        assert severity_for("strict", family) == "error"


def test_unknown_level_is_an_error() -> None:
    with pytest.raises(TypingLevelError):
        severity_for("strong", "param-inference")


# ---------------------------------------------------------------------------
# the pragma
# ---------------------------------------------------------------------------


def test_pragma_parses_the_level(tmp_path: Path) -> None:
    assert parse_pragma("# pyscriptc: typed=strict\n") == "strict"
    assert parse_pragma("# pyscriptc: typed=basic\n\nx = 1\n") == "basic"
    assert parse_pragma("x = 1\n") is None
    assert parse_pragma("# a normal comment\n") is None


def test_pragma_rejects_an_unknown_level() -> None:
    with pytest.raises(TypingLevelError):
        parse_pragma("# pyscriptc: typed=stricter\n")
    # Sorbet's other sigils (`ignore`, `false`, `true`, `strong`) are not
    # pyscriptc levels: a wrong spelling is refused, never silently
    # accepted.
    with pytest.raises(TypingLevelError):
        parse_pragma("# pyscriptc: typed=false\n")


def test_pragma_may_only_raise(tmp_path: Path) -> None:
    source = Path("lib.py")
    policy = TypingPolicy(mode="standard")
    assert policy.level_for(source, "# pyscriptc: typed=strict\n") == ("strict", "pragma")
    # The pragma never lowers: basic under a standard mode stays standard.
    assert policy.level_for(source, "# pyscriptc: typed=basic\n") == ("standard", "config")
    # An equal pragma keeps the config level.
    assert policy.level_for(source, "# pyscriptc: typed=standard\n") == ("standard", "config")


def test_off_mode_pragma_still_raises(tmp_path: Path) -> None:
    policy = TypingPolicy(mode="off")
    assert policy.level_for(Path("lib.py"), "# pyscriptc: typed=strict\n") == ("strict", "pragma")


# ---------------------------------------------------------------------------
# globs
# ---------------------------------------------------------------------------


def test_glob_raises_the_level_of_the_matched_paths(tmp_path: Path) -> None:
    policy = TypingPolicy(mode="basic", globs={"strict": ["src/core/**"]}, base=tmp_path)
    assert policy.level_for(tmp_path / "src" / "core" / "a.py", "") == ("strict", "glob")
    assert policy.level_for(tmp_path / "src" / "other.py", "") == ("basic", "mode")


def test_glob_can_lower_below_the_project_mode(tmp_path: Path) -> None:
    policy = TypingPolicy(mode="standard", globs={"off": ["vendor/**"]}, base=tmp_path)
    assert policy.level_for(tmp_path / "vendor" / "x.py", "") == ("off", "glob")


def test_longest_matching_pattern_wins(tmp_path: Path) -> None:
    policy = TypingPolicy(mode="standard", globs={"basic": ["src/**"], "strict": ["src/core/**"]}, base=tmp_path)
    assert policy.level_for(tmp_path / "src" / "core" / "a.py", "") == ("strict", "glob")
    assert policy.level_for(tmp_path / "src" / "b.py", "") == ("basic", "glob")


def test_strict_glob_over_an_off_mode(tmp_path: Path) -> None:
    policy = TypingPolicy(mode="off", globs={"strict": ["pkg/lib.py"]}, base=tmp_path)
    assert policy.level_for(tmp_path / "pkg" / "lib.py", "") == ("strict", "glob")


# ---------------------------------------------------------------------------
# the static scan
# ---------------------------------------------------------------------------


def test_scan_finds_missing_annotations(tmp_path: Path) -> None:
    path = write_case(tmp_path, "lib.py", "def shout(text):\n    return text.upper()\n")
    findings = scan_file(path)
    families = {finding.family for finding in findings}
    assert families == {"param-inference", "return-inference"}


def test_scan_finds_the_construct_forms(tmp_path: Path) -> None:
    source = (
        "from typing import Any, Sequence\n\n\n"
        "def f(x: Any) -> str:\n    return str(x)\n\n\n"
        "def g(s: Sequence[int]) -> int:\n    return s[0]\n"
    )
    path = write_case(tmp_path, "lib.py", source)
    findings = scan_file(path)
    families = {finding.family for finding in findings}
    assert "dynamic-annotation" in families
    assert "generic-alias" in families


def test_gate_filters_by_level(tmp_path: Path) -> None:
    findings = scan_file(
        write_case(tmp_path, "gate_t.py", "from typing import Any\n\n\ndef f(x, y: Any):\n    return x\n")
    )
    assert [finding.family for finding in gate(findings, "off")] == []
    assert {finding.family for finding in gate(findings, "standard")} == {"param-inference", "return-inference"}
    assert {finding.family for finding in gate(findings, "strict")} == {
        "param-inference",
        "return-inference",
        "dynamic-annotation",
    }
    strict = gate(findings, "strict")
    assert all(finding.severity == "error" for finding in strict)


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


def test_config_parses_the_typing_table(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyscriptc.typing]\nmode = "basic"\nstrict = ["src/**"]\n', encoding="utf-8"
    )
    config = load_config(script)
    policy = config.typing_policy()
    assert policy.mode == "basic"
    assert policy.globs == {"strict": ["src/**"]}
    assert policy.base == tmp_path


def test_config_rejects_an_unknown_mode(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "strong"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script).typing_policy()


def test_config_rejects_an_unknown_typing_key(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmodee = "strict"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script)


def test_config_rejects_a_non_string_glob(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pyscriptc.typing]\nstrict = [1]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script)


def test_config_without_the_table_gives_the_default(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    config = load_config(script)
    assert config.typing_policy().mode == DEFAULT_LEVEL


# ---------------------------------------------------------------------------
# `pyscriptc infer --report`
# ---------------------------------------------------------------------------


def _report(root: Path, *extra: str) -> dict:
    out = run_cli("infer", str(root), "--report", "--json", "--bridge", "own", *extra)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_infer_report_carries_the_typing_section(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    report = _report(root)
    section = report["typing"]
    assert section["mode"] == "standard"
    assert section["summary"]["files"] == 2
    assert section["summary"]["warnings"] >= 2
    families = {finding["family"] for finding in section["findings"]}
    assert families == {"param-inference", "return-inference"}
    rows = {row["module"]: row for row in report["modules"]}
    assert rows["lib"]["level"] == "standard"


def test_infer_report_respects_the_mode_and_the_pragma(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "# pyscriptc: typed=strict\n\ndef shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    report = _report(root)
    section = report["typing"]
    assert section["mode"] == "off"
    rows = {row["module"]: row for row in report["modules"]}
    # The pragma raised lib over the off mode, __main__ stays off.
    assert rows["lib"]["level"] == "strict"
    assert rows["__main__"]["level"] == "off"
    codes = {finding["code"] for finding in section["findings"]}
    assert "PS1001" in codes
    assert all(finding["severity"] == "error" for finding in section["findings"] if finding["code"] == "PS1001")


def test_infer_report_off_suppresses_the_findings(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    report = _report(root)
    assert report["typing"]["findings"] == []
    # The coverage numbers stay: off silences the typing reports, not the
    # coverage report.
    assert report["total"]["unannotated_params"] == 1


def test_infer_report_text_names_the_level_and_the_findings(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    out = run_cli("infer", str(root), "--report", "--bridge", "own")
    assert out.returncode == 0, out.stderr
    assert "typing mode standard" in out.stdout
    assert "PS1001" in out.stdout


def test_infer_report_without_the_table_keeps_the_plain_shape(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    report = _report(root)
    # The default mode is standard, so the section is there; a script that
    # reads the old keys is unaffected.
    assert report["typing"]["mode"] == "standard"
    assert "provider" in report
    assert "modules" in report
    assert "total" in report


# ---------------------------------------------------------------------------
# `pyscriptc assess`
# ---------------------------------------------------------------------------


def test_assess_json_carries_the_typing_section(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    out = run_cli("assess", str(root), "--json")
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)
    section = report["typing"]
    assert section["mode"] == "standard"
    assert section["summary"]["warnings"] >= 2


def test_assess_text_names_the_typing_mode(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    out = run_cli("assess", str(root))
    assert out.returncode == 0, out.stderr
    assert "Typing (mode standard)" in out.stdout
    assert "PS1001" in out.stdout


def test_assess_off_mode_prints_no_findings(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    out = run_cli("assess", str(root))
    assert out.returncode == 0, out.stderr
    assert "Typing (mode off)" in out.stdout
    assert "(no typing findings)" in out.stdout


# ---------------------------------------------------------------------------
# the levels never change what compiles
# ---------------------------------------------------------------------------


def test_levels_do_not_change_the_compile(tmp_path: Path) -> None:
    source = write_case(tmp_path, "main.py", "def shout(text: str) -> str:\n    return text.upper()\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "strict"\n', encoding="utf-8")
    strict_run = run_cli("compile", str(source), "--emit", "ir", "-o", str(tmp_path / "strict"))
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    off_run = run_cli("compile", str(source), "--emit", "ir", "-o", str(tmp_path / "off"))
    assert strict_run.returncode == 0, strict_run.stderr
    assert off_run.returncode == 0, off_run.stderr
    strict_ir = (tmp_path / "strict").read_text(encoding="utf-8")
    off_ir = (tmp_path / "off").read_text(encoding="utf-8")
    assert strict_ir == off_ir
