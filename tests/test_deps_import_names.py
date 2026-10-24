"""Issue #408: the deps report maps import names to distributions.

A distribution and an import name are different things: the `tomli`
distribution provides the `tomli` import, `python-dateutil` provides
`dateutil`, `PyYAML` provides `yaml`. The mapping is read from the wheel's
`top_level.txt` (else its RECORD's top-level entries) while the wheel is
open at unpack, stored beside the unpacked source root, carried in the
`deps` report per distribution, and read by `coverage` and the compile
fence path so a fence on the import names the distribution and its lane.

A wheel with neither `top_level.txt` nor usable RECORD entries is reported
as `unknown`, never guessed. uv and the wheel downloads are faked: the
tests never reach the network.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pyscriptc import cli, coverage, deps

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


# ------------------------------------------------------------ wheel fixtures


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(name: str, files: dict[str, bytes]) -> bytes:
    """A pure wheel; add `f'{name}-1.0.dist-info/top_level.txt'` to map names."""

    dist = f"{name}-1.0.dist-info"
    files = dict(files)
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n".encode()
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    rows = [f"{path},sha256={_b64(data)},{len(data)}" for path, data in files.items()]
    rows.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pylock(entries: list[str]) -> str:
    return 'lock-version = "1.0"\ncreated-by = "uv"\n\n' + "\n".join(entries)


def _wheel_entry(name: str, version: str, filename: str, digest: str) -> str:
    return (
        f'[[packages]]\nname = "{name}"\nversion = "{version}"\n'
        f'wheels = [{{ url = "https://files.example/{filename}", hashes = {{ sha256 = "{digest}" }} }}]\n'
    )


# The requests-family shape, shrunk: a distribution (`PyYAML`) whose import
# name (`yaml`) differs, one distribution with a top_level.txt listing two
# modules, one with RECORD fallback, one with neither.
YAML_WHEEL = make_wheel(
    "PyYAML",
    {
        "yaml/__init__.py": b"from .core import safe_load\n",
        "yaml/core.py": b"def safe_load(text: str) -> int:\n    return 1\n",
        "PyYAML-1.0.dist-info/top_level.txt": b"yaml\n",
    },
)
TWO_WHEEL = make_wheel(
    "dateutil-ish",
    {
        "dateutil/__init__.py": b"from .relativedelta import RelativeDelta, RelRelativeDelta\n",
        "dateutil/relativedelta.py": (
            b"class RelativeDelta:\n    pass\n\n\nclass RelRelativeDelta(RelativeDelta):\n    pass\n"
        ),
        "six/__init__.py": b"",
        "dateutil-ish-1.0.dist-info/top_level.txt": b"dateutil\nsix\n",
    },
)
RECORD_ONLY_WHEEL = make_wheel(
    "opaque",
    {
        "tomli/__init__.py": b"VERSION = '2.0'\n",
        "tomli/_parser.py": b"",
    },
)
NO_NAMES_WHEEL = make_wheel("bare", {"tests/test_bare.py": b"pass\n"})
NATIVE_WHEEL = make_wheel(
    "sneaky",
    {
        "sneaky/__init__.py": b"",
        "sneaky/_speed.so": b"\x7fELF",
        "sneaky-1.0.dist-info/top_level.txt": b"sneaky\n",
    },
)

ALL_WHEELS = {
    "pyyaml-1.0-py3-none-any.whl": YAML_WHEEL,
    "dateutil-ish-1.0-py3-none-any.whl": TWO_WHEEL,
    "opaque-1.0-py3-none-any.whl": RECORD_ONLY_WHEEL,
    "bare-1.0-py3-none-any.whl": NO_NAMES_WHEEL,
    "sneaky-1.0-py3-none-any.whl": NATIVE_WHEEL,
}

SCRIPT = """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyYAML",
#   "dateutil-ish",
#   "opaque",
#   "bare",
# ]
# ///
print("hi")
"""


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "lock": _pylock(
            [
                _wheel_entry("pyyaml", "1.0", "pyyaml-1.0-py3-none-any.whl", sha(YAML_WHEEL)),
                _wheel_entry("dateutil-ish", "1.0", "dateutil-ish-1.0-py3-none-any.whl", sha(TWO_WHEEL)),
                _wheel_entry("opaque", "1.0", "opaque-1.0-py3-none-any.whl", sha(RECORD_ONLY_WHEEL)),
                _wheel_entry("bare", "1.0", "bare-1.0-py3-none-any.whl", sha(NO_NAMES_WHEEL)),
            ]
        ),
        "calls": [],
        "blobs": dict(ALL_WHEELS),
        "fetched": [],
    }

    def run_uv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        state["calls"].append(argv)
        if argv[1:3] == ["pip", "compile"]:
            Path(argv[argv.index("-o") + 1]).write_text(state["lock"], encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "export":
            return subprocess.CompletedProcess(argv, 0, state["lock"], "")
        raise AssertionError(argv)

    def urlopen(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        state["fetched"].append(name)
        return state["blobs"][name]

    monkeypatch.setattr(deps, "_run_uv", run_uv)
    monkeypatch.setattr(deps, "_urlopen", urlopen)
    monkeypatch.setenv("PYSC_UV", "uv")
    monkeypatch.setenv("PYSC_NO_TELEMETRY", "1")
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))
    return state


def _script(tmp_path: Path, body: str = SCRIPT) -> Path:
    path = tmp_path / "tool.py"
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------------ record at unpack


def test_materialise_records_top_level_names(tmp_path: Path) -> None:
    root = deps.materialise(YAML_WHEEL, sha(YAML_WHEEL), tmp_path, name="pyyaml")
    assert (root / ".imports").read_text(encoding="utf-8") == "yaml\n"
    assert deps.unpacked_import_names(root) == ("yaml",)


def test_materialise_records_every_top_level_name(tmp_path: Path) -> None:
    root = deps.materialise(TWO_WHEEL, sha(TWO_WHEEL), tmp_path, name="dateutil-ish")
    assert deps.unpacked_import_names(root) == ("dateutil", "six")


def test_materialise_falls_back_to_the_record_top_level(tmp_path: Path) -> None:
    root = deps.materialise(RECORD_ONLY_WHEEL, sha(RECORD_ONLY_WHEEL), tmp_path, name="opaque")
    assert deps.unpacked_import_names(root) == ("tomli",)


def test_materialise_without_any_names_records_none(tmp_path: Path) -> None:
    root = deps.materialise(NO_NAMES_WHEEL, sha(NO_NAMES_WHEEL), tmp_path, name="bare")
    assert not (root / ".imports").exists()
    assert deps.unpacked_import_names(root) == ()


def test_materialise_audit_event_carries_the_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pyscriptc.log import log_path

    monkeypatch.setenv("PYSC_LOG_DIR", str(tmp_path / "audit"))
    deps.materialise(YAML_WHEEL, sha(YAML_WHEEL), tmp_path, name="pyyaml")
    records = [json.loads(line) for line in log_path().read_text(encoding="utf-8").splitlines()]
    event = next(r for r in records if r.get("event") == "deps.materialise")
    assert event["importNames"] == {"count": 1, "names": ["yaml"]}


# ------------------------------------------------------------ the report


def test_the_deps_report_lists_import_names_per_distribution(fake: dict[str, Any], tmp_path: Path) -> None:
    report = deps.run_deps(_script(tmp_path))
    names = {p["name"]: p["import_names"] for p in report["packages"]}
    assert names == {
        "pyyaml": ("yaml",),
        "dateutil-ish": ("dateutil", "six"),
        "opaque": ("tomli",),
        "bare": (),
    }


def test_the_report_maps_import_names_to_distributions(fake: dict[str, Any], tmp_path: Path) -> None:
    report = deps.run_deps(_script(tmp_path))
    assert report["importNames"]["yaml"] == {
        "distribution": "pyyaml",
        "lane": "pure",
        "hint": "compile from source",
    }
    assert report["importNames"]["dateutil"]["distribution"] == "dateutil-ish"
    assert report["importNames"]["tomli"]["distribution"] == "opaque"
    # A wheel with neither source of names: `unknown`, never guessed.
    assert report["importNames"]["bare"] == {
        "distribution": "bare",
        "lane": "pure",
        "hint": "compile from source",
        "importNames": "unknown",
    }


def test_text_report_says_provides(fake: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["deps", str(_script(tmp_path))]) == 0  # every wheel here is pure
    out = capsys.readouterr().out
    assert "provides: yaml" in out
    assert "provides: dateutil, six" in out
    assert "provides: unknown" in out


def test_a_native_wheel_contributes_no_import_names(fake: dict[str, Any], tmp_path: Path) -> None:
    fake["lock"] = _pylock([_wheel_entry("sneaky", "1.0", "sneaky-1.0-py3-none-any.whl", sha(NATIVE_WHEEL))])
    script = _script(
        tmp_path,
        """
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "sneaky",
# ]
# ///
print("hi")
""",
    )
    report = deps.run_deps(script)
    assert report["importNames"] == {
        "sneaky": {"distribution": "sneaky", "lane": "native", "hint": report["packages"][0]["hint"]}
    }


def test_two_distributions_claiming_one_name_is_ambiguous(tmp_path: Path) -> None:
    left = deps.DepResult("first", "1.0", "pure", "none-any wheel", import_names=("shared", "left"))
    right = deps.DepResult("second", "1.0", "native", "wheels", import_names=("shared",))
    mapping = deps.import_module_map([left, right])
    assert mapping["shared"]["distribution"] == "first"
    assert mapping["shared"]["ambiguous"] == "first, second"
    assert mapping["left"]["distribution"] == "first"


# ------------------------------------------------------------ coverage


def test_coverage_names_the_distribution_behind_a_fenced_import(fake: dict[str, Any], tmp_path: Path) -> None:
    # `dateutil` is outside the compiler's closed import table, so `import
    # dateutil` fences; `yaml` is a lowered table module and never would.
    script = _script(
        tmp_path,
        """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "dateutil-ish",
