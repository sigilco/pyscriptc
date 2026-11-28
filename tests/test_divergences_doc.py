"""Regression guards for docs/reference/divergences.md.

The numbered list is load-bearing: the ledger generator parses it
(scripts/gen_divergence_ledger.py), the parity contract claims that anything
not listed is identical, and tests/test_divergence_ledger.py replays one row
per number. These guards keep the document parseable and honest: the numbers
are unique and consecutive, and every item links a source (a repo path, an
issue, or a spec file) so a reader can trace where the divergence comes from.
"""

from __future__ import annotations

import json
import re

from tests.test_gates import ROOT

DIVERGENCES = ROOT / "docs" / "reference" / "divergences.md"


def numbered_section() -> str:
    text = DIVERGENCES.read_text(encoding="utf-8")
    return text.split("## Numbered divergences", 1)[1].split("\n## ", 1)[0]


def numbered_items() -> list[tuple[int, str]]:
    """(number, body) for every entry of the numbered-divergences section."""
    section = numbered_section()
    return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^(\d+)\. (.*)$", section, re.M)]


def test_numbers_are_unique() -> None:
    numbers = [n for n, _ in numbered_items()]
    assert len(numbers) == len(set(numbers)), "duplicate divergence numbers"


def test_numbers_are_consecutive_from_one() -> None:
    numbers = [n for n, _ in numbered_items()]
    assert sorted(numbers) == numbers, "the numbered list must stay in ascending order"
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    assert not missing, f"numbered divergences have gaps: {missing}"


def ledger_rows() -> list[dict]:
    ledger = json.loads((ROOT / "docs" / "reference" / "divergence-ledger.json").read_text(encoding="utf-8"))
    return ledger["rows"]


def test_every_item_links_a_source() -> None:
    """Each item carries a traceable source: an issue link, a divergence cross-reference,
    a repo path (a probe, a spec, or a code file), or a ledger row whose probe records
    the recorded bytes for both sides."""
    probed = {f"D{n}" for n, _ in numbered_divergences_with_probes()}
    for number, body in numbered_items():
        ok = (
            re.search(r"\]\(http", body)
            or re.search(r"divergence \d+", body)
            or re.search(r"`[\w./-]+\.(?:py|c|md|toml|json)`", body)
            or f"D{number}" in probed
        )
        assert ok, f"divergence {number} carries no source link"


def numbered_divergences_with_probes() -> list[tuple[int, bool]]:
    out = []
    for row in ledger_rows():
        m = re.fullmatch(r"D(\d+)", row["id"])
        if m and row["listed"]:
            out.append((int(m.group(1)), row["probe"] is not None))
    return out
