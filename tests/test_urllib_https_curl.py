"""urllib.request over https:// through dlopen'ed libcurl (issue #235).

The native client reaches an https:// URL through libcurl, loaded at run time
so a binary keeps working on a machine without it. The tests here drive that
path with a fake libcurl: a small shared library that exports the seven easy
API symbols the runtime resolves, records the options it saw and answers with
a canned response. No network, no libcurl on the machine, no sleep.

The runtime honours PSC_LIBCURL_PATH for the library to load, and the shim
honours PSC_CURL_SHIM_RESPONSE (the canned HTTP response bytes) and
PSC_CURL_SHIM_LOG (where it records the options it saw).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case

SHIM_SOURCE = Path(__file__).resolve().parent / "fixtures" / "http_curl_shim" / "fake_curl.c"

# The options the runtime sets. The shim and the runtime have to agree on the
# numbers, because neither of them ships the curl headers.
OPT_URL = 10002
OPT_POSTFIELDS = 10015
OPT_HTTPHEADER = 10023
OPT_CUSTOMREQUEST = 10036
OPT_POST = 47
OPT_NOBODY = 44
OPT_FOLLOWLOCATION = 52
OPT_POSTFIELDSIZE_LARGE = 30120
OPT_SSL_VERIFYPEER = 64
OPT_HEADERFUNCTION = 20079
OPT_SSL_VERIFYHOST = 81
OPT_TIMEOUT_MS = 155

CC = shutil.which("cc") or shutil.which("gcc")


def _build_shim(tmp_path: Path) -> Path:
    """Build the fake libcurl into a shared library."""
    if CC is None:
        pytest.skip("no C compiler on this machine")
    library = tmp_path / "libfakecurl.so"
    proc = subprocess.run(
        [CC, "-shared", "-fPIC", "-O1", "-o", str(library), str(SHIM_SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return library


def _run_with_shim(
    tmp_path: Path,
    source: Path,
    func: str,
    args: str,
    library: Path,
    response: bytes | None = None,
    log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Compile and run `source` with the fake libcurl as the https backend."""
    env = {**os.environ, "PSC_LIBCURL_PATH": str(library)}
    if response is not None:
        canned = tmp_path / "response.bin"
        canned.write_bytes(response)
        env["PSC_CURL_SHIM_RESPONSE"] = str(canned)
    if log is not None:
        env["PSC_CURL_SHIM_LOG"] = str(log)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--run",
            "--call",
            func,
            "--args",
            args,
            "-o",
            str(tmp_path / func),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _recorded(log: Path) -> dict[str, str]:
    """The options the shim recorded, as a name to value mapping."""
    values: dict[str, str] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            values.setdefault(name, value)
    return values


def _recorded_all(log: Path, name: str) -> list[str]:
    """Every value the shim recorded under `name` (the headers repeat)."""
    out: list[str] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            out.append(line[len(name) + 1 :])
    return out


def _case(tmp_path: Path, name: str, body: str) -> Path:
    return write_case(tmp_path, name, body)


# A canned 200 response: a status line, two header rows and the body.
CANNED_OK = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nX-Marker: canned\r\nContent-Length: 5\r\n\r\nhello"