# ]
# ///
import dateutil

print(dateutil.RelativeDelta())
""",
    )
    report = coverage.build_report(script)
    fenced = [e for e in report["fences"] if e["code"] == "PS2002" and "dateutil" in e["message"]]
    assert fenced, report["fences"]
    entry = fenced[0]
    assert entry["dependency"]["distribution"] == "dateutil-ish"
    assert entry["dependency"]["lane"] == "pure"
    assert "dateutil-ish provides dateutil" in entry["message"]
    assert "compile from source" in entry["message"]


def test_coverage_on_a_tree_without_dependencies_has_no_dependency_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "plain.py"
    script.write_text("import nosuch\n\nprint(nosuch.f())\n", encoding="utf-8")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the resolver ran for a source without dependencies")

    monkeypatch.setattr(coverage, "deps_run_deps", boom)
    report = coverage.build_report(script)
    assert all("dependency" not in e for e in report["fences"])


def test_coverage_keeps_the_plain_message_when_the_map_does_not_know_the_name(
    fake: dict[str, Any], tmp_path: Path
) -> None:
    script = _script(
        tmp_path,
        """
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyYAML",
# ]
# ///
import nosuch

print(nosuch.f())
""",
    )
    report = coverage.build_report(script)
    fenced = [e for e in report["fences"] if e["code"] == "PS2002" and "nosuch" in e["message"]]
    assert fenced
    assert "dependency" not in fenced[0]
    assert fenced[0]["message"].endswith("is not in the closed import table")


def test_coverage_reports_an_unknown_map_as_unknown(fake: dict[str, Any], tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        """
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "bare",
# ]
# ///
import bare

