"""Issue #424: only a fence in a reached module blocks the compile.

CPython runs a module body once, at the import that reaches it, and never
otherwise. A dependency module the program never imports never runs, so a
fence inside it cannot change what the compiled program does: the compile
reports it as a note and moves on. A fence the entry's import graph does
reach still refuses, with its code and the distribution that ships it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pyscriptc import cli, deps
from pyscriptc.package import lower_directory

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")


def write_tree(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def write_dep(tmp_path: Path, files: dict[str, str]) -> Path:
    """A dependency source root: one unpacked pure wheel's import root."""
    root = tmp_path / "deps"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# A dependency whose reachable part is clean and whose unreachable part
# carries a fence: the fence is what the reach rule weighs.
CLEAN = "def hello() -> str:\n    return 'hi'\n"
FENCED = "def broken(x: int) -> int:\n    return x.not_a_field\n"

DEP_FILES = {
    "dep/__init__.py": "from .core import hello\n",
    "dep/core.py": CLEAN,
    "dep/broken.py": FENCED,
}


def program(tmp_path: Path, body: str) -> Path:
    return write_tree(tmp_path, "prog", {"__main__.py": body})


# ------------------------------------------------------------ the reach rule


def test_an_unreached_dependency_fence_does_not_block_the_compile(tmp_path: Path) -> None:
    from pyscriptc.package import lower_directory as lower

    root = program(tmp_path, "import dep\n\nprint(dep.hello())\n")
    dep_root = write_dep(tmp_path, DEP_FILES)
    module, _source = lower(root, dep_roots=[dep_root])
    assert module["fences"] == []
    names = {fn["name"] for fn in module["functions"]}
    assert "dep.core.hello" in names
    assert "dep.broken.broken" not in names


def test_a_reached_dependency_fence_refuses_with_code_and_distribution(tmp_path: Path) -> None:
    root = program(tmp_path, "import dep.broken\n\nprint(dep.broken.broken(1))\n")
    dep_root = write_dep(tmp_path, {"dep/__init__.py": "VALUE = 1\n", "dep/broken.py": FENCED})
    lane_report = {"dep": {"distribution": "tiny-dep", "lane": "pure", "hint": "compile from source"}}
    module, _source = lower_directory(root, dep_roots=[dep_root], dep_lane_report=lane_report)
    assert module["fences"], "the reached fence must refuse"
    fence = module["fences"][0]
    assert fence["code"] == "PS2001"
    assert "tiny-dep" in fence["message"], fence["message"]
    assert "dep.broken" in fence["message"], fence["message"]


def test_the_pruned_module_is_reported_as_a_note(tmp_path: Path) -> None:
    root = program(tmp_path, "import dep\n\nprint(dep.hello())\n")
    dep_root = write_dep(tmp_path, DEP_FILES)
    module, _source = lower_directory(root, dep_roots=[dep_root])
    notes = module.get("_notes", [])
    assert any("dep.broken" in note for note in notes), notes


def test_a_package_body_the_entry_imports_is_reached(tmp_path: Path) -> None:
    """`import dep.sub` runs `dep`'s body first, so `dep`'s fences matter."""
    root = program(tmp_path, "import dep.sub\n\nprint(dep.sub.value())\n")
    dep_root = write_dep(
        tmp_path,
        {
            "dep/__init__.py": "from .core import broken\n",
            "dep/core.py": FENCED,
            "dep/sub.py": "def value() -> int:\n    return 1\n",
        },
    )
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"], "the fence behind the package body must refuse"


def test_a_transitive_dependency_import_reaches(tmp_path: Path) -> None:
    """`dep` imports `dep.mid`, `dep.mid` imports `dep.leaf`: all reached."""
    root = program(tmp_path, "import dep\n\nprint(dep.hello())\n")
    dep_root = write_dep(
        tmp_path,
        {
            "dep/__init__.py": "import dep.mid\n\n\ndef hello() -> str:\n    return dep.mid.hello()\n",
            "dep/mid.py": "from dep.leaf import helper\n\n\ndef hello() -> str:\n    return helper()\n",
            "dep/leaf.py": "def helper() -> str:\n    return 'hi'\n",
            "dep/broken.py": FENCED,
        },
    )
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"] == []
    names = {fn["name"] for fn in module["functions"]}
    assert {"dep.mid.hello", "dep.leaf.helper"} <= names


def test_an_import_in_an_unreached_program_module_still_binds(tmp_path: Path) -> None:
    """The program tree keeps the whole-tree rule; the dep import binds.

    The entry never imports `dead.py`, so CPython never runs its body, but
    the compile still lowers it and its `import dep` must resolve the way
    CPython's would.
    """
    root = write_tree(
        tmp_path,
        "prog",
        {
            "__main__.py": "print('main')\n",
            "dead.py": "import dep\n\n\ndef f() -> int:\n    return dep.value()\n",
        },
    )
    dep_root = write_dep(tmp_path, {"dep/__init__.py": "def value() -> int:\n    return 1\n"})
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"] == [], module["fences"]
    names = {fn["name"] for fn in module["functions"]}
    assert "dead.f" in names


def test_an_unreferenced_dependency_is_fully_pruned(tmp_path: Path) -> None:
    """Nothing imports `otherdep`: no code is lowered and the note says so."""
    root = program(tmp_path, "print('main')\n")
    dep_root = write_dep(
        tmp_path,
        {
            "dep/__init__.py": CLEAN,
            "otherdep/__init__.py": FENCED,
        },
    )
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"] == []
    names = {fn["name"] for fn in module["functions"]}
    assert not [name for name in names if name.startswith(("dep.", "otherdep."))], names
    notes = module.get("_notes", [])
    assert any("otherdep" in note for note in notes), notes


