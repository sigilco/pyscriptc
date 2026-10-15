"""Tests for the canonical IR form and its hash (tests/test_canonical.py only).

Restricted scope per the irscore plan: these tests never touch other test
files or run the full suite.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION, canonicalize, hash_module

assert CANONICAL_SCHEMA_VERSION == "canonical-schema/1", "frozen API constant drifted"

ROOT = Path(__file__).resolve().parents[1]
HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def base_module() -> dict[str, Any]:
    """Minimal valid-shaped module exercising every incidental-key site."""
    return {
        "irVersion": 1,
        "subset": "gates-v1",
        "cpython": "3.13.1",
        "sourceFile": "/somewhere/invoice.py",
        "entry": "add",
        "functions": [
            {
                "name": "add",
                "params": [{"name": "a", "type": {"kind": "int"}}],
                "returnType": {"kind": "int"},
                "locals": [{"name": "a", "type": {"kind": "int"}}],
                "body": [
                    {
                        "kind": "return",
                        "value": {"kind": "name", "id": "a", "type": {"kind": "int"}},
                        "span": {"file": "/somewhere/invoice.py", "line": 2, "col": 4},
                    }
                ],
                "span": {"file": "/somewhere/invoice.py", "line": 1, "col": 0},
            }
        ],
        "classes": [
            {
                "name": "Invoice",
                "fields": [{"name": "n", "type": {"kind": "int"}}],
                "methods": [],
                "span": {"file": "/somewhere/invoice.py", "line": 4, "col": 0},
            },
            {
                "name": "Agate",
                "fields": [],
                "methods": [],
                "span": {"file": "/somewhere/invoice.py", "line": 9, "col": 0},
            },
        ],
        "fences": [
            {
                "code": "PS2002",
                "message": "b message",
                "function": None,
                "span": {"file": "/somewhere/invoice.py", "line": 7, "col": 0},
            },
            {
                "code": "PS2002",
                "message": "a message",
                "function": "add",
                "span": {"file": "/somewhere/invoice.py", "line": 3, "col": 2},
            },
        ],
    }


def shuffle_keys(value: Any) -> Any:
    """Rebuild dicts with reversed key insertion order, recursively."""
    if isinstance(value, dict):
        return {k: shuffle_keys(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [shuffle_keys(item) for item in value]
    return value


def test_key_order_perturbation_same_hash() -> None:
    module = base_module()
    assert hash_module(module) == hash_module(shuffle_keys(module))


def test_fence_order_perturbation_same_hash() -> None:
    module = base_module()
    reordered = copy.deepcopy(module)
    reordered["fences"] = list(reversed(reordered["fences"]))
    assert hash_module(module) == hash_module(reordered)


def test_fences_differing_only_in_span_same_hash() -> None:
    module = base_module()
    moved = copy.deepcopy(module)
    moved["fences"][0]["span"] = {**moved["fences"][0]["span"], "line": 99, "col": 9}
    assert hash_module(module) == hash_module(moved)


SEED_SCRIPT = """
import json, sys
sys.path.insert(0, %(src)r)
from pyscriptc.canonical import hash_module

# classes come from set iteration: order flips with PYTHONHASHSEED
names = %(names)r
classes = [{"name": n, "fields": [], "methods": [],
            "span": {"file": "x", "line": 1, "col": 0}} for n in names]
with open(sys.argv[1], encoding="utf-8") as fh:
    module = json.load(fh)
module["classes"] = classes
print(hash_module(module))
"""


@pytest.mark.parametrize("seed", ["0", "1", "2"])
def test_pythonhashseed_invariance(seed: str) -> None:
    # Mimics lower.py:607: synthetic classes are emitted while iterating a
    # set of names, so the classes list order flips with PYTHONHASHSEED.
    # The canonical hash must be identical under every seed.
    module = base_module()
    names = {"Invoice", "Agate", "Zephyr", "Quartz", "Beryl"}
    script = SEED_SCRIPT % {"src": str(ROOT / "src"), "names": repr(names)}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(module, fh)
        module_path = fh.name
    hashes = set()
    for _ in range(3):
        proc = subprocess.run(
            [sys.executable, "-c", script, module_path],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed},
        )
        assert proc.returncode == 0, proc.stderr
        hashes.add(proc.stdout.strip())
    Path(module_path).unlink()
    assert len(hashes) == 1, hashes


def test_strip_at_every_depth_and_nothing_else() -> None:
    module = base_module()
    module["lineno"] = 42  # real AST attr name; must survive as a sentinel
    canonical = canonicalize(module)

    def check(value: Any) -> None:
        if isinstance(value, dict):
            assert "span" not in value
            assert "sourceFile" not in value
            assert "cpython" not in value
            assert "_notes" not in value
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(canonical)
    assert canonical["lineno"] == 42
    assert canonical["irVersion"] == 1
    assert canonical["entry"] == "add"
    assert len(canonical["functions"]) == 1


def test_idempotent_and_input_not_mutated() -> None:
    module = base_module()
    snapshot = copy.deepcopy(module)
    once = canonicalize(module)
    twice = canonicalize(once)
    assert once == twice
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)
    assert module == snapshot


def test_schema_version_changes_hash() -> None:
    module = base_module()
    baseline = hash_module(module)
    import pyscriptc.canonical as canon

    original = canon.CANONICAL_SCHEMA_VERSION
    try:
        canon.CANONICAL_SCHEMA_VERSION = original + "-x"
        assert hash_module(module) != baseline
    finally:
        canon.CANONICAL_SCHEMA_VERSION = original


def test_hash_is_lowercase_64_hex() -> None:
    assert HEX64.match(hash_module(base_module()))


def test_end_to_end_lower_add_py() -> None:
    # Normal CLI path: lower examples/add.py via the same entry compile_cmd uses.
    from pyscriptc.lower import lower_file

    path = ROOT / "examples" / "add.py"
    module_a, _ = lower_file(str(path))
    module_b, _ = lower_file(str(path))
    hash_a = hash_module(module_a)
    assert hash_a == hash_module(module_b)
    assert HEX64.match(hash_a)
    # Spans carry the invocation path; canonical hashes must not depend on it.
    module_c, _ = lower_file(str(path))
    module_c["sourceFile"] = "/elsewhere/add.py"
    for fn in module_c["functions"]:
        fn["span"]["file"] = "/elsewhere/add.py"
    assert hash_module(module_c) == hash_a
