"""A module-level name bound to an island value adopts the island's object.

With `--island`, the Stage 2 seed execs the module's island-lane imports and
its literal or deterministic module assignments island-side, and every
definition the planner routed to the island defines itself there. A module
assignment like `app = Flask()` therefore ran twice: once island-side in the
seed and once natively in the entry, so the routes registered by the island
decorators landed on the seed's object while the served object was a second,
routeless instance (every request answered 404). The binding now takes a
global slot whose initializer is the def-handle trampoline: the native side
reads the object the seed built instead of building another.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.boxed import boxed_definitions
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import island_policy

ROOT = Path(__file__).resolve().parents[1]

MINIAPP = """\
class App:
    def __init__(self) -> None:
        self.routes = []

    def route(self, path: str):
        def deco(fn):
            self.routes.append(path)
            return fn

        return deco

    def run(self, port: int) -> None:
        print("routes:", ",".join(self.routes))
"""

# The native top level shape: the entry probe clears (an island method call
# on a handle lowers), so the entry lowers natively and the assignment is
# the one binding of `app`.
MAIN = """\
from miniapp import App

app = App()


@app.route("/")
def index():
    return "hi"


if __name__ == "__main__":
    app.run(8080)
"""


def _lower(source: str) -> dict:
    with boxed_definitions(True), island_policy(["miniapp"], default=True):
        return lower_source(source, "main.py")


def test_module_island_value_binding_takes_one_slot(tmp_path: Path) -> None:
    """`app = App()` binds a slot; the entry reads the seed's object."""
    (tmp_path / "miniapp.py").write_text(MINIAPP, encoding="utf-8")
    module = _lower(MAIN)
    assert module["fences"] == []
    validate_module(module)
    # The value is an island handle adopted from the seed, not a second
    # construction: the module's global initializer is the def-handle
    # trampoline over the island name, so the native side reads the object
    # the seed built.
    inits = module.get("globals") or []
    handles = [arg for init in inits for arg in init.get("args", []) if arg.get("symbol") == "ps_island_def_handle"]
    assert handles, module.get("globals")
    assert handles[0]["island"] == {"name": "app"}
    # The entry carries no second construction of the island value.
    entry = next(fn for fn in module["functions"] if fn["name"] == "main")
    body = json.dumps(entry["body"])
    assert "ps_island_def_handle" not in body


def test_module_island_value_binary_matches_cpython(tmp_path: Path) -> None:
    """End to end: the compiled binary registers the route on the served app."""
    (tmp_path / "miniapp.py").write_text(MINIAPP, encoding="utf-8")
    (tmp_path / "main.py").write_text(MAIN, encoding="utf-8")
    binary = tmp_path / "main_bin"
    env = {"PYTHONPATH": str(tmp_path)}
    comp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(tmp_path / "main.py"),
            "--island",
            "--emit",
            "bin",
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**__import__("os").environ, **env},
        timeout=300,
    )
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
        timeout=60,
    )
    cpython = subprocess.run(
        [sys.executable, str(tmp_path / "main.py")],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
        timeout=60,
    )
    assert run.returncode == cpython.returncode
    assert run.stdout == cpython.stdout
    assert run.stdout == "routes: /\n"


@pytest.mark.parametrize("marker", ["app.run(8080)"])
def test_the_repro_shape_needs_a_native_top_level(marker: str) -> None:
    """Guard: the repro really exercises the native top-level shape."""
    module = _lower(MAIN)
    assert module["fallback"]["moduleTopLevel"] == "native"
    assert module["fallback"]["functions"]["index"] == "island"
