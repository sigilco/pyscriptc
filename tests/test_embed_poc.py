"""EXPERIMENTAL embedding POC: the env seam, the engine manifest, the bundle script.

The default link must not change: with `PYSC_EXPERIMENTAL_EMBED_PREFIX` unset the
island binary links the host libpython exactly as before. The bundle test needs a
python-build-standalone `install/` tree and only runs when `PYSC_EMBED_POC_PREFIX`
points at one (see .agents/plans/2026-10-25-embedding-libpython.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from pyscriptc.boxed import embed_link_flags

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "engine_manifest.py"
VERSION = sysconfig.get_config_var("VERSION") or "3.14"

ISLAND_SRC = """import fractions

def main() -> None:
    f = fractions.Fraction(3, 4)
    s: str = str(f + fractions.Fraction(1, 6))
    print(s)

if __name__ == "__main__":
    main()
"""


def _fake_prefix(tmp_path: Path, static: bool = False) -> Path:
    (tmp_path / "include" / f"python{VERSION}").mkdir(parents=True)
    (tmp_path / "lib").mkdir()
    if static:
        (tmp_path / "lib" / f"libpython{VERSION}.a").write_bytes(b"")
    suffix = "dylib" if sys.platform == "darwin" else "so"
    (tmp_path / "lib" / f"libpython{VERSION}.{suffix}").write_bytes(b"")
    return tmp_path


def test_unset_prefix_changes_nothing() -> None:
    assert embed_link_flags(None, VERSION) is None
    assert embed_link_flags("", VERSION) is None


def test_shared_prefix_gets_relative_rpath(tmp_path: Path) -> None:
    prefix = _fake_prefix(tmp_path)
    include, flags = embed_link_flags(str(prefix), VERSION)  # type: ignore[misc]
    assert include == str(prefix / "include" / f"python{VERSION}")
    assert not any(f.startswith("-L") for f in flags)
    assert any(f.startswith(str(prefix / "lib" / f"libpython{VERSION}")) for f in flags)
    assert any(f.startswith("-Wl,-rpath,") and f.endswith("/../lib") for f in flags)


def test_static_prefix_links_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = _fake_prefix(tmp_path, static=True)
    monkeypatch.setenv("PYSC_EXPERIMENTAL_EMBED_LINK", "static")
    _, flags = embed_link_flags(str(prefix), VERSION)  # type: ignore[misc]
    assert str(prefix / "lib" / f"libpython{VERSION}.a") in flags
    assert not any(f.startswith("-lpython") for f in flags)


def test_bad_prefix_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PYSC_EXPERIMENTAL_EMBED_PREFIX"):
        embed_link_flags(str(tmp_path), VERSION)


def test_default_island_link_is_the_host_libpython(tmp_path: Path) -> None:
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYSC_EXPERIMENTAL")}
    done = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", "--island", str(src), "--emit", "bin", "-o", "prog"]
        + ["--debug-dir", "dbg"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    link = (tmp_path / "dbg" / "cc.sh").read_text(encoding="utf-8").splitlines()[-1]
    assert f"-lpython{VERSION}" in link
    assert "@executable_path" not in link and "$ORIGIN" not in link


def _probe(reference: list[str], tmp_path: Path) -> dict:
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps(reference), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(MANIFEST), "probe", "--reference", str(ref)], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_probe_describes_this_engine(tmp_path: Path) -> None:
    manifest = _probe(["json", "os", "no_such_stdlib_module_xyz"], tmp_path)
    assert manifest["schema"] == 1
    assert manifest["engine"]["name"] == "cpython"
    assert manifest["engine"]["language_level"] == "{}.{}".format(*sys.version_info[:2])
    assert "json" in manifest["stdlib"]["available"]
    assert manifest["stdlib"]["missing"] == ["no_such_stdlib_module_xyz"]
    assert manifest["features"]["walrus_pep572"] is True


def test_check_reports_imports_the_engine_lacks(tmp_path: Path) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "engine": {"name": "pypy", "version": "3.11.17"},
                "stdlib": {"available": ["json"], "missing": ["annotationlib"]},
                "features": {},
            }
        ),
        encoding="utf-8",
    )
    prog = tmp_path / "p.py"
    prog.write_text("import json\nimport annotationlib\nimport requests\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(MANIFEST), "check", str(manifest), str(prog)], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 1
    assert "p.py:2: `annotationlib` is not available on pypy 3.11.17" in done.stdout
    assert "json" not in done.stdout and "requests" not in done.stdout


@pytest.mark.skipif(not os.environ.get("PYSC_EMBED_POC_PREFIX"), reason="needs a python-build-standalone install/ tree")
def test_bundle_runs_without_system_python(tmp_path: Path) -> None:
    prefix = os.environ["PYSC_EMBED_POC_PREFIX"]
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    out = tmp_path / "bundle"
    done = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "embed_poc.py"),
            "--prefix",
            prefix,
            "--trim-by-run",
            str(src),
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    # An empty environment: no PATH, no PYTHONHOME, no VIRTUAL_ENV.
    run = subprocess.run([str(out / "bin" / "prog")], env={}, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "11/12\n"
