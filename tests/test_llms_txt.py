"""Tests for agent access: llms.txt and llms-full.txt generation and coverage."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_llms_txt import (  # noqa: E402
    DATA_DIR,
    DOCS_DIR,
    build_llms_full_txt,
    build_llms_txt,
    get_data_files,
    get_doc_pages,
)


def test_all_docs_pages_appear_in_llms_txt() -> None:
    pages = get_doc_pages()
    data_files = get_data_files()
    llms_text = build_llms_txt(pages, data_files)

    # Every .md file under docs/ (excluding .docmd/) must be represented
    for md_file in sorted(DOCS_DIR.rglob("*.md")):
        rel = md_file.relative_to(DOCS_DIR)
        if str(rel).startswith(".docmd"):
            continue

        # Page url or relative path must appear in llms_text
        matching = [p for p in pages if p["path"] == str(rel)]
        assert matching, f"Doc file {rel} not cataloged in pages"
        assert matching[0]["url"] in llms_text, f"Doc page URL {matching[0]['url']} missing from llms.txt"


def test_all_public_data_files_appear_in_llms_txt() -> None:
    pages = get_doc_pages()
    data_files = get_data_files()
    llms_text = build_llms_txt(pages, data_files)

    # Every .json file under compat/data/ must appear
    for json_file in sorted(DATA_DIR.glob("*.json")):
        name = json_file.name
        assert f"[{name}]" in llms_text, f"Data file {name} link missing from llms.txt"
        matching = [df for df in data_files if df["name"] == name]
        assert matching, f"Data file {name} not in data_files list"
        # Must carry a description and schema/format
        assert matching[0]["description"] in llms_text
        assert matching[0]["schema"] in llms_text


def test_llms_full_txt_contains_page_content_and_data_schemas() -> None:
    pages = get_doc_pages()
    data_files = get_data_files()
    full_text = build_llms_full_txt(pages, data_files)

    for p in pages:
        assert p["title"] in full_text
        assert p["url"] in full_text

    for df in data_files:
        assert df["name"] in full_text
        assert df["schema"] in full_text


def test_cli_check_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_llms_txt.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"check failed:\n{result.stderr}\n{result.stdout}"
