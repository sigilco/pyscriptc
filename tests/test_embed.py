"""The island runtime pack and the `--embed-python` seam.

The default link must not change: with no mode set (or `host`, the resolved
`auto` on a normal interpreter) the island binary links the host libpython
exactly as before, byte for byte. The bundle link is tested against a fake
pack tree; the real fetch is network-dependent and only runs when
`PYSC_EMBED_POC_PREFIX` points at a python-build-standalone `install/` tree
(see .agents/plans/2026-10-25-embedding-libpython.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from pyscriptc import embed

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "engine_manifest.py"
VERSION = sysconfig.get_config_var("VERSION") or "3.14"
LOCK = ROOT / "packaging" / "runtime.lock.toml"

ISLAND_SRC = """import fractions

def main() -> None:
    f = fractions.Fraction(3, 4)
    s: str = str(f + fractions.Fraction(1, 6))
    print(s)

if __name__ == "__main__":
    main()
"""


def _fake_pack(tmp_path: Path) -> Path:
    """A minimal unpacked pack: include/, lib/libpython, lib/pythonX.Y/."""
    (tmp_path / "include" / f"python{VERSION}").mkdir(parents=True)
    libdir = tmp_path / "lib"
    libdir.mkdir()
    if sys.platform == "darwin":
        (libdir / f"libpython{VERSION}.dylib").write_bytes(b"")
    else:
        (libdir / f"libpython{VERSION}.so.1.0").write_bytes(b"")
        os.symlink(f"libpython{VERSION}.so.1.0", libdir / f"libpython{VERSION}.so")
    stdlib = libdir / f"python{VERSION}"
    stdlib.mkdir()
    (stdlib / "os.py").write_text("", encoding="utf-8")
    (stdlib / "lib-dynload").mkdir()
    return tmp_path


def test_lock_pins_the_two_slice_platforms() -> None:
    data = embed.read_lock(LOCK)
    assert data["schema"] == 1
    assert data["cpython"].startswith("3.14.")
    platforms = set(data["runtime"][data["cpython"]])
    assert {"macos-arm64", "linux-x86_64"} <= platforms


def test_lock_pack_resolves_by_platform() -> None:
    pack = embed.lock_pack("macos-arm64", path=LOCK)
    assert pack.platform == "macos-arm64"
    assert pack.pack_id.startswith("cp314")
    assert pack.asset_url.startswith("https://github.com/astral-sh/python-build-standalone/releases/download/")
    assert len(pack.sha256) == 64


def test_lock_pack_refuses_an_unlocked_platform() -> None:
    with pytest.raises(embed.EmbedError, match="no runtime pack"):
        embed.lock_pack("windows-x86_64", path=LOCK)


def test_sha256_mismatch_is_refused(tmp_path: Path) -> None:
    blob = tmp_path / "asset.bin"
    blob.write_bytes(b"a flipped byte")
    with pytest.raises(embed.EmbedError, match="sha256 mismatch"):
        embed.verify_sha256(blob, "0" * 64)


def test_bundle_link_flags_use_the_relative_rpath(tmp_path: Path) -> None:
    pack = _fake_pack(tmp_path)
    include, flags = embed.link_flags(pack, VERSION)
    assert include == str(pack / "include" / f"python{VERSION}")
    assert not any(flag.startswith("-L") for flag in flags)
    assert any(str(pack / "lib") in flag for flag in flags)
    rpaths = [flag for flag in flags if flag.startswith("-Wl,-rpath,")]
    assert rpaths and all("/../lib" in flag for flag in rpaths)
    assert all("$" not in flag or "$ORIGIN" in flag for flag in flags)


def test_bundle_link_flags_carry_the_elf_extras(tmp_path: Path) -> None:
    pack = _fake_pack(tmp_path)
    _, flags = embed.link_flags(pack, VERSION)
    if sys.platform != "darwin":
        for extra in ("-ldl", "-lm", "-lpthread", "-lutil"):
            assert extra in flags
    else:
        # macOS gets those from the system; the line carries no -l extras.
        assert not any(flag in flags for flag in ("-ldl", "-lpthread", "-lutil"))


def test_resolve_mode_auto_picks_host_on_a_usable_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_RUNTIME_DIR", raising=False)
    assert embed.resolve_mode(None) in ("host", "bundle")
    assert embed.resolve_mode("host") == "host"
    assert embed.resolve_mode("bundle") == "bundle"
    assert embed.resolve_mode("auto") == ("host" if embed.host_has_shared_libpython() else "bundle")


def test_resolve_mode_refuses_an_unknown_mode() -> None:
    with pytest.raises(embed.EmbedError, match="--embed-python"):
        embed.resolve_mode("static")


def test_host_has_shared_libpython_here() -> None:
    # A dev checkout or a wheel venv has one; the assertion documents which
    # side of `auto` this interpreter is on rather than which it should be.
    assert isinstance(embed.host_has_shared_libpython(), bool)


def test_pysc_runtime_dir_is_used_as_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack_tree = _fake_pack(tmp_path)
    monkeypatch.setenv("PYSC_RUNTIME_DIR", str(pack_tree))
    pack = embed.lock_pack(path=LOCK)
    assert embed.pack_dir(pack) == pack_tree
    assert embed.pack_is_ready(pack)
    # fetch is a no-op against the override and never touches the network
    assert embed.fetch(pack) == pack_tree


def test_pysc_runtime_dir_missing_pack_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PYSC_RUNTIME_DIR", str(empty))
    pack = embed.lock_pack(path=LOCK)
    with pytest.raises(embed.EmbedError, match="PYSC_RUNTIME_DIR"):
        embed.fetch(pack)


def test_stage_bundle_writes_bin_and_lib(tmp_path: Path) -> None:
    pack = _fake_pack(tmp_path / "pack")
    (tmp_path / "work").mkdir()
    binary = tmp_path / "work" / "prog"
    binary.write_bytes(b"\xcf\xfa\xed\xfe")
    staged = embed.stage_bundle(tmp_path / "work" / "prog", pack, VERSION, binary)
    assert staged == tmp_path / "work" / "prog" / "bin" / "prog"
    assert staged.is_file()
    libs = list((tmp_path / "work" / "prog" / "lib").glob(f"libpython{VERSION}*"))
    assert libs
    assert (tmp_path / "work" / "prog" / "lib" / f"python{VERSION}" / "os.py").is_file()


def test_default_island_link_is_the_host_libpython(tmp_path: Path) -> None:
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYSC_RUNTIME_DIR"}
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
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            "--island",
            "--embed-python=bundle",
            str(src),
            "--emit",
            "bin",
            "-o",
            "bundle/prog",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYSC_RUNTIME_DIR": os.environ["PYSC_EMBED_POC_PREFIX"]},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    # An empty environment: no PATH, no PYTHONHOME, no VIRTUAL_ENV.
    run = subprocess.run(
        [str(tmp_path / "bundle" / "prog" / "bin" / "prog")], env={}, capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "11/12\n"
