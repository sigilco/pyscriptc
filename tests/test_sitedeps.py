"""Bundle pure-Python dependencies into a bundle runtime's site-packages.

Refs #660. The unit tests build a fake site-packages tree (no network, no
real wheels) except the ones that read what this interpreter actually has
installed; those use the deps the project venv always carries.
"""

from __future__ import annotations

import sysconfig
from pathlib import Path

import pytest

from pyscriptc import sitedeps


def _program(tmp_path: Path, imports: str) -> Path:
    src = tmp_path / "prog.py"
    body = "".join(f"import {name}\n" for name in imports.split())
    src.write_text(body, encoding="utf-8")
    return src


def test_third_party_roots_skip_stdlib_relative_and_own(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "mod.py").write_text(
        "import json\nfrom . import other\nimport dateutil.parser\nimport pkg.helper\n",
        encoding="utf-8",
    )
    roots = sitedeps.third_party_roots(src)
    assert roots == ["dateutil"]


def test_third_party_roots_keep_first_order_and_dedupe(tmp_path: Path) -> None:
    src = _program(tmp_path, "b.a a a.c")
    assert sitedeps.third_party_roots(src) == ["a", "b"] or sitedeps.third_party_roots(src) == sorted({"a", "b"})


def test_distributions_of_maps_a_root_to_its_distribution() -> None:
    # `packaging` is a pyscriptc dependency, always installed in the venv.
    assert "packaging" in sitedeps.distributions_of("packaging")


def test_distributions_of_answers_empty_for_the_unknown(tmp_path: Path) -> None:
    assert sitedeps.distributions_of("definitely-not-a-distribution-emb660") == []


