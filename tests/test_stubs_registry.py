"""Stub discovery, loading and the typed-signature registry (partial slice, not wired into lowering)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pyscriptc.sigregistry import LoweringSignature, Refusal, SignatureRegistry, kind_accepts
from pyscriptc.stdlib_queue import WAVE_FUNCTIONS
from pyscriptc.stubs import STDLIB_SUBSET, discover_roots, find_stub, load_stub, stdlib_available


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    main = _write(tmp_path / "app" / "main.py", "import util\n")
    _write(tmp_path / "app" / "util.py", "def scale(x, k=2):\n    return x * k\n")
    _write(tmp_path / "app" / "util.pyi", "def scale(x: int, k: int = 2) -> int: ...\n")
    _write(tmp_path / "app" / "stubs" / "vendorlib" / "__init__.pyi", "def ping(host: str) -> bool: ...\n")
    return main


def _registry(source: Path | None = None, **kwargs: object) -> SignatureRegistry:
    return SignatureRegistry.for_source(source, version=(3, 14), platform="linux", **kwargs)  # type: ignore[arg-type]


def test_roots_follow_precedence_order(project: Path, tmp_path: Path) -> None:
    explicit = tmp_path / "extra"
    explicit.mkdir()
    kinds = [root.kind for root in discover_roots(project, [explicit])]
    assert kinds == ["explicit", "beside", "stubs-dir", "typeshed"]


def test_beside_and_stubs_dir_are_found(project: Path) -> None:
    reg = _registry(project)
    util = reg.module("util")
    assert util is not None and util.origin == "beside"
    assert reg.module("vendorlib").origin == "stubs-dir"  # type: ignore[union-attr]
    spec = reg.lowering("util", "scale")
    assert isinstance(spec, LoweringSignature)
    assert spec.spec() == {
        "params": [{"kind": "int"}, {"kind": "int"}],
        "return": {"kind": "int"},
        "min": 1,
        "defaults": ["2"],
        "names": ["x", "k"],
    }


def test_explicit_path_beats_beside(project: Path, tmp_path: Path) -> None:
    _write(tmp_path / "override" / "util.pyi", "def scale(x: float) -> float: ...\n")
    reg = _registry(project, stub_paths=[tmp_path / "override"])
    sig = reg.lowering("util", "scale")
    assert isinstance(sig, LoweringSignature)
    assert sig.origin == "explicit" and sig.returns == {"kind": "float"}


def test_user_stub_cannot_retype_stdlib(project: Path) -> None:
    _write(project.parent / "json.pyi", "def loads(s: str) -> int: ...\n")
    found = find_stub("json", discover_roots(project))
    assert found is not None and found[0].kind == "typeshed"


def test_typeshed_subset_and_versions() -> None:
    assert "tomllib" not in STDLIB_SUBSET
    assert _registry().module("tomllib") is None
    assert _registry(stdlib_allow=None).module("tomllib") is not None
    assert stdlib_available("tomllib", (3, 11)) and not stdlib_available("tomllib", (3, 10))


def test_no_typeshed_root() -> None:
    assert _registry(typeshed=None).module("json") is None


def test_version_and_platform_branches(tmp_path: Path) -> None:
    stub = _write(
        tmp_path / "m.pyi",
        """
        import sys
        if sys.version_info >= (3, 13):
            def f() -> int: ...
        else:
            def f() -> str: ...
        if sys.platform == "win32":
            def g() -> bytes: ...
        else:
            def g() -> float: ...
        if sys.version_info >= (3, 13) and sys.platform != "win32":
            def h() -> int: ...
        """,
    )
    new = load_stub("m", stub, "beside", version=(3, 14), platform="linux")
    old = load_stub("m", stub, "beside", version=(3, 12), platform="win32")
    assert new.functions["f"].callable.return_type.kind == "int"  # type: ignore[union-attr]
    assert old.functions["f"].callable.return_type.kind == "str"  # type: ignore[union-attr]
    assert new.functions["g"].callable.return_type.kind == "float"  # type: ignore[union-attr]
    assert old.functions["g"].callable.return_type.kind == "bytes"  # type: ignore[union-attr]
    assert "h" in new.functions and "h" not in old.functions


def test_type_ignore_and_any(tmp_path: Path) -> None:
    _write(
        tmp_path / "loose.pyi",
        """
        from typing import Any
        def fine(x: int) -> int: ...
        def skipped(x: int) -> int: ...  # type: ignore
        def anyarg(x: Any) -> int: ...
        def bare(x) -> int: ...
        def __getattr__(name: str) -> Any: ...
        """,
    )
    _write(tmp_path / "whole.pyi", "# type: ignore\ndef fine(x: int) -> int: ...\n")
    reg = SignatureRegistry(discover_roots(None, [tmp_path], typeshed=None))
    assert isinstance(reg.lowering("loose", "fine"), LoweringSignature)
    assert reg.lowering("loose", "skipped").reason == "ignored"  # type: ignore[union-attr]
    assert reg.lowering("loose", "anyarg").reason == "any"  # type: ignore[union-attr]
    assert reg.lowering("loose", "bare").reason == "unannotated"  # type: ignore[union-attr]
    assert reg.lowering("loose", "missing").reason == "incomplete-stub"  # type: ignore[union-attr]
    assert reg.lowering("whole", "fine").reason == "ignored"  # type: ignore[union-attr]
    assert reg.lowering("absent", "f").reason == "no-stub"  # type: ignore[union-attr]


def test_reexports_star_and_aliases(tmp_path: Path) -> None:
    _write(
        tmp_path / "pkg" / "__init__.pyi",
        """
        from ._impl import run as run
        from ._types import Num
        from .more import *
        def twice(x: Num) -> Num: ...
        """,
    )
    _write(tmp_path / "pkg" / "_impl.pyi", "def run(cmd: str, /) -> int: ...\n")
    _write(tmp_path / "pkg" / "_types.pyi", "from typing import TypeAlias\nNum: TypeAlias = int\n")
    _write(tmp_path / "pkg" / "more.pyi", "def extra(flag: bool) -> list[str]: ...\n")
    reg = SignatureRegistry(discover_roots(None, [tmp_path], typeshed=None))
    run = reg.signature("pkg", "run")
    assert run is not None and run.module == "pkg._impl" and run.callable.positional_only == 1  # type: ignore[union-attr]
    twice = reg.lowering("pkg", "twice")
    assert isinstance(twice, LoweringSignature) and twice.returns == {"kind": "int"}
    extra = reg.lowering("pkg", "extra")
    assert isinstance(extra, LoweringSignature) and extra.returns == {"kind": "list", "elem": {"kind": "str"}}


def test_methods_drop_receiver_and_map_self(tmp_path: Path) -> None:
    _write(
        tmp_path / "shapes.pyi",
        """
        from typing import Self
        class Box:
            def grow(self, by: int) -> Self: ...
            @staticmethod
            def unit(size: float) -> float: ...
            @classmethod
            def make(cls, side: int) -> Self: ...
        _box: Box
        grow = _box.grow
        """,
    )
    stub = load_stub("shapes", tmp_path / "shapes.pyi", "beside")
    grow = stub.functions["Box.grow"]
    assert grow.method and [p.name for p in grow.callable.parameters] == ["by"]  # type: ignore[union-attr]
    assert grow.callable.return_type.kind == "Box"  # type: ignore[union-attr]
    assert [p.name for p in stub.functions["Box.unit"].callable.parameters] == ["size"]  # type: ignore[union-attr]
    assert [p.name for p in stub.functions["Box.make"].callable.parameters] == ["side"]  # type: ignore[union-attr]
    assert stub.functions["grow"].overloads == grow.overloads


def test_overloads_pick_by_argument_kind() -> None:
    reg = _registry()
    assert reg.lowering("os", "listdir").reason == "overload-ambiguous"  # type: ignore[union-attr]
    listdir = reg.lowering("os", "listdir", ["str"])
    assert isinstance(listdir, LoweringSignature)
    assert listdir.returns == {"kind": "list", "elem": {"kind": "str"}}
    assert reg.lowering("os.path", "exists").reason == "union-ambiguous"  # type: ignore[union-attr]
    assert isinstance(reg.lowering("os.path", "exists", ["str"]), LoweringSignature)


def test_bundled_signatures_resolve_through_reexports() -> None:
    reg = _registry()
    assert reg.signature("hashlib", "sha256").module == "_hashlib"  # type: ignore[union-attr]
    randint = reg.lowering("random", "randint")
    assert isinstance(randint, LoweringSignature) and randint.params == ({"kind": "int"}, {"kind": "int"})
    assert reg.lowering("shutil", "which", ["str"]).reason == "union-return"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, rows in WAVE_FUNCTIONS.items() for name in rows],
)
def test_registry_agrees_with_closed_table(module: str, name: str) -> None:
    """Where typeshed lowers, its signature extends the hand-written row: same leading params and return."""
    row = WAVE_FUNCTIONS[module][name]
    kinds = ["bytes" if p["kind"] == "buf" else p["kind"] for p in row["params"]][: row.get("min", len(row["params"]))]
    got = _registry().lowering(module, name, kinds)
    if isinstance(got, Refusal):
        pytest.skip(f"typeshed does not lower {module}.{name}: {got.reason}")
    assert got.returns == row["return"]
    width = len(row["params"])
    assert len(got.params) >= width
    for stub_param, hand_param in zip(got.params, row["params"], strict=False):
        stub_kind, hand_kind = stub_param["kind"], hand_param["kind"]
        assert kind_accepts(stub_kind, hand_kind) or kind_accepts(hand_kind, stub_kind)
    if "names" in row:
        assert list(got.names[:width]) == row["names"]


def test_re_sub_matches_hand_row_exactly() -> None:
    got = _registry().lowering("re", "sub", ["str", "str", "str"])
    assert isinstance(got, LoweringSignature)
    assert got.spec() == WAVE_FUNCTIONS["re"]["sub"]
