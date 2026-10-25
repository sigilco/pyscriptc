"""Regression and conformance tests for island keyword arguments and *args/**kwargs (issue #605).

Island call sites (module attribute calls, from-imports routed to the island, and
island-planned definitions called via ps_island_fn_invoke) accept keyword arguments
and *args/**kwargs forwarding when argument values are lowerable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from benchmarks.net.server import Server

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_island_stdlib_textwrap_keywords(tmp_path: Path) -> None:
    """Calling textwrap.fill with keyword argument width=12 matches CPython."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntextwrap = "island"\n', encoding="utf-8")
    src = tmp_path / "app.py"
    src.write_text(
        """import textwrap

def main() -> None:
    text: str = "the quick brown fox jumps over the lazy dog"
    wrapped: str = textwrap.fill(text, width=15)
    print(wrapped)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "app_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_from_import_keywords(tmp_path: Path) -> None:
    """From-imported function routed to island called with keyword arguments."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntextwrap = "island"\n', encoding="utf-8")
    src = tmp_path / "from_imp.py"
    src.write_text(
        """from textwrap import fill

def main() -> None:
    out: str = fill("hello world from island call", width=10)
    print(out)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "from_imp_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_stdlib_json_dumps_keywords(tmp_path: Path) -> None:
    """json.dumps with indent and sort_keys keywords routed to island."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\njson = "island"\n', encoding="utf-8")
    src = tmp_path / "json_kw.py"
    src.write_text(
        """import json

def main() -> None:
    data: dict[str, int] = {"banana": 3, "apple": 5, "cherry": 1}
    out: str = json.dumps(data, indent=2, sort_keys=True)
    print(out)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "json_kw_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_planned_definition_keywords(tmp_path: Path) -> None:
    """An island-planned definition called with keywords from native code."""
    src = tmp_path / "fenced_def.py"
    src.write_text(
        """def format_entry(name, count=0, prefix="item: ", suffix=" units") -> str:
    # Unannotated parameter name and dynamic string concat fence -> island plane
    return prefix + str(name) + ": " + str(count) + suffix

def main() -> None:
    # Called natively with keyword arguments
    out1: str = format_entry("apples", count=42)
    print(out1)
    out2: str = format_entry("oranges", prefix="FRUIT -> ", suffix=" pcs", count=10)
    print(out2)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "fenced_def_bin"
    comp = _run_cli("compile", str(src), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_args_and_kwargs_forwarding(tmp_path: Path) -> None:
    """Forwarding *args and **kwargs from native code to an island function."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntextwrap = "island"\n', encoding="utf-8")
    src = tmp_path / "forward.py"
    src.write_text(
        """import textwrap

def compute_total(label, *nums) -> str:
    # Fences -> runs on island
    total = sum(nums)
    return str(label) + ": " + str(total)

def main() -> None:
    # 1. *args forwarding to island function
    nums: list[int] = [10, 20, 30, 40]
    res1: str = compute_total("SUM", *nums)
    print(res1)

    # 2. **kwargs forwarding to island module
    opts: dict[str, int] = {"width": 10}
    wrapped: str = textwrap.fill("short line wraps here", **opts)
    print(wrapped)

    # 3. Both *args and **kwargs together
    args: list[str] = ["the lazy fox jumped quickly"]
    wrapped2: str = textwrap.fill(*args, **opts)
    print(wrapped2)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "forward_bin"
    comp = _run_cli("compile", str(src), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_handle_method_keywords(tmp_path: Path) -> None:
    """Calling a method on an island handle with keyword arguments."""
    mod = tmp_path / "builder.py"
    mod.write_text(
        """class Builder:
    def __init__(self, sep):
        self.sep = sep
    def join(self, parts, prefix="", suffix=""):
        return prefix + self.sep.join(parts) + suffix

def get_builder(sep):
    return Builder(sep)
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nbuilder = "island"\n', encoding="utf-8")
    src = tmp_path / "method_kw.py"
    src.write_text(
        """import builder

def main() -> None:
    b = builder.get_builder(" - ")
    parts: list[str] = ["one", "two", "three"]
    out: str = b.join(parts, prefix="<", suffix=">")
    print(out)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "method_kw_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_unlowerable_arg_fences(tmp_path: Path) -> None:
    """An unlowerable argument value at an island call site triggers PS1003."""
    mod = tmp_path / "custom_island.py"
    mod.write_text("def run(item=None):\n    pass\n", encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncustom_island = "island"\n', encoding="utf-8")
    src = tmp_path / "bad_kw.py"
    src.write_text(
        """import custom_island

class Custom:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def main() -> None:
    c = Custom(1)
    custom_island.run(item=c)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), "--skip-check", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS1003" in comp.stderr


def test_island_requests_get_timeout_repro_issue_605(tmp_path: Path) -> None:
    """Repro from issue #605: requests.get with timeout keyword argument."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nrequests = "island"\n', encoding="utf-8")
    with Server(threaded=False) as server:
        src = tmp_path / "app.py"
        src.write_text(
            f"""import requests

def main() -> None:
    url = "http://127.0.0.1:{server.port}/"
    r = requests.get(url, timeout=5)
    code: int = r.status_code
    print(code)

if __name__ == "__main__":
    main()
""",
            encoding="utf-8",
        )
        binary = tmp_path / "app_bin"
        comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr

        native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
        cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout == "200\n"


def test_island_kwargs_non_string_key_fences(tmp_path: Path) -> None:
    """**kwargs with non-string keys fences with PS1003."""
    mod = tmp_path / "custom_island.py"
    mod.write_text("def run(**kwargs):\n    pass\n", encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncustom_island = "island"\n', encoding="utf-8")
    src = tmp_path / "bad_kwargs_key.py"
    src.write_text(
        """import custom_island

def main() -> None:
    d: dict[int, int] = {1: 2}
    custom_island.run(**d)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), "--skip-check", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS1003" in comp.stderr
