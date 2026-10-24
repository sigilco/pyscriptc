"""Parse `docs/reference/divergences.md` into structured rows.

The compat site publishes the numbered divergences next to the fences, in the
style of the doc itself: one row per numbered item, with the text, the family
it belongs to and the sources it cites.

The doc is hand-written prose, so the parser is deliberately narrow: it reads
the numbered list under "## Numbered divergences" and nothing else. A change
in the shape of that section is a parser failure, not a silent empty list: the
builder raises when the doc has items and the parser found none.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIVERGENCES = ROOT / "docs" / "reference" / "divergences.md"

# The section the numbered items live in.
SECTION = "## Numbered divergences"

# One numbered item: "12. `assert` is always evaluated; ...". The number, a
# space and the item text. The item may span lines, so the parser joins the
# continuation lines of an item.
ITEM = re.compile(r"^(\d+)\.\s+(.*)$")

# The link syntax inside an item: [text](url).
LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# The family each divergence belongs to. The families are the buckets the site
# groups them under; the order is the order the site shows them.
#
# The classification is by keyword, so a new item lands in a family without a
# code change, and the family table stays readable. The keywords are matched
# against the lowercased item text.
FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("numeric", ("`int` is `i64`", "//` by zero", "zero division", "big int")),
    ("text", ("latin-1", "unicode", "string literal", "strings are a")),
    ("collections", ("set and `frozenset`", "iteration is insertion order", "hash order")),
    ("objects", ("subclass", "classmethod", "staticmethod", "`abc`", "`abcmeta`", "`protocol`", "apperror")),
    (
        "stdlib",
        (
            "`re` is the runtime",
            "`json` has no",
            "library failures",
            "`unicodedata`",
            "`importlib.resources`",
            "`shutil`",
            "`dotenv`",
            "`yaml.safe_load`",
            "`jwt`",
            "`orjson`",
            "`urllib.request`",
            "urlopen",
            "socket",
            "https://",
            "`x is none`",
            "`assert`",
            "range(0)",
            "apperror",
        ),
    ),
    ("island", ("island call", "embedded cpython", "fallback tier")),
    (
        "target",
        (
            "wasm32",
            "windows",
            "cross-compil",
            "target",
            "linux",
            "macos",
        ),
    ),
)

# The family an item lands in when no keyword matches. Every item is about a
# difference from CPython, so the fallback is the language bucket.
DEFAULT_FAMILY = "stdlib"


def _family(text: str) -> str:
    """The family of one item, by keyword."""
    lowered = text.lower()
    for family, keywords in FAMILIES:
        if any(keyword in lowered for keyword in keywords):
            return family
    return DEFAULT_FAMILY


def _sources(text: str) -> list[str]:
    """The sources an item cites, as the link targets in order."""
    seen: list[str] = []
    for _, target in LINK.findall(text):
        if target not in seen:
            seen.append(target)
    return seen


def parse(text: str) -> list[dict[str, Any]]:
    """The numbered items of the divergences doc, as structured rows."""
    lines = text.splitlines()
    try:
        start = lines.index(SECTION)
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        match = ITEM.match(line)
        if match:
            if current is not None:
                rows.append(current)
            current = {"number": int(match.group(1)), "text": match.group(2).strip()}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        # A continuation line belongs to the item above it.
        current["text"] = f"{current['text']} {stripped}"
    if current is not None:
        rows.append(current)
    for row in rows:
        row["family"] = _family(row["text"])
        row["sources"] = _sources(row["text"])
    return rows


def read(path: Path | None = None) -> list[dict[str, Any]]:
    """The divergences of the doc in the tree.

    Raises ValueError when the doc is missing, because a site that silently
    drops the divergence list would look like "no divergences", which is the
    one answer the site must never give.
    """
    source = path or DIVERGENCES
    if not source.exists():
        raise ValueError(f"divergences doc not found: {source}")
    rows = parse(source.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"no numbered divergences found in {source}")
    return rows
