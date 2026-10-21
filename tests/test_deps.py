"""`pyscriptc deps`: PEP 723 / pyproject parsing, pylock handling, lanes, cache.

uv and the wheel downloads are faked; one opt-in test talks to PyPI
(PYSC_NETWORK_TESTS=1).
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

from pyscriptc import cli, deps
from pyscriptc.deps import DepsError

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")

SCRIPT = """\
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "colorama>=0.4",
#   "PyYAML",
# ]
# ///
print("hi")
"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(
    name: str, files: dict[str, bytes], *, purelib: bool = True, record_override: dict[str, str] | None = None
) -> bytes:
    dist = f"{name}-1.0.dist-info"
    files = dict(files)
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n".encode()
    files[f"{dist}/WHEEL"] = (
        f"Wheel-Version: 1.0\nRoot-Is-Purelib: {'true' if purelib else 'false'}\nTag: py3-none-any\n".encode()
    )
    rows = [f"{path},sha256={_b64(data)},{len(data)}" for path, data in files.items()]
    for path, digest in (record_override or {}).items():
        rows = [r for r in rows if not r.startswith(path + ",")] + [f"{path},{digest},0"]
    rows.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pylock(entries: list[str]) -> str:
    return 'lock-version = "1.0"\ncreated-by = "uv"\n\n' + "\n".join(entries)


def wheel_entry(name: str, version: str, filenames_and_hashes: list[tuple[str, str]], marker: str | None = None) -> str:
    wheels = ", ".join(
        f'{{ url = "https://files.example/{fn}", hashes = {{ sha256 = "{h}" }} }}' for fn, h in filenames_and_hashes
    )
    out = f'[[packages]]\nname = "{name}"\nversion = "{version}"\n'
    if marker:
        out += f"marker = {json.dumps(marker)}\n"
    return out + f"wheels = [{wheels}]\n"


DOCOPT_SDIST = (
    '[[packages]]\nname = "docopt"\nversion = "0.6.2"\n'
    f'sdist = {{ url = "https://files.example/docopt-0.6.2.tar.gz", hashes = {{ sha256 = "{"c" * 64}" }} }}\n'
)
COLORAMA = make_wheel("colorama", {"colorama/__init__.py": b"VERSION = '1.0'\n"})
NATIVE_IN_ANY = make_wheel("sneaky", {"sneaky/__init__.py": b"", "sneaky/_speed.so": b"\x7fELF"})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "lock": pylock(
            [
                wheel_entry("colorama", "0.4.6", [("colorama-0.4.6-py2.py3-none-any.whl", sha(COLORAMA))]),
                wheel_entry(
                    "pyyaml",
                    "6.0.3",
                    [
                        ("pyyaml-6.0.3-cp314-cp314-macosx_11_0_arm64.whl", "a" * 64),
                        ("pyyaml-6.0.3-cp314-cp314-manylinux_2_28_x86_64.whl", "b" * 64),
                    ],
                ),
                DOCOPT_SDIST,
            ]
        ),
        "calls": [],
        "blobs": {"colorama-0.4.6-py2.py3-none-any.whl": COLORAMA},
        "fetched": [],
    }

    def run_uv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        state["calls"].append(argv)
        if argv[1:3] == ["pip", "compile"]:
            assert "--no-build" in argv
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


# ------------------------------------------------------------ parsing


def test_pep723_block_is_read() -> None:
    meta = deps.read_pep723(SCRIPT)
    assert meta == {"requires-python": ">=3.11", "dependencies": ["colorama>=0.4", "PyYAML"]}


def test_pep723_absent_and_other_block_types() -> None:
    assert deps.read_pep723("print(1)\n") is None
    assert deps.read_pep723("# /// tool\n# x = 1\n# ///\n") is None


def test_pep723_rejects_two_script_blocks() -> None:
    block = "# /// script\n# dependencies = []\n# ///\n"
    with pytest.raises(DepsError, match="more than one"):
        deps.read_pep723(block + "\n" + block)


