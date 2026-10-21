"""pyrefly TSP facts adapter: canned-JSON decoder tests plus one live check."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    PyreflyTspFactsAdapter,
    _tsf_denote,
    _tsf_type,
    facts_provider,
)


def test_instance_with_type_args() -> None:
    raw = {
        "kind": "instance",
        "qname": "builtins.list",
        "mro": ["typing.Sequence"],
        "typeArgs": [{"kind": "instance", "qname": "builtins.int", "mro": []}],
    }
    facts = _tsf_type(raw)
    assert facts is not None
    assert facts.kind == "list"
    assert [a.kind for a in facts.args] == ["int"]


def test_none_and_tuple_instances() -> None:
    none = _tsf_type({"kind": "instance", "qname": "types.NoneType", "mro": []})
    assert none is not None and none.kind == "none" and none.optional
    tup = _tsf_type(
        {
            "kind": "instance",
            "qname": "builtins.tuple",
            "typeArgs": [{"kind": "instance", "qname": "builtins.int"}, {"kind": "instance", "qname": "builtins.str"}],
        }
    )
    assert tup is not None and tup.kind == "tuple" and len(tup.args) == 2


def test_union_marks_optional() -> None:
    facts = _tsf_type(
        {
            "kind": "union",
            "members": [
                {"kind": "instance", "qname": "builtins.int"},
                {"kind": "instance", "qname": "types.NoneType"},
            ],
        }
    )
    assert facts is not None and facts.kind == "union" and facts.optional


def test_class_is_type_and_denotes_to_instance() -> None:
    facts = _tsf_type({"kind": "class", "qname": "builtins.int", "mro": []})
    assert facts is not None and facts.kind == "type"
    assert _tsf_denote(facts).kind == "int"


def test_function_module_any() -> None:
    fn = _tsf_type({"kind": "function", "qname": "m.f", "returns": {"kind": "instance", "qname": "builtins.str"}})
    assert fn is not None and fn.callable is not None and fn.callable.return_type.kind == "str"
    mod = _tsf_type({"kind": "module", "name": "os.path"})
    assert mod is not None and mod.kind == "module" and mod.runtime_repr == "os.path"
    anything = _tsf_type({"kind": "any"})
    assert anything is not None and anything.kind == "any"


def test_other_parses_display_text() -> None:
    facts = _tsf_type({"kind": "other", "display": "Literal[42]"})
    assert facts is not None and facts.kind == "literal"


def test_null_and_unknown_kinds_are_no_answer() -> None:
    assert _tsf_type(None) is None
    assert _tsf_type({"kind": "mystery"}) is None


def test_factory_wires_provider() -> None:
    assert isinstance(facts_provider("pyrefly-tsp"), PyreflyTspFactsAdapter)


def test_version_mismatch_is_clear_check_failure(tmp_path: Path) -> None:
    script = tmp_path / "fake.py"
    script.write_text(
        textwrap.dedent(
            """\
            import json, sys
            def read():
                n = 0
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line.lower().startswith(b"content-length"):
                        n = int(line.split(b":")[1])
                    if line in (b"\\r\\n", b"\\n"):
                        return json.loads(sys.stdin.buffer.read(n))
            def send(i, r):
                b = json.dumps({"jsonrpc": "2.0", "id": i, "result": r}).encode()
                sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(b) + b)
                sys.stdout.buffer.flush()
            while (m := read()) is not None:
                if "id" not in m:
                    continue
                send(m["id"], {} if m["method"] == "initialize" else "9.9.9")
            """
        )
    )
    import sys

    adapter = PyreflyTspFactsAdapter(command=[sys.executable, str(script), "tsp"])
    source = tmp_path / "m.py"
    source.write_text("x = 1\n")
    with pytest.raises(CheckFailed, match="protocol"):
        adapter.facts(source)
    adapter.close()


@pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly unavailable")
def test_live_pyrefly_tsp(tmp_path: Path) -> None:
    path = tmp_path / "live.py"
    path.write_text(
        textwrap.dedent(
            """\
            from typing import overload

            def add(a: int, b: int = 2) -> int:
                return a + b

            @overload
            def pick(x: int) -> int: ...
            @overload
            def pick(x: str) -> str: ...
            def pick(x):
                return x

            total = add(1)
            name = "hi".upper()
            chosen = pick("a")
            """
        )
    )
    with PyreflyTspFactsAdapter() as adapter:
        facts = adapter.facts(path)
        again = adapter.facts(path)
    assert facts.provider == "pyrefly-tsp"
    assert facts.expression_at(13, 0) is not None and facts.expression_at(13, 0).kind == "int"
    assert facts.expression_at(13, 8) is not None and facts.expression_at(13, 8).kind == "int"
    sig = facts.signature("__main__.add")
    assert sig is not None and sig.return_type.kind == "int"
    assert [p.type.kind for p in sig.parameters] == ["int", "int"]
    assert len(facts.overload_choices("__main__.pick")) == 2
    assert facts.overload_choices("builtins.str.upper")
    assert again.expressions.keys() == facts.expressions.keys()
