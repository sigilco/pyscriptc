"""The PyPy runtime pack and the PyPy island engine (issue #668).

Three layers, each with its own cost:

- the **unit** layer needs no network and no pack: the lock rows parse, the
  descriptor selects the pack route, the engine gate refuses the syntax a
  3.11 engine does not have, and the link flags carry the shape the pack
  needs;
- the **pack** layer needs the pinned PyPy pack (fetched or unpacked): the
  library, the include directory and the staged bundle are where the pack
  says they are;
- the **acceptance** layer needs the pack and a toolchain: the fractions
  program compiles against the PyPy headers, runs from an **empty
  environment** after the bundle is relocated, and prints what CPython
  prints.

The acceptance layer is the one the issue names ("the fractions program
runs from an empty environment on Linux x86_64 and macOS arm64 with output
equal to CPython's"). It is skipped when the pack is not on the machine, so
the suite stays green on a machine without a PyPy SDK.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import embed, engine_gate, island_engine

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging" / "runtime.lock.toml"
FIXTURES = ROOT / "tests" / "fixtures" / "engines"
PYPY_DESCRIPTOR = FIXTURES / "pypy.toml"
PYPY_MANIFEST = FIXTURES / "pypy-manifest.json"

FRACTIONS_PROGRAM = """\
import fractions


def main() -> None:
    f = fractions.Fraction(3, 4)
    s: str = str(f + fractions.Fraction(1, 6))
    print(s)


if __name__ == "__main__":
    main()
