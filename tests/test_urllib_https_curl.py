"""https:// over libcurl, linked at build time (issue #235).

The driver adds libcurl's link flags (resolved through `curl-config --libs`
or `pkg-config libcurl`) to the link line of a program whose emitted IR
reaches an https:// URL, and only those. When neither tool finds libcurl
the compile fails with a diagnostic naming the package to install, and
`PSC_CURL_DYNAMIC=1` opts out of the build-time link (the https request
then raises URLError at run time, the way it did before the curl path
existed). No network here: the unit tests drive `curl_link_flags` with a
mocked tool lookup, and the smoke compiles a program with the dynamic opt
out and checks the run-time refusal, which fires before any connection.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from tests.test_gates import compile_run, write_case

from pyscriptc.emit.llvm import _driver
from pyscriptc.errors import IRError

HTTPS_IR = 'declare ptr @ps_http_request(...)\n@url = constant [8 x i8] c"https://\\00"\n'
HTTP_IR = 'declare ptr @ps_http_request(...)\n@url = constant [7 x i8] c"http://\\00"\n'


def test_http_only_program_adds_no_flags() -> None:
    assert _driver.curl_link_flags(HTTP_IR) == []


def test_program_without_the_http_client_adds_no_flags() -> None:
    assert _driver.curl_link_flags('@other = constant [8 x i8] c"https://\\00"\n') == []


def test_https_flags_come_from_curl_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="-lcurl -lssl\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _driver.curl_link_flags(HTTPS_IR) == ["-lcurl", "-lssl"]
    assert seen == [["/usr/bin/curl-config", "--libs"]]


def test_https_flags_fall_back_to_pkg_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pkg-config" if name == "pkg-config" else None)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="-lcurl\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _driver.curl_link_flags(HTTPS_IR) == ["-lcurl"]


def test_missing_libcurl_fails_with_a_naming_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(IRError) as excinfo:
        _driver.curl_link_flags(HTTPS_IR)
    message = str(excinfo.value)
    assert "libcurl" in message
    assert "apt install" in message
    assert "PSC_CURL_DYNAMIC" in message


def test_failing_tools_fail_with_the_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="missing")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(IRError, match="libcurl"):
        _driver.curl_link_flags(HTTPS_IR)


def test_dynamic_env_skips_the_build_time_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_driver.PS_CURL_DYNAMIC_ENV, "1")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _driver.curl_link_flags(HTTPS_IR) == []


def test_wasm_target_never_links_libcurl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _driver.curl_link_flags(HTTPS_IR, "wasm32-wasi") == []


def test_dynamic_build_raises_urlerror_at_run_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-out build links without libcurl; https then refuses at run time."""
    from tests import test_gates

    monkeypatch.setitem(test_gates.ENV, _driver.PS_CURL_DYNAMIC_ENV, "1")
    path = write_case(
        tmp_path,
        "https_refused.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    try:",
                "        urllib.request.urlopen(url)",
                "    except OSError:",
                "        return 42",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr(["https://127.0.0.1:1/"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "42\n"
