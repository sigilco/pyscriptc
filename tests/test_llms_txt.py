"""Tests for agent access: docmd's llms.txt plus the appended public data files."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_llms_txt import DATA_DIR, apply  # noqa: E402

DOCS_DIR = ROOT / "docs"
BUILT = ROOT / "dist" / "docs"
BASE_URL = "https://pyscriptc.pages.dev/docs"

needs_build = pytest.mark.skipif(
    not (BUILT / "llms.txt").is_file(), reason="needs the docmd build (mise run docs:build)"
)


def _final_out(tmp_path: Path) -> Path:
    """A copy of docmd's output with the data section applied."""
    for name in ("llms.txt", "llms-full.txt"):
        shutil.copy2(BUILT / name, tmp_path / name)
    apply(tmp_path)
    return tmp_path


def _page_urls() -> list[str]:
    urls = []
    for md in sorted(DOCS_DIR.rglob("*.md")):
        rel = md.relative_to(DOCS_DIR)
        if rel.parts[0] == ".docmd":
            continue
        sub = rel.with_suffix("").as_posix().removesuffix("index").rstrip("/")
        urls.append(f"{BASE_URL}/{sub}/" if sub else f"{BASE_URL}/")
    return urls


@needs_build
def test_every_docs_page_appears_in_llms_txt(tmp_path: Path) -> None:
    text = (_final_out(tmp_path) / "llms.txt").read_text(encoding="utf-8")
    for url in _page_urls():
        assert f"({url})" in text, f"doc page {url} missing from llms.txt"


@needs_build
def test_every_public_data_file_appears_in_llms_txt(tmp_path: Path) -> None:
    out = _final_out(tmp_path)
    short = (out / "llms.txt").read_text(encoding="utf-8")
    full = (out / "llms-full.txt").read_text(encoding="utf-8")
    names = sorted(p.name for p in DATA_DIR.glob("*.json"))
    assert names
    for name in names:
        assert f"[{name}](" in short, f"data file {name} missing from llms.txt"
        assert f"### [{name}](" in full, f"data file {name} missing from llms-full.txt"


def test_apply_is_idempotent_and_keeps_docmd_content(tmp_path: Path) -> None:
    (tmp_path / "llms.txt").write_text("# t\n\n## Documentation Files\n\n- [A](u)\n", encoding="utf-8")
    (tmp_path / "llms-full.txt").write_text("# t - Full Context\n\n## [A](u)\n\nbody\n", encoding="utf-8")
    apply(tmp_path)
    first = {n: (tmp_path / n).read_text(encoding="utf-8") for n in ("llms.txt", "llms-full.txt")}
    apply(tmp_path)
    for n, text in first.items():
        assert (tmp_path / n).read_text(encoding="utf-8") == text
    assert first["llms.txt"].startswith("# t\n\n## Documentation Files\n\n- [A](u)\n")
    assert first["llms.txt"].count("## Public Data Files") == 1
    assert "body" in first["llms-full.txt"]


def test_apply_requires_docmd_output(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        apply(tmp_path)
