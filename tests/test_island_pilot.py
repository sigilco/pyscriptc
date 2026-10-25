"""Receipt and test suite for the island pilot (issue #553).

A typed program calls `requests.get` against a local http.server (no internet in
CI); the compiled binary matches CPython output on status code and body length.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from benchmarks.net.server import BODY, Server

ROOT = Path(__file__).resolve().parents[1]
_BODY_LEN = len(BODY)


def _run_cli(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_island_requests_get_receipt_matches_cpython(tmp_path: Path) -> None:
    """The eye-opening receipt: requests.get from a typed binary matches CPython."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nrequests = "island"\n', encoding="utf-8")
    with Server(threaded=False) as server:
        source_text = f"""import requests

def main() -> None:
    url = "http://127.0.0.1:{server.port}/"
    response = requests.get(url)
    code: int = response.status_code
    text: str = response.text
    print(code, len(text))

if __name__ == "__main__":
    main()
"""
        src = tmp_path / "receipt.py"
        src.write_text(source_text, encoding="utf-8")
        binary = tmp_path / "receipt_bin"

        comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr

        native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60)

        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout == f"200 {_BODY_LEN}\n"


def test_island_requests_get_zero_config_cli_flag(tmp_path: Path) -> None:
    """--island CLI flag routes untyped PyPI packages with zero config."""
    with Server(threaded=False) as server:
        source_text = f"""import requests

def main() -> None:
    url = "http://127.0.0.1:{server.port}/"
    response = requests.get(url)
    code: int = response.status_code
    text: str = response.text
    print(code, len(text))

if __name__ == "__main__":
    main()
"""
        src = tmp_path / "zero_config.py"
        src.write_text(source_text, encoding="utf-8")
        binary = tmp_path / "zero_config_bin"

        comp = _run_cli("compile", "--island", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr

        native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60)

        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout == f"200 {_BODY_LEN}\n"


def test_island_requests_get_bytes_content(tmp_path: Path) -> None:
    """Reading bytes from an island response handle via content attribute."""
    with Server(threaded=False) as server:
        source_text = f"""import requests

def main() -> None:
    url = "http://127.0.0.1:{server.port}/"
    response = requests.get(url)
    code: int = response.status_code
    content: bytes = response.content
    print(code, len(content))

if __name__ == "__main__":
    main()
"""
        src = tmp_path / "bytes_case.py"
        src.write_text(source_text, encoding="utf-8")
        binary = tmp_path / "bytes_case_bin"

        comp = _run_cli("compile", "--island", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr

        native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60)

        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout == f"200 {_BODY_LEN}\n"


def test_island_calls_from_four_threads(tmp_path: Path) -> None:
    """Four threads calling requests.get concurrently remain correct and GIL-safe."""
    with Server(threaded=True) as server:
        source_text = f"""import requests
import threading

errors = 0
lock: threading.Lock = threading.Lock()

def worker(url: str) -> None:
    global errors
    i: int = 0
    while i < 3:
        r = requests.get(url)
        code: int = r.status_code
        text: str = r.text
        if code != 200 or len(text) != {_BODY_LEN}:
            lock.acquire()
            errors = errors + 1
            lock.release()
        i = i + 1

def main() -> None:
    url = "http://127.0.0.1:{server.port}/"
    t1: threading.Thread = threading.Thread(target=worker, args=(url,))
    t2: threading.Thread = threading.Thread(target=worker, args=(url,))
    t3: threading.Thread = threading.Thread(target=worker, args=(url,))
    t4: threading.Thread = threading.Thread(target=worker, args=(url,))
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t1.join()
    t2.join()
    t3.join()
    t4.join()
    print("errors:", errors)

if __name__ == "__main__":
    main()
"""
        src = tmp_path / "thread_test.py"
        src.write_text(source_text, encoding="utf-8")
        binary = tmp_path / "thread_test_bin"

        comp = _run_cli("compile", "--island", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr

        native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60)

        assert native.returncode == 0, native.stderr
        assert cpython.returncode == 0, cpython.stderr
        assert native.stdout == cpython.stdout
        assert native.stdout == "errors: 0\n"


def test_island_getattr_unannotated_fences(tmp_path: Path) -> None:
    """An unannotated handle read fences without the fallback tier; under it, main joins the island (issue #612)."""
    src = tmp_path / "fence_case.py"
    src.write_text(
        """import requests

def main() -> None:
    r = requests.get("http://127.0.0.1:8000")
    print(r.status_code)
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), "--emit", "ir", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS2002" in comp.stderr

    # The per-definition fallback tier (issue #612): main's body fences, so
    # the planner routes main to the island, where the read is plain CPython.
    # `--island` is the alias of `--fallback=cpython`, so it compiles too.
    comp_fb = _run_cli("compile", "--fallback=cpython", str(src), "--emit", "ir", cwd=tmp_path)
    assert comp_fb.returncode == 0, comp_fb.stderr
    comp_island = _run_cli("compile", "--island", str(src), "--emit", "ir", cwd=tmp_path)
    assert comp_island.returncode == 0, comp_island.stderr


def test_island_runtime_resolves_local_module_in_program_dir(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text(
        """class Box:
    def __init__(self, msg: str):
        self.msg = msg

def make_box(text: str) -> Box:
    return Box(f"boxed:{text}")
""",
        encoding="utf-8",
    )
    src = tmp_path / "app.py"
    src.write_text(
        """import helper

def main() -> None:
    b = helper.make_box("hello")
    msg: str = b.msg
    print(msg)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "app_bin"
    comp = _run_cli("compile", "--island", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    # Execute from a different working directory (/tmp)
    run = subprocess.run([str(binary)], cwd="/tmp", capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "boxed:hello"


def test_island_runtime_respects_pythonpath(tmp_path: Path) -> None:
    ext_dir = tmp_path / "somewhere_else"
    ext_dir.mkdir()
    (ext_dir / "extmod.py").write_text(
        """class Box:
    def __init__(self, val: int):
        self.val = val

def answer() -> Box:
    return Box(42)
""",
        encoding="utf-8",
    )
    src = tmp_path / "path_test.py"
    src.write_text(
        """import extmod

def main() -> None:
    b = extmod.answer()
    val: int = b.val
    print("val:", val)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "path_test_bin"
    comp = _run_cli(
        "compile",
        "--island",
        str(src),
        "--emit",
        "bin",
        "-o",
        str(binary),
        cwd=tmp_path,
        env={"PYTHONPATH": str(ext_dir)},
    )
    assert comp.returncode == 0, comp.stderr

    run = subprocess.run(
        [str(binary)],
        cwd="/tmp",
        env={"PYTHONPATH": str(ext_dir)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "val: 42"


def test_island_import_forms_submodule_and_from_import(tmp_path: Path) -> None:
    src = tmp_path / "forms_test.py"
    src.write_text(
        """import packaging.version
from packaging.version import parse

def main() -> None:
    v1 = packaging.version.parse("1.2.3")
    m1: int = v1.major
    v2 = parse("4.5.6")
    m2: int = v2.major
    print(m1, m2)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "forms_test_bin"
    comp = _run_cli("compile", "--island", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "1 4"
