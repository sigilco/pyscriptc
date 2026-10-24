"""Issue #398: the deps report's `sourceRoots` feed the compile path.

A PEP 723 script that imports a pure-Python wheel compiles from the
unpacked wheel's source, with the same output as CPython on the same
inputs. A native dependency still refuses, with the lane hint. The deps
cache is honored: the second compile with the same lock does no download.

uv and the wheel downloads are faked, so the tests never reach the network.
"""

from __future__ import annotations

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

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


# ------------------------------------------------------------ wheel fixtures


def _b64(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(name: str, files: dict[str, bytes]) -> bytes:
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


# A small pure wheel: a package with a re-export in its `__init__.py` and a
# submodule, the shape a real pure dependency has.
PURE_WHEEL = make_wheel(
    "tinydep",
    {
        "tinydep/__init__.py": b"from .core import greet\n\nVERSION = '1.0'\n",
        "tinydep/core.py": b"def greet(name: str) -> str:\n    return 'hi ' + name\n",
    },
)

# A wheel that ships an extension module: the native lane.
NATIVE_WHEEL = make_wheel(
    "fastdep",
    {
        "fastdep/__init__.py": b"VALUE = 1\n",
        "fastdep/_speed.so": b"\x7fELF",
    },
)

SCRIPT = """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tinydep",
# ]
# ///
import tinydep

print(tinydep.greet("world"))
print(tinydep.VERSION)
"""


def _pylock(entries: list[str]) -> str:
    return 'lock-version = "1.0"\ncreated-by = "uv"\n\n' + "\n".join(entries)


def _wheel_entry(name: str, version: str, filename: str, digest: str) -> str:
    return (
        f'[[packages]]\nname = "{name}"\nversion = "{version}"\n'
        f'wheels = [{{ url = "https://files.example/{filename}", hashes = {{ sha256 = "{digest}" }} }}]\n'
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A fake uv and wheel fetch: one pure wheel, one native wheel."""
    state: dict[str, Any] = {
        "lock": _pylock(
            [
                _wheel_entry("tinydep", "1.0", "tinydep-1.0-py3-none-any.whl", sha(PURE_WHEEL)),
                _wheel_entry("fastdep", "1.0", "fastdep-1.0-py3-none-any.whl", sha(NATIVE_WHEEL)),
            ]
        ),
        "calls": [],
        "blobs": {
            "tinydep-1.0-py3-none-any.whl": PURE_WHEEL,
            "fastdep-1.0-py3-none-any.whl": NATIVE_WHEEL,
        },
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


# ------------------------------------------------------------ source roots


def test_deps_report_source_roots_are_the_unpacked_pure_wheels(fake: dict[str, Any], tmp_path: Path) -> None:
    report = deps.run_deps(_script(tmp_path))
    pure = [p for p in report["packages"] if p["lane"] == "pure"]
    assert [p["name"] for p in pure] == ["tinydep"]
    (root,) = [p["source_root"] for p in pure]
    assert (Path(root) / "tinydep" / "__init__.py").is_file()
    assert report["sourceRoots"] == [root]


def test_source_roots_helper_returns_the_pure_roots(fake: dict[str, Any], tmp_path: Path) -> None:
    roots = deps.source_roots(_script(tmp_path))
    assert len(roots) == 1
    assert (roots[0] / "tinydep" / "core.py").is_file()


# ------------------------------------------------------------ compile path


@needs_clang
def test_pep723_script_importing_a_pure_wheel_matches_cpython(fake: dict[str, Any], tmp_path: Path) -> None:
    """The differential test: the compiled binary prints what CPython prints."""
    script = _script(tmp_path)
    expected = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(_pure_root(fake, tmp_path))},
    )
    got = cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(tmp_path / "bin")])
    assert got == 0
    binary = tmp_path / "bin"
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.stdout == expected.stdout
    assert ran.returncode == expected.returncode


def _pure_root(fake: dict[str, Any], tmp_path: Path) -> Path:
    """The unpacked pure wheel's import root, for the CPython oracle."""
    report = deps.run_deps(_script(tmp_path))
    root = next(p["source_root"] for p in report["packages"] if p["lane"] == "pure")
    return Path(root)


@needs_clang
def test_a_native_dependency_still_refuses_with_the_lane_hint(fake: dict[str, Any], tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "fastdep",
# ]
# ///
import fastdep

print(fastdep.VALUE)
""",
    )
    code = cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "tool.ir.json")])
    assert code == 3
    fences = json.loads((tmp_path / "tool.ir.json").read_text(encoding="utf-8"))["fences"]
    messages = [f["message"] for f in fences]
    assert any("fastdep" in m and "native" in m for m in messages), messages


@needs_clang
def test_the_deps_cache_is_honored_on_the_second_compile(fake: dict[str, Any], tmp_path: Path) -> None:
    """The second compile with the same lock does not download again."""
    script = _script(tmp_path)
    assert cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "a.ir.json")]) == 0
    assert fake["fetched"], "the first compile downloads the wheel"
    fake["fetched"].clear()
    fake["calls"].clear()
    assert cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "b.ir.json")]) == 0
    assert fake["fetched"] == [], "the second compile re-downloaded the wheel"
    assert not [call for call in fake["calls"] if call[1:3] == ["pip", "compile"]], "the second compile re-resolved"


def test_a_compile_without_dependencies_never_calls_the_resolver(
    fake: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "plain.py"
    script.write_text("print('hi')\n", encoding="utf-8")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the resolver ran for a source without dependencies")

    monkeypatch.setattr(cli.deps, "run_deps", boom)
    assert cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "p.ir.json")]) == 0


# ------------------------------------------------------------ lane table


@needs_clang
def test_the_imports_lane_table_keeps_a_dependency_off_the_compile_path(fake: dict[str, Any], tmp_path: Path) -> None:
    """`refuse` in the `[imports]` table: the dependency is not compiled."""
    script = _script(tmp_path)
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntinydep = "refuse"\n', encoding="utf-8")
    code = cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "tool.ir.json")])
    assert code == 3
    fences = json.loads((tmp_path / "tool.ir.json").read_text(encoding="utf-8"))["fences"]
    assert any("tinydep" in f["message"] for f in fences)


@needs_clang
def test_the_imports_lane_table_compiles_a_dependency(fake: dict[str, Any], tmp_path: Path) -> None:
    """`compile` in the `[imports]` table: the dependency compiles."""
    script = _script(tmp_path)
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntinydep = "compile"\n', encoding="utf-8")
    assert cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "tool.ir.json")]) == 0
    fences = json.loads((tmp_path / "tool.ir.json").read_text(encoding="utf-8"))["fences"]
    assert fences == []


# ------------------------------------------------------------ lowering


def test_a_dependency_module_lowers_next_to_the_script(fake: dict[str, Any], tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = _script(tmp_path)
    roots = deps.source_roots(script)
    module, _text = lower_file(str(script), dep_roots=roots)
    names = {fn["name"] for fn in module["functions"]}
    assert "tinydep.core.greet" in names
    assert module["fences"] == []


def test_a_dependency_re_export_binds_from_the_package(fake: dict[str, Any], tmp_path: Path) -> None:
    """`from tinydep import greet` binds the re-export, not the submodule."""
    from pyscriptc.lower import lower_file

    script = _script(
        tmp_path,
        """\
# /// script
# dependencies = ["tinydep"]
# ///
from tinydep import greet

print(greet("world"))
""",
    )
    roots = deps.source_roots(script)
    module, _text = lower_file(str(script), dep_roots=roots)
    assert module["fences"] == []
    names = {fn["name"] for fn in module["functions"]}
    assert "tinydep.core.greet" in names


# ------------------------------------------------------------ data files (issue #410)


def make_data_wheel(name: str, files: dict[str, bytes]) -> bytes:
    """A pure wheel whose top-level entry is a package plus loose data files."""
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


# The certifi shape: a package whose code reads a data file through
# `importlib.resources.files`, plus a nested package's data file for the
# dotted-path layout (`pkg/sub/data.bin`).
CERTIFI_WHEEL = make_data_wheel(
    "certifi",
    {
        "certifi/__init__.py": b"from certifi.core import where, contents\n\n__all__ = ['where', 'contents']\n",
        "certifi/core.py": (
            b"from importlib.resources import files\n\n"
            b"def where() -> str:\n"
            b"    return str(files('certifi').joinpath('cacert.pem'))\n\n"
            b"def contents() -> str:\n"
            b"    return files('certifi').joinpath('cacert.pem').read_text(encoding='ascii')\n"
        ),
        "certifi/cacert.pem": b"-----BEGIN CERTIFICATE-----\nMIIBkTCB+wIIAN\n-----END CERTIFICATE-----\n",
    },
)

NESTED_WHEEL = make_data_wheel(
    "pkgx",
    {
        "pkgx/__init__.py": b"",
        "pkgx/sub/__init__.py": b"",
        "pkgx/sub/reader.py": (
            b"from importlib.resources import files\n\n\ndef blob() -> bytes:\n"
            b"    return files('pkgx.sub').joinpath('data.bin').read_bytes()\n"
        ),
        "pkgx/sub/data.bin": bytes(range(256)),
    },
)


DATA_SCRIPT = """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "certifi",
# ]
# ///
from certifi import contents, where


def main() -> None:
    pem: bytes = contents().encode("utf-8")
    print(len(pem))
    print(where().endswith("certifi/cacert.pem"))
    print(pem[0:5])


if __name__ == "__main__":
    main()
"""


def _data_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wheels: dict[str, bytes]) -> dict[str, Any]:
    """A fake uv and wheel fetch over the given wheel bytes."""
    entries = []
    blobs: dict[str, bytes] = {}
    for name, data in wheels.items():
        filename = f"{name}-1.0-py3-none-any.whl"
        entries.append(_wheel_entry(name, "1.0", filename, sha(data)))
        blobs[filename] = data
    state: dict[str, Any] = {
        "lock": _pylock(entries),
        "calls": [],
        "blobs": blobs,
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


def test_data_files_picks_the_non_code_members(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    found = deps.data_files(CERTIFI_WHEEL)
    assert [path for path, _body in found] == ["certifi/cacert.pem"]
    nested = deps.data_files(NESTED_WHEEL)
    assert [path for path, _body in nested] == ["pkgx/sub/data.bin"]
    assert dict(nested)["pkgx/sub/data.bin"] == bytes(range(256))


def test_a_wheel_without_data_files_stages_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert deps.data_files(PURE_WHEEL) == []
    _data_fake(monkeypatch, tmp_path, {"tinydep": PURE_WHEEL})
    script = _script(tmp_path)
    binary = tmp_path / "tool"
    from pyscriptc.cli import _stage_dependency_data

    staged = _stage_dependency_data(script, _deps_config(script), _Args("bin", False), binary)
    assert staged is not None and staged.name == "tool-data"
    assert not staged.exists(), "a package with no data files must stage nothing"


class _Args:
    """The emit lane fields `_stage_dependency_data` reads off the argparse namespace."""

    def __init__(self, emit: str, run: bool) -> None:
        self.emit = emit
        self.run = run


def _deps_config(script: Path) -> Any:
    from pyscriptc import config

    return config.load_config(script)


@needs_clang
def test_certifi_resources_match_cpython(fake: dict[str, Any], tmp_path: Path) -> None:
    """The differential test: the compiled binary reads the staged PEM like CPython."""
    monkey = pytest.MonkeyPatch()
    try:
        _data_fake(monkey, tmp_path, {"certifi": CERTIFI_WHEEL})
        script = _script(tmp_path, DATA_SCRIPT)
        # CPython reads the data file next to the unpacked wheel's source root
        # (the host cannot see the wheel's package layout), so the oracle run
        # happens with that root on PYTHONPATH and the tree's parent as cwd:
        # the staged tree in the compiled run is the parent's image of it.
        root = _pure_root_named(monkey, tmp_path, "certifi")
        expected = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(root.parent),
            env={**os.environ, "PYTHONPATH": str(root)},
        )
        binary = tmp_path / "bin"
        got = cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(binary)])
        assert got == 0
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        assert ran.stdout == expected.stdout, (ran.stdout, expected.stdout, ran.stderr)
        assert ran.returncode == expected.returncode
        staged = tmp_path / "bin-data" / "certifi" / "cacert.pem"
        assert staged.is_file(), "the data file is laid out under the dotted path"
    finally:
        monkey.undo()


def _pure_root_named(monkey: pytest.MonkeyPatch, tmp_path: Path, name: str, body: str = DATA_SCRIPT) -> Path:
    report = deps.run_deps(_script(tmp_path, body))
    return Path(next(p["source_root"] for p in report["packages"] if p["name"] == name))


@needs_clang
def test_nested_package_data_lands_under_the_dotted_path(fake: dict[str, Any], tmp_path: Path) -> None:
    monkey = pytest.MonkeyPatch()
    try:
        _data_fake(monkey, tmp_path, {"pkgx": NESTED_WHEEL})
        body = """\
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pkgx",
# ]
# ///
from pkgx.sub.reader import blob


def main() -> None:
    data: bytes = blob()
    print(len(data))
    print(data[0], data[255])


if __name__ == "__main__":
    main()
"""
        script = _script(tmp_path, body)
        binary = tmp_path / "nested"
        got = cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(binary)])
        assert got == 0
        staged = tmp_path / "nested-data" / "pkgx" / "sub" / "data.bin"
        assert staged.is_file()
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        root = _pure_root_named(monkey, tmp_path, "pkgx", body)
        expected = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(root.parent),
            env={**os.environ, "PYTHONPATH": str(root)},
        )
        assert ran.stdout == expected.stdout, (ran.stdout, expected.stdout, ran.stderr)
        assert ran.returncode == 0
    finally:
        monkey.undo()


@needs_clang
def test_a_recompile_replaces_the_staged_tree(fake: dict[str, Any], tmp_path: Path) -> None:
    """The stale staged file from the previous compile is gone after a recompile."""
    monkey = pytest.MonkeyPatch()
    try:
        _data_fake(monkey, tmp_path, {"certifi": CERTIFI_WHEEL})
        script = _script(tmp_path, DATA_SCRIPT)
        binary = tmp_path / "bin"
        assert cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(binary)]) == 0
        stale = tmp_path / "bin-data" / "certifi" / "stale.pem"
        stale.write_bytes(b"old")
        assert cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(binary)]) == 0
        assert not stale.exists(), "the recompile removed the staged tree"
        assert (tmp_path / "bin-data" / "certifi" / "cacert.pem").is_file()
    finally:
        monkey.undo()


@needs_clang
def test_no_dependencies_compiles_without_a_staged_tree(tmp_path: Path) -> None:
    script = tmp_path / "plain.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    binary = tmp_path / "plain"
    assert cli.main(["compile", str(script), "--skip-check", "-o", str(binary)]) == 0
    assert not (tmp_path / "plain-data").exists()