def _fake_site(tmp_path: Path) -> Path:
    site = tmp_path / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (site / "purepkg").mkdir()
    (site / "purepkg" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (site / "puremod.py").write_text("value = 2\n", encoding="utf-8")
    native = site / "nativepkg"
    native.mkdir()
    (native / "__init__.py").write_text("value = 3\n", encoding="utf-8")
    (native / "_speedups.cpython-314-x86_64-linux-gnu.so").write_bytes(b"\x7fELF")
    native_info = site / "nativelib-1.0.dist-info"
    native_info.mkdir()
    (native_info / "RECORD").write_text(
        "nativepkg/_speedups.cpython-314-x86_64-linux-gnu.so,,\nnativelib-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    (native_info / "METADATA").write_text("Name: nativelib\n", encoding="utf-8")
    pure_info = site / "Pure_Pkg-1.0.dist-info"
    pure_info.mkdir()
    (pure_info / "RECORD").write_text("purepkg/__init__.py,,\nPure_Pkg-1.0.dist-info/RECORD,,\n", encoding="utf-8")
    (pure_info / "METADATA").write_text("Name: Pure_Pkg\n", encoding="utf-8")
    return site


def test_stage_site_packages_copies_pure_trees_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    from pyscriptc import imports

    imports._installed_distributions.cache_clear()
    lib_dir = tmp_path / "lib"
    staged = sitedeps.stage_site_packages(lib_dir, "3.14", ["purepkg"])
    dest = lib_dir / "python3.14" / "site-packages"
    assert (dest / "purepkg" / "__init__.py").is_file()
    assert (dest / "Pure_Pkg-1.0.dist-info" / "RECORD").is_file()
    assert staged


def test_stage_site_packages_refuses_native_with_ps4302(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    from pyscriptc import imports

    imports._installed_distributions.cache_clear()
    native, _ = imports.distribution_is_native("nativelib")
    assert native, "the fake site must mark nativelib native"
    with pytest.raises(sitedeps.SiteError, match=r"PS4302.*nativelib"):
        sitedeps.stage_site_packages(tmp_path / "lib", "3.14", ["nativepkg"])


def test_check_pure_refuses_native_before_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    from pyscriptc import imports

    imports._installed_distributions.cache_clear()
    native, _ = imports.distribution_is_native("nativelib")
    assert native, "the fake site must mark nativelib native"
    with pytest.raises(sitedeps.SiteError, match="PS4302"):
        sitedeps.check_pure(["nativepkg"])


def test_closure_walks_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    monkeypatch.setattr(
        "pyscriptc.sitedeps.distributions_of",
        lambda root: {"purepkg": ["Pure_Pkg"], "puremod": ["Pure_Pkg"]}.get(root, []),
    )
    from importlib import metadata

    def _requires(dist):
        return ["puremod>=1"] if dist == "Pure_Pkg" else []

    monkeypatch.setattr(metadata, "requires", _requires, raising=False)
    resolved = sitedeps._closure(["purepkg"])
    assert "purepkg" in resolved["Pure_Pkg"]
    assert resolved  # the requirement walk reached puremod's import root too


def test_closure_walks_transitive_when_root_names_its_own_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root whose distribution has the same name still expands.

    The walk keeps two seen-sets, one for import roots and one for
    distributions, because a root like `fastapi` shares its name with the
    `fastapi` distribution: one seen-set made the shared name mark the
    distribution visited before its requirements were read, and the bundle
    staged the top-level distribution without any of its dependencies
    (`starlette`, `pydantic` for the FastAPI example).
    """
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    monkeypatch.setattr(
        "pyscriptc.sitedeps.distributions_of",
        lambda root: {"toplevel": ["toplevel"], "helper": ["helper"]}.get(root, []),
    )
    from importlib import metadata

    def _requires(dist):
        return ["helper>=1"] if dist == "toplevel" else []

    monkeypatch.setattr(metadata, "requires", _requires, raising=False)
    resolved = sitedeps._closure(["toplevel"])
    assert resolved == {"toplevel": ["toplevel"], "helper": ["helper"]}


def test_closure_skips_an_extra_gated_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An `extra == "..."` requirement does not travel.

    The program opts into an extra by importing the extra's modules, and
    the walk cannot know which extras the program wants. Pulling every
    extra in dragged Markupsafe (a compiled extension) into the Flask
    bundle through `jinja2`, and the bundle compile refused with PS4302
    for a dependency the app never imports.
    """
    site = _fake_site(tmp_path / "site")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    monkeypatch.setattr(
        "pyscriptc.sitedeps.distributions_of",
        lambda root: {"toplevel": ["toplevel"], "optional": ["optional"], "needed": ["needed"]}.get(root, []),
    )
    from importlib import metadata

    def _requires(dist):
        if dist == "toplevel":
            return ["needed>=1", 'optional>=1; extra == "full"', "envdep>=1; python_version < '3.0'"]
        return []

    monkeypatch.setattr(metadata, "requires", _requires, raising=False)
    resolved = sitedeps._closure(["toplevel"])
    assert "needed" in resolved, "a plain requirement travels"
    assert "optional" not in resolved, "an extra-gated requirement does not travel"
    assert "envdep" not in resolved, "a failing environment marker does not travel"


def test_distributions_of_answers_a_distribution_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A requirement names a distribution (`annotated-types`), an import names a module.

    The closure reads `Requires-Dist`, whose names are distribution names,
    while `distributions_of` answers for import roots. The two spellings
    differ (`annotated-types` against `annotated_types`), so the lookup
    tries the module spelling too.
    """
    site = tmp_path / "site-packages"
    site.mkdir()
    (site / "annotated_types.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr("pyscriptc.imports._site_packages", lambda: [site])
    assert sitedeps.distributions_of("annotated_types") == ["annotated_types"]
    assert sitedeps.distributions_of("annotated-types") == ["annotated_types"]


def test_record_roots_reads_a_dist_info_record(tmp_path: Path) -> None:
    record = tmp_path / "RECORD"
    record.write_text(
        "purepkg/__init__.py,,\npurepkg-1.0.dist-info/METADATA,,\nsix.py,,\n",
        encoding="utf-8",
    )
    assert sitedeps._record_roots(record) == {"purepkg", "purepkg-1.0.dist-info", "six"}


@pytest.mark.skipif(sysconfig.get_path("purelib") is None, reason="needs an installed interpreter")
def test_check_pure_accepts_this_venv_s_pure_deps() -> None:
    # `packaging` (a pyscriptc dependency) is pure; if it ever grows a wheel
    # extension this test flips and the refusal is the honest answer.
    sitedeps.check_pure(["packaging"])
