"""Per-function IR digests name the unit that changed."""

from benchmarks.compat2.fndigest import diff_units, unit_digests


def _module(body_b: str = "x") -> dict:
    return {
        "irVersion": 1,
        "sourceFile": "a.py",
        "functions": [
            {"name": "f", "body": ["x"], "span": {"line": 1}},
            {"name": "g", "body": [body_b], "span": {"line": 5}},
        ],
        "classes": [],
        "fences": [],
    }


def test_identical_modules_have_no_diff() -> None:
    assert diff_units(unit_digests(_module()), unit_digests(_module())) == []


def test_span_and_source_file_do_not_count() -> None:
    other = _module()
    other["sourceFile"] = "elsewhere.py"
    other["functions"][0]["span"] = {"line": 99}
    assert diff_units(unit_digests(_module()), unit_digests(other)) == []


def test_changed_function_is_named() -> None:
    rows = diff_units(unit_digests(_module()), unit_digests(_module("y")))
    assert rows == [("fn:g", "changed")]


def test_added_and_removed_units() -> None:
    a = _module()
    b = _module()
    b["functions"].pop(0)
    b["functions"].append({"name": "h", "body": [], "span": {}})
    assert diff_units(unit_digests(a), unit_digests(b)) == [("fn:f", "removed"), ("fn:h", "added")]
