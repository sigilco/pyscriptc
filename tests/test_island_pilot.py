"""Receipt and test suite for the island pilot (issue #553).

A typed program calls `requests.get` against a local http.server (no internet in
CI); the compiled binary matches CPython output on status code and body length.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from benchmarks.net.server import BODY, Server

ROOT = Path(__file__).resolve().parents[1]
_BODY_LEN = len(BODY)


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
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
    """Attribute access on an island handle without annotation fences with PS1001."""
    src = tmp_path / "fence_case.py"
    src.write_text(
        """import requests

def main() -> None:
    r = requests.get("http://127.0.0.1:8000")
    print(r.status_code)
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", "--island", str(src), "--emit", "ir", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS1001" in comp.stderr
    assert "status_code needs an annotation" in comp.stderr