"""


def _pypy_pack_available() -> bool:
    """Whether the pinned PyPy pack is on this machine."""
    try:
        pack = embed.lock_pypy_pack()
    except embed.EmbedError:
        return False
    return embed.pypy_pack_is_ready(pack)


def _compile(
    tmp_path: Path,
    source_text: str,
    out: Path,
    *,
    engine: Path | None = PYPY_DESCRIPTOR,
    island: bool = True,
    imports: str | None = '[imports]\nfractions = "island"\n',
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    """Compile `source_text` into `out`; the finished process is returned.

    The compile runs in `tmp_path` so the project config it reads
    (`pyscriptc.toml`) is the one the test wrote, not the repository's.
    """
    source = tmp_path / "prog.py"
    source.write_text(source_text, encoding="utf-8")
    if imports is not None:
        (tmp_path / "pyscriptc.toml").write_text(imports, encoding="utf-8")
    argv = [sys.executable, "-m", "pyscriptc", "compile"]
    if island:
        argv.append("--island")
    if engine is not None:
        argv += ["--island-engine", str(engine)]
    argv += ["--emit", "bin", str(source), "-o", str(out)]
    return subprocess.run(
        argv,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_empty_env(binary: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run `binary` with nothing in the environment."""
    return subprocess.run(
        [str(binary)],
        env={},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --------------------------------------------------------------------------
# The lock rows
# --------------------------------------------------------------------------


def test_the_lock_pins_the_two_slice_platforms() -> None:
    table = embed.read_pypy_lock(LOCK)
    assert table["schema"] == 1
    releases = table["releases"]
    assert set(releases) == {"linux-x86_64", "macos-arm64"}
    for row in releases.values():
        assert row["version"].startswith("7.3.")
        assert row["python"] == "3.11"
        assert row["asset"].startswith("pypy3.11-")
        assert len(row["sha256"]) == 64


def test_lock_pypy_pack_resolves_by_platform() -> None:
    pack = embed.lock_pypy_pack("linux-x86_64", LOCK)
    assert pack.platform == "linux-x86_64"
    assert pack.version == "7.3.23"
    assert pack.python == "3.11"
    assert pack.asset_url.endswith(pack.asset)
    assert pack.pack_id.startswith("pypy7.3.23-")


def test_lock_pypy_pack_refuses_an_unlocked_platform() -> None:
    with pytest.raises(embed.EmbedError, match="no pypy runtime pack for linux-aarch64"):
        embed.lock_pypy_pack("linux-aarch64", LOCK)


def test_the_lock_assets_are_the_official_pypy_releases() -> None:
    """The asset URL points at downloads.python.org, not at a mirror."""
    pack = embed.lock_pypy_pack("linux-x86_64", LOCK)
    assert pack.asset_url == f"https://downloads.python.org/pypy/{pack.asset}"


# --------------------------------------------------------------------------
# The descriptor: the pack route
# --------------------------------------------------------------------------


def test_the_fixture_descriptor_names_the_pack() -> None:
    descriptor = island_engine.load_file(PYPY_DESCRIPTOR)
    assert descriptor.kind in island_engine.PYPY_KINDS
    assert island_engine.uses_pack(descriptor)
    assert island_engine.engine_pack_name(descriptor) == "pypy"
    assert descriptor.manifest == "pypy-manifest.json"


def test_a_pack_descriptor_needs_no_include_entry() -> None:
    """The pack supplies the include directory; the descriptor need not."""
    defines = island_engine.engine_defines(island_engine.load_file(PYPY_DESCRIPTOR))
    assert defines == ["-DPS_ENGINE_PYPY"]


def test_an_include_descriptor_still_works() -> None:
    """The by-hand route (the descriptor owns its headers) is unchanged."""
    text = PYPY_DESCRIPTOR.read_text(encoding="utf-8").replace('pack = "pypy"', 'include = "include"')
    path = Path("/tmp") / "pypy-include.toml"
    path.write_text(text, encoding="utf-8")
    try:
        descriptor = island_engine.load_file(path)
        assert not island_engine.uses_pack(descriptor)
        defines = island_engine.engine_defines(descriptor)
        assert defines[0] == "-DPS_ENGINE_PYPY"
        assert Path(defines[2]) == path.parent / "include"
    finally:
        path.unlink()


def test_an_unknown_pack_name_is_refused() -> None:
    text = PYPY_DESCRIPTOR.read_text(encoding="utf-8").replace('pack = "pypy"', 'pack = "graalpy"')
    path = Path("/tmp") / "pypy-bad.toml"
    path.write_text(text, encoding="utf-8")
    try:
        descriptor = island_engine.load_file(path)
        with pytest.raises(island_engine.EngineError, match="unknown runtime pack"):
            island_engine.engine_pack_name(descriptor)
    finally:
        path.unlink()


def test_a_pypy_descriptor_without_a_pack_or_an_include_is_refused() -> None:
    text = PYPY_DESCRIPTOR.read_text(encoding="utf-8").replace('pack = "pypy"\n', "")
    path = Path("/tmp") / "pypy-bare.toml"
    path.write_text(text, encoding="utf-8")
    try:
        descriptor = island_engine.load_file(path)
        with pytest.raises(island_engine.EngineError, match="abi include"):
            island_engine.engine_defines(descriptor)
    finally:
        path.unlink()


# --------------------------------------------------------------------------
# The language gate
# --------------------------------------------------------------------------


def test_the_level_is_read_from_the_manifest() -> None:
    from pyscriptc import engine_manifest

    descriptor = island_engine.load_file(PYPY_DESCRIPTOR)
    manifest = engine_manifest.load_manifest_file(PYPY_MANIFEST)
    level = engine_gate.engine_level(descriptor, manifest)
    assert level is not None
    assert level.text == "3.11"
    assert level.feature_version == (3, 11)


def test_no_manifest_leaves_the_gate_open() -> None:
    assert engine_gate.engine_level(island_engine.builtin_descriptor(), None) is None


def test_the_gate_refuses_the_syntax_a_3_11_engine_lacks() -> None:
    """PEP 695, 750 and 758 constructs are refused at the 3.11 level."""
    level = engine_gate.LanguageLevel(3, 11)
    for source in (
        "def f[T](x: T) -> T:\n    return x\n",
        "x = 1\nt = t'{x}'\n",
        "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n",
    ):
        with pytest.raises(engine_gate.GateError) as caught:
            engine_gate.check_source(source, level)
        assert "3.11" in caught.value.message


def test_the_gate_allows_the_syntax_a_3_11_engine_has() -> None:
    level = engine_gate.LanguageLevel(3, 11)
    for source in (
        "def f(x: int) -> int:\n    return x\n",
        "x = 1\nprint(f'{x}')\n",
        "try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n",
        "match 1:\n    case 1:\n        pass\n",
    ):
        engine_gate.check_source(source, level)


def test_the_gate_is_closed_at_the_compiler_level() -> None:
    """At 3.14 the compiler's own syntax parses: the gate only lowers."""
    level = engine_gate.LanguageLevel(3, 14)
    engine_gate.check_source("x = 1\nt = t'{x}'\n", level)


def test_the_manifest_declares_the_features_the_engine_lacks() -> None:
    """The fixture manifest is the engine's own claim; the gate reads it."""
    data = json.loads(PYPY_MANIFEST.read_text(encoding="utf-8"))
    features = data["features"]
    assert features["type_params_pep695"] is False
    assert features["tstring_pep750"] is False
    assert features["except_no_parens_pep758"] is False
    assert features["deferred_annotations_pep649"] is False


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_manifest_is_a_genuine_probe_output(tmp_path: Path) -> None:
    """The fixture manifest is what the engine itself reports.

    `scripts/engine_manifest.py probe` runs inside the engine; the
    fixture must be its output (the features and the identity; the
    stdlib list is probed against the compiler's own 3.14 reference list,
    so it is regenerated with `--reference`). A hand-edited manifest
    would describe an engine nobody runs.
    """
    engine_bin = embed.pypy_pack_dir(embed.lock_pypy_pack()) / "bin"
    pypy = next((p for p in sorted(engine_bin.glob("pypy3*")) if p.is_file() and os.access(p, os.X_OK)), None)
    if pypy is None:
        pytest.skip("the pack carries no interpreter binary")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "engine_manifest.py"), "refs"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    ref_path = tmp_path / "reference.json"
    ref_path.write_text(done.stdout, encoding="utf-8")
    probed = subprocess.run(
        [str(pypy), str(ROOT / "scripts" / "engine_manifest.py"), "probe", "--reference", str(ref_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert probed.returncode == 0, probed.stderr
    got = json.loads(probed.stdout)
    want = json.loads(PYPY_MANIFEST.read_text(encoding="utf-8"))
    assert got["engine"] == want["engine"]
    assert got["features"] == want["features"]
    assert got["stdlib"] == want["stdlib"]


# --------------------------------------------------------------------------
# The pack layer
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_pack_carries_the_library_and_the_headers() -> None:
    pack = embed.lock_pypy_pack()
    directory = embed.pypy_pack_dir(pack)
    shared = embed.pypy_library(directory, pack.python)
    assert shared is not None
    assert shared.name == f"libpypy{pack.python}-c.so" or shared.name.endswith(".dylib")
    include = directory / "include" / f"pypy{pack.python}"
    assert include.is_dir()
    assert (include / "Python.h").is_file()


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_link_flags_name_the_library_by_soname() -> None:
    """A path-named entry would land in NEEDED verbatim and break a move."""
    pack = embed.lock_pypy_pack()
    directory = embed.pypy_pack_dir(pack)
    include, flags = embed.pypy_link_flags(directory, pack.python)
    assert Path(include) == directory / "include" / f"pypy{pack.python}"
    assert f"-lpypy{pack.python}-c" in flags
    assert any(flag.startswith("-L") for flag in flags)
    rpath = [flag for flag in flags if flag.startswith("-Wl,-rpath")]
    assert len(rpath) == 1
    assert "$ORIGIN/../lib" in rpath[0] or "@executable_path/../lib" in rpath[0]


# --------------------------------------------------------------------------
# The acceptance layer: the fractions program, from an empty environment
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_fractions_program_runs_from_an_empty_environment(tmp_path: Path) -> None:
    """The issue's acceptance criterion, on the real pack.

    The program is compiled against the PyPy engine, the bundle is
    relocated to a fresh directory, and the run happens under `env -i`:
    no PATH, no HOME, no PYTHONHOME, nothing inherited from the compiler.
    The output must equal CPython's, byte for byte.
    """
    work = tmp_path / "work"
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    done = _compile(tmp_path, FRACTIONS_PROGRAM, work / "prog")
    assert done.returncode == 0, done.stderr
    bundle = work / "prog"
    assert (bundle / "bin" / "prog").is_file()
    # The runtime travels with the binary: the shared library and the
    # engine's stdlib tree.
    assert (bundle / "lib" / "libpypy3.11-c.so").is_file() or (bundle / "lib" / "libpypy3.11-c.dylib").is_file()
    assert (bundle / "lib" / "pypy3.11").is_dir()

    # The build tree goes away; the bundle moves.
    shutil.move(str(bundle), str(fresh / "prog"))
    shutil.rmtree(work)

    got = _run_empty_env(fresh / "prog" / "bin" / "prog")
    want = subprocess.run(
        [sys.executable, str(tmp_path / "prog.py")],
        env={},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == want.stdout == "11/12\n"
    assert got.stderr == want.stderr


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_relocated_bundle_carries_its_runtime(tmp_path: Path) -> None:
    """The bundle is relocatable: the binary names the library by soname.

    A path-named link entry would land in the `NEEDED` list verbatim and
    break the first directory move; the soname form resolves through the
    relative rpath after the move. The test reads the dynamic section of
    the compiled binary rather than the link line, because the link line
    is not what ships.
    """
    work = tmp_path / "work"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    done = _compile(tmp_path, FRACTIONS_PROGRAM, work / "prog")
    assert done.returncode == 0, done.stderr
    binary = work / "prog" / "bin" / "prog"
    needed = subprocess.run(
        ["readelf", "-d", str(binary)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if needed.returncode != 0:
        pytest.skip("readelf is not available on this host")
    assert "libpypy3.11-c.so" in needed.stdout
    assert "$ORIGIN/../lib" in needed.stdout
    # The path of the build tree is not in the binary.
    assert str(work) not in needed.stdout
    # And the moved bundle still runs.
    shutil.move(str(work / "prog"), str(fresh / "prog"))
    got = _run_empty_env(fresh / "prog" / "bin" / "prog")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "11/12\n"


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_differential_probe_matches_cpython(tmp_path: Path) -> None:
    """The committed differential probe runs the same on both engines.

    `probes/differential/pypy_engine_fractions.py` is the issue's probe
    (the differential probe rule): the compiled program on the PyPy pack
    must print what the pinned CPython prints, byte for byte, on stdout,
    stderr and the exit code.
    """
    probe = ROOT / "probes" / "differential" / "pypy_engine_fractions.py"
    source = probe.read_text(encoding="utf-8")
    work = tmp_path / "work"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    done = _compile(tmp_path, source, work / "prog")
    assert done.returncode == 0, done.stderr
    shutil.move(str(work / "prog"), str(fresh / "prog"))
    got = _run_empty_env(fresh / "prog" / "bin" / "prog")
    want = subprocess.run(
        [sys.executable, str(probe)],
        env={},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert got.returncode == want.returncode == 0, got.stderr
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_a_native_thread_reaches_the_engine(tmp_path: Path) -> None:
    """A foreign thread registers with the engine before it runs Python.

    Under PyPy that registration is `pypy_thread_attach` (the GIL
    discipline the seam implements): a thread the engine did not create
    must attach itself, or the first Python call on it misbehaves. The
    program starts four native threads, each calling into the island.
    """
    program = """\
import fractions
import threading

errors: int = 0
lock: threading.Lock = threading.Lock()


def worker() -> None:
    global errors
    i: int = 0
    while i < 4:
        f = fractions.Fraction(i, 7)
        if f.numerator * 7 != f.denominator * i:
            lock.acquire()
            errors = errors + 1
            lock.release()
        i = i + 1


def main() -> None:
    t1: threading.Thread = threading.Thread(target=worker)
    t2: threading.Thread = threading.Thread(target=worker)
    t3: threading.Thread = threading.Thread(target=worker)
    t4: threading.Thread = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t1.join()
    t2.join()
    t3.join()
    t4.join()
    print("errors:", errors)


if __name__ == "__main__":
    main()
"""
    work = tmp_path / "work"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    done = _compile(tmp_path, program, work / "prog")
    assert done.returncode == 0, done.stderr
    shutil.move(str(work / "prog"), str(fresh / "prog"))
    got = _run_empty_env(fresh / "prog" / "bin" / "prog")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "errors: 0\n"


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_gate_refuses_a_3_14_construct_at_compile_time(tmp_path: Path) -> None:
    """An unsupported construct is a compile-time diagnostic, not a run-time
    syntax error the user never sees."""
    done = _compile(
        tmp_path,
        "def f[T](x: T) -> T:\n    return x\n\n\nprint(f(1))\n",
        tmp_path / "prog",
        imports=None,
        timeout=300,
    )
    assert done.returncode != 0
    assert "PS4303" in done.stderr
    assert "3.11" in done.stderr


@pytest.mark.skipif(not _pypy_pack_available(), reason="needs the pinned PyPy pack")
def test_the_default_path_is_unchanged_by_the_pypy_pack(tmp_path: Path) -> None:
    """Without an engine selection the compile links the host's CPython."""
    done = _compile(
        tmp_path,
        FRACTIONS_PROGRAM,
        tmp_path / "prog",
        engine=None,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr
    binary = tmp_path / "prog"
    assert binary.is_file()
    run = _run_empty_env(binary)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "11/12\n"


def test_the_cache_root_is_not_shared_with_the_cpython_pack() -> None:
    """The two packs live side by side in the cache, never on top of each other."""
    cpython = embed.lock_pack()
    pypy = embed.lock_pypy_pack("linux-x86_64", LOCK)
    assert pypy.pack_id != cpython.pack_id
    assert embed.pypy_pack_dir(pypy) != embed.pack_dir(cpython)


def test_the_pypy_pack_is_not_the_cpython_runtime_dir() -> None:
    """`PYSC_RUNTIME_DIR` names one pack; the PyPy route looks for its own."""
    assert embed.pypy_pack_dir(embed.lock_pypy_pack("linux-x86_64", LOCK), Path("/tmp/x")).name.startswith("pypy")


def test_the_environment_variable_does_not_leak_into_the_pack_id() -> None:
    """The pack id is the lock's, not the host's: two hosts share a cache."""
    pack = embed.lock_pypy_pack("linux-x86_64", LOCK)
    assert "linux-x86_64" in pack.pack_id
    assert os.sep not in pack.pack_id
