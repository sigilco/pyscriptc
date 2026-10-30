"""A pinned parameter lowers its definition natively (issue #500, the pins).

The pin map is a package-wide fact: the checker proves the type of every
argument at every call site, and the sites that agree pin the parameter.
The pin lifts the PS1001 parameter fence, so the definition enters the
export table and the caller's call site lowers to a direct native call.

The case below is a two-module program whose helper is unannotated. The
checker proves `text: str` at all three call sites, so the pin lifts the
fence, the helper lowers natively, and the binary calls it directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import subprocess

from tests.test_gates import run_cli, write_case

_HELPER = (
    "def parse_count(text, default=0):\n"
    "    try:\n"
    "        return int(text)\n"
    "    except (ValueError, TypeError):\n"
    "        return default\n"
)
_MAIN = (
    "from helpers import parse_count\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(parse_count('42'))\n"
    "    print(parse_count('41') + 1)\n"
    "    print(parse_count('nope', default=-1))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _pins(root: Path):
    """The pin map `pyscriptc infer` builds for a tree."""

    from pyscriptc.inferrunner import build_facts, facts_as_pins

    return facts_as_pins(build_facts(root))


def test_the_pin_reaches_the_definition_it_proves(tmp_path: Path) -> None:
    from pyscriptc.inferrunner import _module_name

    root = tmp_path / "pinned"
    root.mkdir(parents=True, exist_ok=True)
    helpers = write_case(root, "helpers.py", _HELPER)
    write_case(root, "__main__.py", _MAIN)
    # The pin is keyed by the qualified name of the callee, and the lowerer
    # asks for the name it lowers the definition under.
    assert _module_name(helpers, root) == "helpers"
    pins = _pins(root)
    assert pins.pins_for("helpers.parse_count")["text"].kind == "str"


def test_the_pinned_parameter_lifts_the_fence(tmp_path: Path) -> None:
    """Without the pin the parameter fences; with it the def lowers."""

    from pyscriptc.factsbridge import BridgeProvider, CheckerBridge
    from pyscriptc.inferrunner import pinned_facts
    from pyscriptc.package import lower_directory

    root = tmp_path / "pinned"
    root.mkdir(parents=True, exist_ok=True)
    write_case(root, "helpers.py", _HELPER)
    write_case(root, "__main__.py", _MAIN)

    facts = __import__("pyscriptc.inferrunner", fromlist=["build_facts"]).build_facts(root)
    pin_map = __import__("pyscriptc.inferrunner", fromlist=["facts_as_pins"]).facts_as_pins(facts)
    facts_by_file = pinned_facts(root, facts)

    class _Provider(BridgeProvider):
        def for_file(self, path: Path):
            found = facts_by_file.get(path.resolve())
            bridge = CheckerBridge(found, self.records) if found is not None else super().for_file(path)
            bridge.call_site_facts = pin_map
            return bridge

    provider = _Provider("pyrefly", strict=False)
    provider.open()
    try:
        module, _source = lower_directory(root, program_mode=True, bridge=provider, config=None)
    finally:
        provider.close()

    names = [fn["name"] for fn in module["functions"]]
    assert "helpers.parse_count" in names
    fences = [fence for fence in module["fences"] if fence["code"] == "PS1001"]
    assert not [fence for fence in fences if "text needs an annotation" in fence["message"]]


def test_the_pinned_helper_runs_native_and_matches_cpython(tmp_path: Path) -> None:
    """The binary calls the helper natively and agrees with CPython."""

    root = tmp_path / "pinned"
    root.mkdir(parents=True, exist_ok=True)
    write_case(root, "helpers.py", _HELPER)
    write_case(root, "__main__.py", _MAIN)

    # `pyscriptc compile --infer` applies the package-wide pins: the
    # checker proves the type of every argument at every call site, and
    # the sites that agree pin the parameter.
    proc = run_cli(
        "compile",
        str(root),
        "--program",
        "--infer",
        "--emit",
        "bin",
        "--out-dir",
        str(tmp_path / "out"),
        "--skip-check",
    )
    assert proc.returncode == 0, proc.stderr
    binary = next((tmp_path / "out").rglob("pinned"))
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0
    assert run.stdout == "42\n42\n-1\n"

    # The helper is a native LLVM function, not a call into the interpreter.
    ll = (tmp_path / "out" / "pinned.ll").read_text(encoding="utf-8")
    assert "define i64 @helpers.parse_count" in ll


def test_the_unpinned_helper_keeps_its_fence(tmp_path: Path) -> None:
    """Without the pins the same helper fences, so the proof is the pins."""

    from pyscriptc.package import lower_directory

    root = tmp_path / "pinned"
    root.mkdir(parents=True, exist_ok=True)
    write_case(root, "helpers.py", _HELPER)
    write_case(root, "__main__.py", _MAIN)

    module, _source = lower_directory(root, program_mode=True, bridge=None, config=None)
    names = [fn["name"] for fn in module["functions"]]
    assert "helpers.parse_count" not in names
    messages = [fence["message"] for fence in module["fences"]]
    assert any("text needs an annotation" in message for message in messages)
