# SPDX-License-Identifier: Apache-2.0
"""The typing report (issue #1315).

The contract these tests hold:

- the config has one key, `mode`, with two values: `standard` (the
  default, so an unconfigured project is unchanged) and `off`;
- the mode only configures the report: which typing findings surface;
  compilation, lowering and exit codes are untouched;
- the scanner's names come from the lowerer's own tables, so the report
  and the compiler cannot name the same construct two ways;
- `pyscriptc infer --report` carries the typing section;
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
    DEFAULT_MODE,
    MODES,
    TypingModeError,
    TypingPolicy,
    check_mode,
    scan_file,
    typing_report,
)

# ---------------------------------------------------------------------------
# the modes
# ---------------------------------------------------------------------------


def test_modes_are_the_two_named_ones() -> None:
    assert MODES == ["standard", "off"]
    assert DEFAULT_MODE == "standard"


def test_unknown_mode_is_an_error() -> None:
    with pytest.raises(TypingModeError):
        check_mode("strict")


def test_the_policy_reports_whether_it_is_enabled() -> None:
    assert TypingPolicy(mode="standard").enabled() is True
    assert TypingPolicy(mode="off").enabled() is False


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


def test_scan_says_nothing_about_a_taken_alias(tmp_path: Path) -> None:
    source = "from typing import Sequence\n\n\ndef g(s: Sequence[int]) -> int:\n    return s[0]\n"
    path = write_case(tmp_path, "lib.py", source)
    findings = scan_file(path)
    assert findings == []


def test_scan_reads_the_lowering_tables() -> None:
    """The scanner's name lists are the lowerer's own tables (issue #1315)."""

    from pyscriptc.lower._annotations import _DYNAMIC_ANNOTATIONS, _TAKEN_ALIASES
    from pyscriptc.typinglevels import _lowerer_tables

    dynamic, taken, bare_taken, _erased = _lowerer_tables()
    assert dynamic == frozenset(_DYNAMIC_ANNOTATIONS - {"NoReturn", "Never"})
    assert taken == frozenset(_TAKEN_ALIASES)


def test_the_scan_agrees_with_the_compiler(tmp_path: Path) -> None:
    """Every construct form the scan reports is one the compiler fences."""

    from pyscriptc.lower import lower_source

    cases = {
        "dynamic.py": "from typing import Any\n\n\ndef f(x: Any) -> None:\n    pass\n",
        "alias.py": "from typing import Awaitable\n\n\ndef f(x: Awaitable[int]) -> None:\n    pass\n",
        "bare.py": "from typing import List\n\n\ndef f(x: List) -> None:\n    pass\n",
    }
    for name, source in cases.items():
        path = write_case(tmp_path, name, source)
        scanned = {finding.code for finding in scan_file(path) if finding.code != "PS1001"}
        lowered = lower_source(source, str(path))
        fenced = {fence["code"] for fence in lowered.get("fences", [])} - {"PS2002"}
        assert scanned == fenced, name


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


def test_config_parses_the_typing_table(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    config = load_config(script)
    policy = config.typing_policy()
    assert policy.mode == "off"


def test_config_rejects_an_unknown_mode(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "strict"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script).typing_policy()


def test_config_rejects_an_unknown_typing_key(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmodee = "off"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script)


def test_config_rejects_a_glob_list(tmp_path: Path) -> None:
    """The per-glob lists are gone: a level list is an unknown key."""

    script = write_case(tmp_path, "main.py", "print(1)\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nstrict = ["src/**"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(script)


def test_config_without_the_table_gives_the_default(tmp_path: Path) -> None:
    script = write_case(tmp_path, "main.py", "print(1)\n")
    config = load_config(script)
    assert config.typing_policy().mode == DEFAULT_MODE


# ---------------------------------------------------------------------------
# the report section
# ---------------------------------------------------------------------------


def test_the_report_surfaces_the_findings(tmp_path: Path) -> None:
    path = write_case(tmp_path, "lib.py", "def shout(text):\n    return text.upper()\n")
    findings = scan_file(path)
    section = typing_report(TypingPolicy(mode="standard"), [path], findings)
    assert section["mode"] == "standard"
    assert section["summary"]["files"] == 1
    assert section["summary"]["findings"] == len(findings)


def test_the_report_off_surfaces_nothing(tmp_path: Path) -> None:
    path = write_case(tmp_path, "lib.py", "def shout(text):\n    return text.upper()\n")
    findings = scan_file(path)
    section = typing_report(TypingPolicy(mode="off"), [path], findings)
    assert section["mode"] == "off"
    assert section["findings"] == []
    assert section["summary"]["findings"] == 0


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
    assert section["summary"]["findings"] >= 2
    families = {finding["family"] for finding in section["findings"]}
    assert families == {"param-inference", "return-inference"}


def test_infer_report_respects_the_mode(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    report = _report(root)
    section = report["typing"]
    assert section["mode"] == "off"
    assert section["findings"] == []
    # The coverage numbers stay: off silences the typing findings, not the
    # coverage report.
    assert report["total"]["unannotated_params"] == 1


def test_infer_report_text_names_the_mode_and_the_findings(tmp_path: Path) -> None:
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
    assert section["summary"]["findings"] >= 2


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
# the mode never changes what compiles
# ---------------------------------------------------------------------------


def test_the_mode_does_not_change_the_compile(tmp_path: Path) -> None:
    source = write_case(tmp_path, "main.py", "def shout(text: str) -> str:\n    return text.upper()\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "standard"\n', encoding="utf-8")
    standard_run = run_cli("compile", str(source), "--emit", "ir", "-o", str(tmp_path / "standard"))
    (tmp_path / "pyproject.toml").write_text('[tool.pyscriptc.typing]\nmode = "off"\n', encoding="utf-8")
    off_run = run_cli("compile", str(source), "--emit", "ir", "-o", str(tmp_path / "off"))
    assert standard_run.returncode == 0, standard_run.stderr
    assert off_run.returncode == 0, off_run.stderr
    standard_ir = (tmp_path / "standard").read_text(encoding="utf-8")
    off_ir = (tmp_path / "off").read_text(encoding="utf-8")
    assert standard_ir == off_ir