def test_load_spec_script_and_pyproject(tmp_path: Path) -> None:
    script = tmp_path / "tool.py"
    script.write_text(SCRIPT, encoding="utf-8")
    spec = deps.load_spec(script)
    assert spec.kind == "script" and spec.dependencies == ("colorama>=0.4", "PyYAML")
    assert spec.lock_path.name == "pylock.tool.toml"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.12"\ndependencies = ["attrs>=23", "click"]\n', encoding="utf-8"
    )
    spec = deps.load_spec(tmp_path)
    assert spec.kind == "project" and spec.requires_python == ">=3.12"
    assert spec.dependencies == ("attrs>=23", "click")
    assert spec.lock_path.name == "pylock.pyscriptc.toml"


def test_load_spec_rejects_bad_requirements(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = "attrs"\n', encoding="utf-8")
    with pytest.raises(DepsError, match="list of requirement"):
        deps.load_spec(tmp_path)


def test_requirement_name_normalises() -> None:
    assert deps.requirement_name("Foo_Bar.baz[extra]>=1; python_version<'4'") == "foo-bar-baz"
    with pytest.raises(DepsError):
        deps.requirement_name(">=1")


# ------------------------------------------------------------ classification


@pytest.mark.parametrize(
    ("filename", "pure"),
    [
        ("a-1.0-py3-none-any.whl", True),
        ("a-1.0-py2.py3-none-any.whl", True),
        ("a-1.0-1-py312-none-any.whl", True),
        ("a-1.0-py399-none-any.whl", False),
        ("a-1.0-py2-none-any.whl", False),
        ("a-1.0-cp312-abi3-manylinux_2_28_x86_64.whl", False),
        ("a-1.0-py3-none-macosx_11_0_arm64.whl", False),
    ],
)
def test_pure_wheel_tags(filename: str, pure: bool) -> None:
    assert deps.is_pure_wheel(deps.LockedWheel("https://x/" + filename, "0" * 64, filename), "3.14") is pure


def test_lanes_from_tags(fake: dict[str, Any]) -> None:
    lanes = {p.name: deps.classify_tags(p)[0] for p in deps.parse_pylock(fake["lock"])}
    assert lanes == {"colorama": "pure", "pyyaml": "native", "docopt": "sdist"}


def test_parse_pylock_requires_hashes() -> None:
    text = pylock(['[[packages]]\nname = "a"\nversion = "1"\nwheels = [{ url = "https://x/a-1-py3-none-any.whl" }]\n'])
    with pytest.raises(DepsError, match="sha256"):
        deps.parse_pylock(text)


def test_inspect_wheel_pure_and_native() -> None:
    assert deps.inspect_wheel(COLORAMA)[0] is True
    pure, reason = deps.inspect_wheel(NATIVE_IN_ANY)
    assert not pure and "_speed.so" in reason
    pure, reason = deps.inspect_wheel(make_wheel("p", {"p/__init__.py": b""}, purelib=False))
    assert not pure and "Root-Is-Purelib" in reason


def test_inspect_wheel_rejects_tampered_record() -> None:
    bad = make_wheel("t", {"t/__init__.py": b"x = 1\n"}, record_override={"t/__init__.py": "sha256=" + "A" * 43})
    with pytest.raises(DepsError, match="RECORD hash"):
        deps.inspect_wheel(bad)


def test_inspect_wheel_rejects_path_traversal() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.py", b"")
    with pytest.raises(DepsError, match="unsafe path"):
        deps.inspect_wheel(buf.getvalue())


# ------------------------------------------------------------ resolve, lock, cache


def _script(tmp_path: Path) -> Path:
    path = tmp_path / "tool.py"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def test_run_deps_materialises_pure_wheels(fake: dict[str, Any], tmp_path: Path) -> None:
    report = deps.run_deps(_script(tmp_path))
    assert report["lockProvenance"] == "resolved"
    assert report["lanes"] == {"native": 1, "pure": 1, "sdist": 1}
    (root,) = report["sourceRoots"]
    assert Path(root).name == sha(COLORAMA)
    assert (Path(root) / "colorama" / "__init__.py").read_text() == "VERSION = '1.0'\n"
    blob = tmp_path / "cache" / "deps" / "blobs" / "sha256" / sha(COLORAMA)[:2] / f"{sha(COLORAMA)}.whl"
    assert blob.read_bytes() == COLORAMA
    lock = (tmp_path / "pylock.tool.toml").read_text()
    assert "[tool.pyscriptc]" in lock and "input-digest" in lock


def test_lock_is_reused_and_cache_hit_skips_network(fake: dict[str, Any], tmp_path: Path) -> None:
    script = _script(tmp_path)
    deps.run_deps(script)
    fake["calls"].clear()
    fake["fetched"].clear()
    report = deps.run_deps(script, frozen=True, offline=True)
    assert report["lockProvenance"] == "lock"
    assert fake["calls"] == [] and fake["fetched"] == []


def test_stale_lock_with_frozen_fails(fake: dict[str, Any], tmp_path: Path) -> None:
    script = _script(tmp_path)
    deps.run_deps(script)
    script.write_text(SCRIPT.replace('"PyYAML",', '"PyYAML",\n#   "attrs",'), encoding="utf-8")
    with pytest.raises(DepsError, match="stale"):
        deps.run_deps(script, frozen=True)
    assert deps.run_deps(script)["lockProvenance"] == "resolved"


def test_frozen_without_lock_fails(fake: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(DepsError, match="--frozen needs"):
        deps.run_deps(_script(tmp_path), frozen=True)


def test_uv_lock_is_exported_not_rewritten(fake: dict[str, Any], tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["colorama"]\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    report = deps.run_deps(tmp_path, download=False)
    assert report["lockProvenance"] == "uv.lock"
    (call,) = fake["calls"]
    assert call[1] == "export" and "--frozen" in call
    assert not (tmp_path / "pylock.pyscriptc.toml").exists()


def test_hash_mismatch_is_refused(fake: dict[str, Any], tmp_path: Path) -> None:
    fake["blobs"]["colorama-0.4.6-py2.py3-none-any.whl"] = COLORAMA + b"tampered"
    with pytest.raises(DepsError, match="does not match the lock"):
        deps.run_deps(_script(tmp_path))
    assert not (tmp_path / "cache" / "deps" / "blobs").exists()


def test_none_any_wheel_with_extension_is_native(fake: dict[str, Any], tmp_path: Path) -> None:
    fake["lock"] = pylock([wheel_entry("sneaky", "1.0", [("sneaky-1.0-py3-none-any.whl", sha(NATIVE_IN_ANY))])])
    fake["blobs"]["sneaky-1.0-py3-none-any.whl"] = NATIVE_IN_ANY
    report = deps.run_deps(_script(tmp_path))
    (pkg,) = report["packages"]
    assert pkg["lane"] == "native" and pkg["source_root"] is None


def test_uv_no_build_failure_gets_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def run_uv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        msg = "hint: Wheels are required for `docopt` because building from source is disabled"
        return subprocess.CompletedProcess(argv, 1, "", msg)

    monkeypatch.setattr(deps, "_run_uv", run_uv)
    monkeypatch.setenv("PYSC_UV", "uv")
    with pytest.raises(DepsError, match="never builds sdists"):
        deps.run_deps(_script(tmp_path), cache_dir=tmp_path / "c")


def test_cli_deps_subcommand(fake: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _script(tmp_path)
    assert cli.main(["deps", str(script), "--json", "--no-download"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["lanes"] == {"native": 1, "pure": 1, "sdist": 1}
    fake["lock"] = pylock([wheel_entry("colorama", "0.4.6", [("colorama-0.4.6-py2.py3-none-any.whl", sha(COLORAMA))])])
    script.write_text(SCRIPT.replace('#   "PyYAML",\n', ""), encoding="utf-8")
    assert cli.main(["deps", str(script)]) == 0
    assert "colorama 0.4.6" in capsys.readouterr().out


# ------------------------------------------------------------ network (opt-in)


@pytest.mark.skipif(
    os.environ.get("PYSC_NETWORK_TESTS") != "1" or shutil.which("uv") is None,
    reason="network test: set PYSC_NETWORK_TESTS=1 and install uv",
)
def test_network_resolve_real_pypi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_UV", raising=False)
    script = tmp_path / "net.py"
    script.write_text('# /// script\n# dependencies = ["colorama==0.4.6", "pyyaml==6.0.3"]\n# ///\n', encoding="utf-8")
    report = deps.run_deps(script, cache_dir=tmp_path / "cache")
    lanes = {p["name"]: p["lane"] for p in report["packages"]}
    assert lanes == {"colorama": "pure", "pyyaml": "native"}
    assert (Path(report["sourceRoots"][0]) / "colorama" / "__init__.py").is_file()
