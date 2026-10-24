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

SCHEMA_VERSION = "2"

# The previous schema version. The builder still validates a "1" document so
# the nightly bot PR cannot be bricked by a schema bump: it carries data, the
# page upgrade ships with the PR that reads the new sections.
PREVIOUS_SCHEMA_VERSION = "1"

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
    "builtins",
    "stdlib",
    "packages",
    "language",
    "fences",
    "history",
    "targets",
    "divergences",
)

# The per-target status vocabulary. `native` is proven by CI or a smoke lane,
# `experimental` works with known gaps, `planned` is on a milestone and not
# measured. TARGET_STATUSES["refused"] is not a status a row can carry: an
# unlisted target simply does not get a row.
TARGET_STATUSES = ("native", "experimental", "planned")

# The divergence family labels, from docs/reference/divergences.md style.
DIVERGENCE_FAMILIES = ("numeric", "text", "collections", "objects", "stdlib", "island", "target")


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
    _check_probe_fields(row, where, problems)


def _check_probe_fields(row: Any, where: str, problems: list[str]) -> None:
    """The per-row probe counters a support or package row can carry.

    `probes` is None when no compat2 probe covers the row: that is "not
    measured", not zero. A negative or contradictory counter is a data bug.
    """
    probes = row.get("probes", "missing")
    if probes != "missing" and probes is not None and not _is_int(probes):
        problems.append(f"{where}: probes is not an integer or null")
        return
    if probes in (None, "missing"):
        return
    rate = row.get("pass_rate")
    if rate is not None and not (_is_number(rate) and 0 <= rate <= 1):
        problems.append(f"{where}: pass_rate is not a number in [0, 1]")
    outcomes = row.get("probe_outcomes")
    if not _is_dict(outcomes):
        problems.append(f"{where}: probe_outcomes is not an object")
    else:
        for key in ("pass", "fence", "failures", "excluded"):
            if not _is_int(outcomes.get(key)):
                problems.append(f"{where}: probe_outcomes.{key} is not an integer")
        total = sum(outcomes.get(key, 0) for key in ("pass", "fence", "failures", "excluded"))
        if total > probes:
            problems.append(f"{where}: probe outcomes sum to {total}, more than {probes} probes")
    codes = row.get("probe_codes")
    if not _is_dict(codes):
        problems.append(f"{where}: probe_codes is not an object")
    elif not all(_is_str(code) and _is_int(count) for code, count in codes.items()):
        problems.append(f"{where}: probe_codes is not a code/count object")


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
    _check_probe_fields(row, where, problems)


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
    for key in ("code", "title", "why", "fix", "count", "packages"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    for key in ("code", "title", "why", "fix"):
        if not _is_str(row.get(key)):
            problems.append(f"{where}: {key} is not a string")
    for key in ("count", "packages"):
        if not _is_int(row.get(key)):
            problems.append(f"{where}: {key} is not an integer")


def _check_targets_row(row: Any, where: str, problems: list[str]) -> None:
    """One cross-compilation target row."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("id", "triple", "label", "status", "family", "note"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if not _is_str(row.get("id")) or not _is_str(row.get("triple")) or not _is_str(row.get("label")):
        problems.append(f"{where}: id, triple and label must be strings")
    if row.get("status") not in TARGET_STATUSES:
        problems.append(f"{where}: status {row.get('status')!r} is not one of {list(TARGET_STATUSES)}")
    if not _is_str(row.get("family")):
        problems.append(f"{where}: family is not a string")
    if not _is_str(row.get("note")):
        problems.append(f"{where}: note is not a string")


def _check_divergence(row: Any, where: str, problems: list[str]) -> None:
    """One numbered divergence, parsed from docs/reference/divergences.md."""
    if not _is_dict(row):
        problems.append(f"{where}: expected an object")
        return
    for key in ("number", "text", "family", "sources"):
        if key not in row:
            problems.append(f"{where}: missing key {key!r}")
    if not _is_int(row.get("number")):
        problems.append(f"{where}: number is not an integer")
    if not _is_str(row.get("text")):
        problems.append(f"{where}: text is not a string")
    if row.get("family") not in DIVERGENCE_FAMILIES:
        problems.append(f"{where}: family {row.get('family')!r} is not one of {list(DIVERGENCE_FAMILIES)}")
    sources = row.get("sources")
    if not _is_list(sources):
        problems.append(f"{where}: sources is not a list")
    elif not all(_is_str(item) for item in sources):
        problems.append(f"{where}: sources holds a non-string")


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
    history = document.get("history")
    if not _is_list(history):
        problems.append("history is not a list")
    else:
        if len(history) > HISTORY_LIMIT:
            problems.append(f"history holds {len(history)} points, the limit is {HISTORY_LIMIT}")
        for index, row in enumerate(history):
            _check_history(row, f"history[{index}]", problems)
    targets = document.get("targets")
    if not _is_list(targets):
        problems.append("targets is not a list")
    else:
        ids = [row.get("id") for row in targets if _is_dict(row)]
        if len(ids) != len(set(ids)):
            problems.append("targets holds a duplicate id")
        for index, row in enumerate(targets):
            _check_targets_row(row, f"targets[{index}]", problems)
    divergences = document.get("divergences")
    if not _is_list(divergences):
        problems.append("divergences is not a list")
    else:
        numbers = [row.get("number") for row in divergences if _is_dict(row)]
        if numbers != sorted(numbers):
            problems.append("divergences are not sorted by number")
        if len(numbers) != len(set(numbers)):
            problems.append("divergences hold a duplicate number")
        for index, row in enumerate(divergences):
            _check_divergence(row, f"divergences[{index}]", problems)
    return problems


# The contract of `compat/data/summary.json`, the file the landing page reads
# for live numbers. The keys are stable: the landing page names them, so a
# rename is a breaking change for a page this repo also builds.
SUMMARY_KEYS = (
    "schema",
    "generated",
    "commit",
    "url",
    "packages_full",
    "packages_partial",
    "packages_none",
    "packages_total",
    "stdlib_modules",
    "stdlib_full",
    "builtins_full",
    "builtins_total",
    "fences",
    "divergences",
    "probes",
    "probe_pass",
    "probe_failures",
    "pass_rate",
    "weighted_pass",
    "targets_native",
    "targets_experimental",
)

SUMMARY_INT_KEYS = (
    "packages_full",
    "packages_partial",
    "packages_none",
    "packages_total",
    "stdlib_modules",
    "stdlib_full",
    "builtins_full",
    "builtins_total",
    "fences",
    "divergences",
    "probes",
    "probe_pass",
    "probe_failures",
    "targets_native",
    "targets_experimental",
)


def validate_summary(document: Any) -> list[str]:
    """Problems in the summary document, empty when it matches the contract."""
    problems: list[str] = []
    if not _is_dict(document):
        return ["summary is not an object"]
    for key in SUMMARY_KEYS:
        if key not in document:
            problems.append(f"missing key {key!r}")
    extra = sorted(set(document) - set(SUMMARY_KEYS))
    for key in extra:
        problems.append(f"unexpected key {key!r}")
    if document.get("schema") != 1:
        problems.append(f"schema is {document.get('schema')!r}, expected 1")
    for key in ("generated", "commit", "url"):
        if not _is_str(document.get(key)):
            problems.append(f"{key} is not a string")
    for key in SUMMARY_INT_KEYS:
        if not _is_int(document.get(key)):
            problems.append(f"{key} is not an integer")
    for key in ("weighted_pass",):
        if not _is_number(document.get(key)):
            problems.append(f"{key} is not a number")
    rate = document.get("pass_rate")
    if rate is not None and not (_is_number(rate) and 0 <= rate <= 1):
        problems.append("pass_rate is not a number in [0, 1]")
    return problems