def test_a_library_package_prunes_a_dependency_nothing_imports(tmp_path: Path) -> None:
    """A library tree has no entry, so the seeds are all its modules.

    The consumer decides what to import, and the library's own modules
    never import `dep`, so CPython never runs `dep`'s body and its fence
    does not block the compile.
    """
    root = write_tree(
        tmp_path,
        "lib",
        {
            "__init__.py": "from .core import run\n",
            "core.py": "def run() -> int:\n    return 1\n",
        },
    )
    dep_root = write_dep(tmp_path, {"dep/__init__.py": FENCED})
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"] == []
    names = {fn["name"] for fn in module["functions"]}
    assert "lib.core.run" in names


def test_a_library_package_refuses_a_fence_its_imports_reach(tmp_path: Path) -> None:
    """When the library's own module imports the dep, its fence refuses."""
    root = write_tree(
        tmp_path,
        "lib",
        {
            "__init__.py": "from .core import run\n",
            "core.py": "import dep\n\n\ndef run() -> int:\n    return dep.value()\n",
        },
    )
    dep_root = write_dep(tmp_path, {"dep/__init__.py": FENCED})
    module, _source = lower_directory(root, dep_roots=[dep_root])
    assert module["fences"], "the fence behind the library's own import must refuse"


# ------------------------------------------------------------ the CPython oracle


def _wheel(files: dict[str, str]) -> bytes:
    """A minimal pure wheel with a RECORD, the shape deps.unpack expects."""
    dist = "tinydep-1.0.dist-info"
    payload: dict[str, bytes] = {name: text.encode() for name, text in files.items()}
    payload[f"{dist}/METADATA"] = b"Metadata-Version: 2.1\nName: tinydep\nVersion: 1.0\n"
    payload[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()

    rows = [f"{name},sha256={_b64(data)},{len(data)}" for name, data in payload.items()]
    rows.append(f"{dist}/RECORD,,")
    payload[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in payload.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A fake uv and wheel fetch: one pure wheel, with a fenced submodule."""
    wheel = _wheel(
        {
            "tinydep/__init__.py": "from .core import hello\n",
            "tinydep/core.py": CLEAN,
            "tinydep/broken.py": FENCED,
        }
    )
    digest = hashlib.sha256(wheel).hexdigest()
    lock = (
        'lock-version = "1.0"\ncreated-by = "uv"\n\n'
        f'[[packages]]\nname = "tinydep"\nversion = "1.0"\n'
        f'wheels = [{{ url = "https://files.example/tinydep-1.0-py3-none-any.whl", '
        f'hashes = {{ sha256 = "{digest}" }} }}]\n'
    )
    state: dict[str, Any] = {"lock": lock, "calls": [], "fetched": []}

    def run_uv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        state["calls"].append(argv)
        if argv[1:3] == ["pip", "compile"]:
            Path(argv[argv.index("-o") + 1]).write_text(state["lock"], encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    def urlopen(url: str) -> bytes:
        state["fetched"].append(url.rsplit("/", 1)[-1])
        return wheel

    monkeypatch.setattr(deps, "_run_uv", run_uv)
    monkeypatch.setattr(deps, "_urlopen", urlopen)
    monkeypatch.setenv("PYSC_UV", "uv")
    monkeypatch.setenv("PYSC_NO_TELEMETRY", "1")
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))
    return state


def _script(tmp_path: Path, body: str, name: str = "tool.py") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _pep723(body: str) -> str:
    return f'# /// script\n# requires-python = ">=3.11"\n# dependencies = ["tinydep"]\n# ///\n{body}'


def test_a_pep723_script_with_an_unreached_dependency_fence_matches_cpython(
    fake: dict[str, Any], tmp_path: Path
) -> None:
    """The differential test: the binary prints what CPython prints."""
    script = _script(tmp_path, _pep723("import tinydep\n\nprint(tinydep.hello())\n"))
    expected = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(deps.source_roots(script)[0]))},
    )
    got = cli.main(["compile", str(script), "--skip-check", "--run", "-o", str(tmp_path / "bin")])
    assert got == 0
    ran = subprocess.run([str(tmp_path / "bin")], capture_output=True, text=True)
    assert ran.stdout == expected.stdout, f"stderr: {ran.stderr}"
    assert expected.stdout == "hi\n"


def test_a_reached_dependency_fence_still_refuses_the_cli_compile(fake: dict[str, Any], tmp_path: Path) -> None:
    """The regression test: a reached fence refuses with exit 3, the pruned one does not."""
    script = _script(
        tmp_path,
        _pep723("import tinydep.broken\n\nprint(tinydep.broken.broken(1))\n"),
    )
    out = tmp_path / "reached.ir.json"
    got = cli.main(["compile", str(script), "--skip-check", "--emit", "ir", "-o", str(out)])
    assert got == 3, "the reached fence must refuse"
    module = json.loads(out.read_text(encoding="utf-8"))
    assert module["fences"], "the reached fence is a root fence"

    clean = _script(
        tmp_path,
        _pep723("import tinydep\n\nprint(tinydep.hello())\n"),
        name="clean.py",
    )
    code = cli.main(["compile", str(clean), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "clean.ir.json")])
    assert code == 0, "the unreached fence must not refuse"