def test_https_reads_the_canned_response(tmp_path: Path) -> None:
    """An https:// request over the shim returns the canned status and body."""
    library = _build_shim(tmp_path)
    path = _case(
        tmp_path,
        "curl_get.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/plain"]), library, CANNED_OK)
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_https_reports_the_status_and_the_headers(tmp_path: Path) -> None:
    """The status and the header block come from the canned response."""
    library = _build_shim(tmp_path)
    path = _case(
        tmp_path,
        "curl_status.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    info: str = r.info()",
                "    return str(r.status) + '|' + info",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/plain"]), library, CANNED_OK)
    assert got.returncode == 0, got.stderr
    assert got.stdout.startswith("200|")
    assert "X-Marker: canned" in got.stdout


def test_verification_is_always_on(tmp_path: Path) -> None:
    """The peer certificate and the host name are verified: never disabled."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_verify.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/plain"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    recorded = _recorded(log)
    # CURLOPT_SSL_VERIFYPEER is 1 and CURLOPT_SSL_VERIFYHOST is 2, the
    # libcurl defaults: the certificate must chain to the platform trust
    # store and the name must match the host.
    assert recorded["verifypeer"] == "1"
    assert recorded["verifyhost"] == "2"


def test_request_headers_and_body_reach_libcurl(tmp_path: Path) -> None:
    """The headers and the body of a Request reach the curl options."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_post.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(",
                "        url, data=b'payload', headers={'X-Marker': 'yes'}",
                "    )",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/x"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    recorded = _recorded(log)
    assert recorded["method"] == "POST"
    assert recorded["postfields"] == "payload"
    assert recorded["postfieldsize"] == str(len("payload"))
    assert "X-Marker: yes" in _recorded_all(log, "header")


def test_the_url_reaches_libcurl(tmp_path: Path) -> None:
    """The request URL is the one the script asked for."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_url.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/deep/path"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    assert _recorded(log)["url"] == "https://example.com/deep/path"


def test_the_timeout_is_applied(tmp_path: Path) -> None:
    """The timeout rides the request as the millisecond budget."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_timeout.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url, timeout=2.5)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/x"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    ms = int(_recorded(log)["timeout_ms"])
    # The budget is the timeout the caller gave, and the deadline is a whole
    # request, so the milliseconds are just under it.
    assert 2400 <= ms <= 2500


def test_no_timeout_means_no_budget(tmp_path: Path) -> None:
    """Without a timeout the request carries no millisecond budget."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_notimeout.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/x"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    assert _recorded(log)["timeout_ms"] == "0"


def test_redirects_are_not_followed_by_libcurl(tmp_path: Path) -> None:
    """The client drives the redirects, so libcurl follows none itself."""
    library = _build_shim(tmp_path)
    log = tmp_path / "options.log"
    path = _case(
        tmp_path,
        "curl_redirect.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = _run_with_shim(tmp_path, path, "f", repr(["https://example.com/x"]), library, CANNED_OK, log)
    assert got.returncode == 0, got.stderr
    assert _recorded(log)["followlocation"] == "0"


def test_a_missing_libcurl_raises_the_documented_error(tmp_path: Path) -> None:
    """Without libcurl on the machine the https:// request raises URLError."""
    path = _case(
        tmp_path,
        "curl_missing.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    caught: str = ''",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError as e:",
                "        caught = str(e)",
                "    return caught",
                "",
            ]
        ),
    )
    # A path that holds no library: the load fails and the documented error
    # comes up.
    env = {**os.environ, "PSC_LIBCURL_PATH": str(tmp_path / "no-such-libcurl.so")}
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--call",
            "f",
            "--args",
            repr(["https://example.com/"]),
            "-o",
            str(tmp_path / "curl_missing"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert got.returncode == 0, got.stderr
    assert "https is not supported" in got.stdout
    assert "libcurl" in got.stdout


def test_http_does_not_need_libcurl(tmp_path: Path) -> None:
    """The plaintext path never loads libcurl, so it works without it."""
    path = _case(
        tmp_path,
        "curl_http.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    caught: str = ''",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError as e:",
                "        caught = str(e)",
                "    return caught",
                "",
            ]
        ),
    )
    env = {**os.environ, "PSC_LIBCURL_PATH": str(tmp_path / "no-such-libcurl.so")}
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--call",
            "f",
            "--args",
            repr(["http://127.0.0.1:1/x"]),
            "-o",
            str(tmp_path / "curl_http"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert got.returncode == 0, got.stderr
    # The failure is the connection, not the missing libcurl.
    assert "https is not supported" not in got.stdout
