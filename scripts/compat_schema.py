"""Schema for the compat site data file (`compat/data/compat.json`).

The schema is plain data plus a small validator, so the check has no third-party
dependency and runs in the same venv as the compiler.

`validate` returns a list of human-readable problems. An empty list means the
document is valid. The builder refuses to write a document that does not
validate, and `tests/test_compat_json.py` asserts the committed file validates.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1"

# Status vocabulary. `full` is green, `partial` is yellow, `none` is red.
STATUSES = ("full", "partial", "none", "unknown")

# The compat2 outcome names, from benchmarks/compat2/outcomes.py. Kept as a
# literal so the schema does not depend on a frozen module.
OUTCOMES = (
    "pass",
    "fence",
    "wrong-output",
    "crash",
    "timeout",
    "build-error",
    "excluded",
)

# Maximum number of history points kept in the file.
HISTORY_LIMIT = 180

_TOP_LEVEL_KEYS = (
    "schema",
    "generated",
    "commit",
    "sources",
    "summary",
    "ladder",
    "builtins",
    "stdlib",
    "packages",
    "language",
    "fences",
    "history",
)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _check_support_row(row: Any, kind: str, where: str, problems: list[str]) -> None:
    """One builtin or stdlib row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("id", "kind", "label", "status", "count", "unsupported", "fences", "names"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if row.get("kind") != kind:
        problems.append(f"{where}: kind is {row.get('kind')!r}, expected {kind!r}")
    if row.get("status") not in STATUSES:
        problems.append(f"{where}: status {row.get('status')!r} is not one of {list(STATUSES)}")
    if not _is_int(row.get("count")):
        problems.append(f"{where}: count is not an integer")
    if not _is_list(row.get("unsupported")):
        problems.append(f"{where}: unsupported is not a list")
    elif not all(_is_str(item) for item in row["unsupported"]):
        problems.append(f"{where}: unsupported holds a non-string")
    if not _is_list(row.get("fences")):
        problems.append(f"{where}: fences is not a list")
    elif not all(_is_str(item) for item in row["fences"]):
        problems.append(f"{where}: fences holds a non-string")
    if not _is_list(row.get("names")):
        problems.append(f"{where}: names is not a list")
    elif not all(_is_str(item) for item in row["names"]):
        problems.append(f"{where}: names holds a non-string")


def _check_package(row: Any, where: str, problems: list[str]) -> None:
    """One pinned PyPI package row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("name", "import_name", "version", "status", "fences", "definitions", "fenced_definitions"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if row.get("status") not in STATUSES:
        problems.append(f"{where}: status {row.get('status')!r} is not one of {list(STATUSES)}")
    for key in ("fences", "definitions", "fenced_definitions"):
        if not _is_int(row.get(key)):
            problems.append(f"{where}: {key} is not an integer")
    for key in ("top_families", "codes"):
        if not _is_list(row.get(key)):
            problems.append(f"{where}: {key} is not a list")
            continue
        for index, item in enumerate(row[key]):
            if not _is_dict(item) or not _is_str(item.get("family", item.get("code"))):
                problems.append(f"{where}: {key}[{index}] is not a name/count object")
            elif not _is_int(item.get("count")):
                problems.append(f"{where}: {key}[{index}] count is not an integer")


def _check_language(row: Any, where: str, problems: list[str]) -> None:
    """One language-feature group row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("id", "label", "probes", "outcomes", "weighted_pass"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if not _is_int(row.get("probes")):
        problems.append(f"{where}: probes is not an integer")
    if not _is_number(row.get("weighted_pass")):
        problems.append(f"{where}: weighted_pass is not a number")
    outcomes = row.get("outcomes")
    if not _is_dict(outcomes):
        problems.append(f"{where}: outcomes is not an object")
    else:
        for key, value in outcomes.items():
            if key not in OUTCOMES:
                problems.append(f"{where}: outcome {key!r} is not one of {list(OUTCOMES)}")
            elif not _is_int(value):
                problems.append(f"{where}: outcome {key!r} is not an integer")


def _check_fence(row: Any, where: str, problems: list[str]) -> None:
    """One fence-code counter row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("code", "title", "count", "packages"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if not _is_str(row.get("code")):
        problems.append(f"{where}: code is not a string")
    if not _is_str(row.get("title")):
        problems.append(f"{where}: title is not a string")
    for key in ("count", "packages"):
        if not _is_int(row.get(key)):
            problems.append(f"{where}: {key} is not an integer")


def _check_history(row: Any, where: str, problems: list[str]) -> None:
    """One dated history point."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("date", "commit", "weighted_pass", "packages_full", "packages_partial", "packages_none", "fences"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if not _is_str(row.get("date")):
        problems.append(f"{where}: date is not a string")
    if not _is_str(row.get("commit")):
        problems.append(f"{where}: commit is not a string")
    if not _is_number(row.get("weighted_pass")):
        problems.append(f"{where}: weighted_pass is not a number")
    for key in ("packages_full", "packages_partial", "packages_none", "fences"):
        if not _is_int(row.get(key)):
            problems.append(f"{where}: {key} is not an integer")


# The lanes of the per-import policy table in the adoption plan.
LANES = ("native", "pack", "extern", "island", "absent")


def _check_ladder_rung(row: Any, where: str, problems: list[str]) -> None:
    """One ladder rung row (R1, R2)."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("rung", "label", "corpus", "total", "compiled", "passed", "measured"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    for key in ("total", "compiled", "passed"):
        if not _is_int(row.get(key)):
            problems.append(f"{where}: {key} is not an integer")
    for key in ("rung", "label", "corpus"):
        if not _is_str(row.get(key)):
            problems.append(f"{where}: {key} is not a string")
    if not isinstance(row.get("measured"), bool):
        problems.append(f"{where}: measured is not a boolean")


def _check_ladder_lane(row: Any, where: str, problems: list[str]) -> None:
    """One R4 lane-share row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("lane", "imports", "share"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if row.get("lane") not in LANES:
        problems.append(f"{where}: lane {row.get('lane')!r} is not one of {list(LANES)}")
    if not _is_int(row.get("imports")):
        problems.append(f"{where}: imports is not an integer")
    if not _is_number(row.get("share")):
        problems.append(f"{where}: share is not a number")


def _check_ladder(ladder: Any, where: str, problems: list[str]) -> None:
    """The ladder section: the R1, R2 and R4 rows."""
    if not _is_dict(ladder):
        problems.append(f"{where} is not an object")
        return
    rungs = ladder.get("rungs")
    if not _is_list(rungs):
        problems.append(f"{where}.rungs is not a list")
    else:
        for index, row in enumerate(rungs):
            _check_ladder_rung(row, f"{where}.rungs[{index}]", problems)
    lanes = ladder.get("lanes")
    if not _is_list(lanes):
        problems.append(f"{where}.lanes is not a list")
    else:
        for index, row in enumerate(lanes):
            _check_ladder_lane(row, f"{where}.lanes[{index}]", problems)
    if not _is_str(ladder.get("corpus")):
        problems.append(f"{where}.corpus is not a string")


def validate(document: Any) -> list[str]:
    """Problems in `document`, empty when it matches the schema."""
    problems: list[str] = []
    if not _is_dict(document):
        return ["document is not an object"]
    for key in _TOP_LEVEL_KEYS:
        if key not in document:
            problems.append(f"missing top-level key {key!r}")
    extra = sorted(set(document) - set(_TOP_LEVEL_KEYS))
    for key in extra:
        problems.append(f"unexpected top-level key {key!r}")
    if document.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema is {document.get('schema')!r}, expected {SCHEMA_VERSION!r}")
    if not _is_str(document.get("generated")):
        problems.append("generated is not a string")
    if not _is_str(document.get("commit")):
        problems.append("commit is not a string")
    if not _is_dict(document.get("sources")):
        problems.append("sources is not an object")
    if not _is_dict(document.get("summary")):
        problems.append("summary is not an object")
    for key, kind in (("builtins", "builtin"), ("stdlib", "stdlib")):
        rows = document.get(key)
        if not _is_list(rows):
            problems.append(f"{key} is not a list")
            continue
        for index, row in enumerate(rows):
            _check_support_row(row, kind, f"{key}[{index}]", problems)
    packages = document.get("packages")
    if not _is_list(packages):
        problems.append("packages is not a list")
    else:
        for index, row in enumerate(packages):
            _check_package(row, f"packages[{index}]", problems)
    language = document.get("language")
    if not _is_list(language):
        problems.append("language is not a list")
    else:
        for index, row in enumerate(language):
            _check_language(row, f"language[{index}]", problems)
    fences = document.get("fences")
    if not _is_list(fences):
        problems.append("fences is not a list")
    else:
        for index, row in enumerate(fences):
            _check_fence(row, f"fences[{index}]", problems)
    ladder = document.get("ladder")
    if ladder is not None:
        _check_ladder(ladder, "ladder", problems)
    history = document.get("history")
    if not _is_list(history):
        problems.append("history is not a list")
    else:
        if len(history) > HISTORY_LIMIT:
            problems.append(f"history holds {len(history)} points, the limit is {HISTORY_LIMIT}")
        for index, row in enumerate(history):
            _check_history(row, f"history[{index}]", problems)
    return problems