print(bare.x)
""",
    )
    report = coverage.build_report(script)
    fenced = [e for e in report["fences"] if e["code"] == "PS2002" and "bare" in e["message"]]
    assert fenced
    assert fenced[0]["dependency"]["importNames"] == "unknown"


# ------------------------------------------------------------ compile fences


@needs_clang
def test_a_policy_refusal_on_a_mapped_import_names_the_distribution(fake: dict[str, Any], tmp_path: Path) -> None:
    """`refuse` in the `[imports]` table, keyed by the distribution name.

    `dateutil` is outside the closed import table: a lowered table module
    like `yaml` lowers through its own row and never reads the policy.
    """

    script = _script(
        tmp_path,
        """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "dateutil-ish",
# ]
# ///
import dateutil

print(dateutil.RelativeDelta())
""",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ndateutil_ish = "refuse"\n', encoding="utf-8")
    code = cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "tool.ir.json")])
    assert code == 3
    fences = json.loads((tmp_path / "tool.ir.json").read_text(encoding="utf-8"))["fences"]
    assert any("dateutil-ish provides dateutil" in f["message"] for f in fences), fences


@needs_clang
def test_a_fence_on_a_known_native_import_names_the_distribution(fake: dict[str, Any], tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "sneaky",
# ]
# ///
import sneaky

print(sneaky.VALUE)
""",
    )
    fake["lock"] = _pylock([_wheel_entry("sneaky", "1.0", "sneaky-1.0-py3-none-any.whl", sha(NATIVE_WHEEL))])
    code = cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "tool.ir.json")])
    assert code == 3
    fences = json.loads((tmp_path / "tool.ir.json").read_text(encoding="utf-8"))["fences"]
    assert any("sneaky provides sneaky" in f["message"] for f in fences), fences


# ------------------------------------------------------------ differential


def _pythonpath_for(fake: dict[str, Any], tmp_path: Path) -> str:
    report = deps.run_deps(_script(tmp_path))
    roots = [Path(p["source_root"]) for p in report["packages"] if p["lane"] == "pure"]
    return os.pathsep.join(str(r) for r in roots)


@needs_clang
def test_importing_through_the_mapped_name_matches_cpython(fake: dict[str, Any], tmp_path: Path) -> None:
    """The differential test: `import dateutil` compiles and prints what CPython prints.

    The wheel is the local fixture (distribution `dateutil-ish`, import
    `dateutil`); the CPython oracle resolves the same import from the
    unpacked fixture root on PYTHONPATH. No network anywhere. (`import
    yaml` cannot carry this test: `yaml` is a lowered module of the closed
    import table and never goes through the dependency roots.)
    """

    script = _script(
        tmp_path,
        """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "dateutil-ish",
# ]
# ///
import dateutil

print(dateutil.RelRelativeDelta)
""",
    )
    expected = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": _pythonpath_for(fake, tmp_path)},
    )
    got = cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(tmp_path / "bin")])
    assert got == 0
    binary = tmp_path / "bin"
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.stdout == expected.stdout
    assert ran.returncode == expected.returncode
