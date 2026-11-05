"""Cheap PR-time checks for the release-only steps.

The release matrix takes about 20 minutes and its auxiliary steps (the version
stamp, the runtime pack builder) only run there. Three rehearsal failures for
v0.8.0 came from these steps (a SHA read as a version, a pwsh-only default
shell, a keyword argument the typed embed module no longer takes), none of
them from the compiler. These tests run the same steps in seconds.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
PACK_SCRIPT = ROOT / "scripts" / "build_runtime_pack.py"


def _stamp_steps() -> list[tuple[str, str]]:
    """(shell, run script) of every `Stamp release version` step in release.yml."""
    lines = RELEASE_YML.read_text(encoding="utf-8").splitlines()
    found: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if line.strip() != "- name: Stamp release version":
            continue
        indent = len(line) - len(line.lstrip()) + 2
        shell = ""
        script: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            current = lines[cursor]
            if current.strip() and len(current) - len(current.lstrip()) < indent:
                break
            stripped = current.strip()
            if stripped.startswith("shell:"):
                shell = stripped.split(":", 1)[1].strip()
            if stripped == "run: |":
                cursor += 1
                while cursor < len(lines) and (not lines[cursor].strip() or len(lines[cursor]) - len(lines[cursor].lstrip()) > indent):
                    script.append(lines[cursor])
                    cursor += 1
                continue
            cursor += 1
        found.append((shell, textwrap.dedent("\n".join(script))))
    return found


def test_release_workflow_has_stamp_steps() -> None:
    assert len(_stamp_steps()) == 2


@pytest.mark.parametrize(
    ("ref", "expected"),
    [("v0.8.0", "0.8.0"), ("v0.9.0-beta.1", "0.9.0-beta.1"), ("", "9.9.9")],
)
def test_stamp_step_names_the_version_for_a_tag_or_a_dispatch(tmp_path: Path, ref: str, expected: str) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "9.9.9"\n', encoding="utf-8")
    (tmp_path / "src" / "pyscriptc").mkdir(parents=True)
    for shell, script in _stamp_steps():
        # The matrix includes Windows, whose default shell is pwsh: the step must pick bash.
        assert shell == "bash"
        done = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "PYSC_RELEASE_VERSION": ref},
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        stamped = (tmp_path / "src" / "pyscriptc" / "_release.py").read_text(encoding="utf-8")
        assert stamped == f'VERSION = "{expected}"\n'


def test_runtime_pack_script_builds_from_a_synthetic_asset(tmp_path: Path) -> None:
    """The pack builder runs end to end on a tiny fake PBS asset, with the share/ trim."""
    version = ".".join(map(str, sys.version_info[:2]))
    payload = tmp_path / "payload" / "python"
    (payload / "include").mkdir(parents=True)
    (payload / "include" / "Python.h").write_text("", encoding="utf-8")
    (payload / "lib" / f"python{version}").mkdir(parents=True)
    (payload / "lib" / f"python{version}" / "os.py").write_text("", encoding="utf-8")
    (payload / "lib" / f"python{version}" / "LICENSE.txt").write_text("license", encoding="utf-8")
    (payload / "lib" / "libpython3.so").write_text("", encoding="utf-8")
    (payload / "bin").mkdir()
    (payload / "bin" / f"python{version}").write_text("", encoding="utf-8")
    (payload / "share" / "terminfo").mkdir(parents=True)
    (payload / "share" / "terminfo" / "x").write_text("", encoding="utf-8")

    asset = tmp_path / "cpython-fake.tar.gz"
    with tarfile.open(asset, "w:gz") as tar:
        tar.add(payload, arcname="python")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    lock = tmp_path / "runtime.lock.toml"
    lock.write_text(
        f'schema = 1\nrelease = "1"\ncpython = "{version}.0"\n\n[runtime."{version}.0".linux-x86_64]\n'
        f'pbs_asset = "{asset.name}"\nsha256 = "{digest}"\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"
    done = subprocess.run(
        [sys.executable, str(PACK_SCRIPT), "--platform", "linux-x86_64", "--lock", str(lock), "--asset", str(asset), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    packs = list(out.glob("pyscriptc-runtime-*-linux-x86_64.tar.zst"))
    assert len(packs) == 1
    with tarfile.open(packs[0], "r:zst") as tar:
        names = tar.getnames()
    assert "include/Python.h" in names
    assert "lib/libpython3.so" in names
    assert "LICENSE.txt" in names
    assert not [name for name in names if "share" in name]
