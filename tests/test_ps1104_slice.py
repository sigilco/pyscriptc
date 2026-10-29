"""The dict[K, Any] / list[Any] slice (issue #814, PS1104 option B).

The annotation is accepted: the container's shape (the key type, that the
elements are values) compiles, and the element type stays unresolved in
the IR as the `any` kind. Every read site proves the concrete element
type from a sound local source (the value the container was initialized
with, the values written into it, or the checker bridge's answer at the
read); a read that cannot prove it fences with PS1107, the new code that
names the unresolved read.

Bare `Any` stays PS1104: the frozen must-fence probe `code.PS1104` pins
the bare annotation (`def f(x: Any)` refuses), and a bare `Any` has no
container shape to accept.
"""

from __future__ import annotations

import pytest

from pyscriptc.coverage import build_report
from pyscriptc.lower import lower_source


def _fences(ir: dict) -> dict[str, int]:
    codes: dict[str, int] = {}
    for fence in ir.get("fences", []):
        codes[fence["code"]] = codes.get(fence["code"], 0) + 1
    return codes


def test_any_dict_display_resolves_reads() -> None:
    """`d: dict[str, Any] = {"k": "v"}` proves the value type; reads lower."""
    ir = lower_source(
        'from typing import Any\n\n\ndef f() -> int:\n    d: dict[str, Any] = {"k": "v"}\n    return len(d["k"])\n'
    )
    assert _fences(ir) == {}


def test_any_dict_write_resolves_reads() -> None:
    """An empty display plus agreeing writes proves the element type."""
    ir = lower_source(
        "from typing import Any\n\n\n"
        "def f() -> int:\n"
        "    d: dict[str, Any] = {}\n"
        '    d["k"] = "v"\n'
        '    return len(d["k"])\n'
    )
    assert _fences(ir) == {}


def test_any_list_display_resolves_reads() -> None:
    ir = lower_source('from typing import Any\n\n\ndef f() -> int:\n    xs: list[Any] = ["a"]\n    return len(xs[0])\n')
    assert _fences(ir) == {}


def test_unresolved_read_fences_ps1107() -> None:
    """A read the local sources cannot prove fences with the new code."""
    ir = lower_source(
        'from typing import Any\n\n\ndef f(d: dict[str, Any]) -> int:\n    v: str = d["k"]\n    return len(v)\n'
    )
    codes = _fences(ir)
    assert codes.get("PS1107") == 1
    assert "PS1104" not in codes


def test_unresolved_list_read_fences_ps1107() -> None:
    ir = lower_source(
        "from typing import Any\n\n\ndef f(xs: list[Any]) -> int:\n    v: str = xs[0]\n    return len(v)\n"
    )
    assert _fences(ir).get("PS1107") == 1


def test_concrete_argument_satisfies_any_dict_parameter() -> None:
    """A dict[str, str] argument satisfies a dict[str, Any] parameter."""
    ir = lower_source(
        "from typing import Any\n\n\n"
        "def g(d: dict[str, Any]) -> int:\n"
        "    return len(d)\n\n\n"
        "def main() -> int:\n"
        '    return g({"k": "v"})\n'
    )
    assert _fences(ir) == {}


def test_key_mismatch_still_fences() -> None:
    """The key type is the container's shape: a wrong key is PS1003."""
    ir = lower_source(
        'from typing import Any\n\n\ndef main() -> int:\n    d: dict[str, Any] = {1: "v"}\n    return len(d["k"])\n'
    )
    assert _fences(ir).get("PS1003") == 1


def test_bare_any_still_fences_ps1104() -> None:
    """The frozen gate: a bare `Any` annotation is PS1104, not accepted."""
    ir = lower_source("from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n")
    assert _fences(ir).get("PS1104") == 1
    assert "PS1107" not in _fences(ir)


def test_bridge_answer_resolves_the_read() -> None:
    """The bridge lane is consulted at the read site.

    mypy reads `d["k"]` on a `dict[str, Any]` parameter as Any (the
    element type is erased, not refined), so the read stays PS1107 even
    with the bridge wired: the fence is honest, not forced.
    """
    pytest.importorskip("mypy")
    from pyscriptc.factsbridge import mypy_available

    if not mypy_available():
        pytest.skip("mypy not available")
    import tempfile
    from pathlib import Path

    from pyscriptc.factsbridge import CheckerBridge

    source = 'from typing import Any\n\n\ndef f(d: dict[str, Any]) -> int:\n    v: int = d["k"]\n    return v\n'
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case.py"
        case.write_text(source, encoding="utf-8")
        bridge = CheckerBridge.for_path(case, "mypy", strict=False)
        ir = lower_source(source, bridge=bridge)
    assert _fences(ir).get("PS1107") == 1


def test_coverage_reports_the_new_family() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case.py"
        case.write_text(
            'from typing import Any\n\n\ndef f(d: dict[str, Any]) -> int:\n    v: str = d["k"]\n    return len(v)\n',
            encoding="utf-8",
        )
        report = build_report(case)
        assert report["summary"]["byFamily"].get("unresolved-container-read") == {"fences": 1, "derived": False}
        # the annotation itself no longer fences
        assert "dynamic-annotation" not in report["summary"]["byFamily"]


def test_typing_dict_spelling_resolves() -> None:
    ir = lower_source(
        "from typing import Any, Dict\n\n\n"
        "def f() -> int:\n"
        '    d: Dict[str, Any] = {"k": "v"}\n'
        '    return len(d["k"])\n'
    )
    assert _fences(ir) == {}
