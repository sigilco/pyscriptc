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

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.test_gates import compile_run, write_case

from pyscriptc.emit.llvm import _driver
from pyscriptc.errors import IRError
from pyscriptc.llvm import link_binary

HTTPS_IR = 'declare ptr @ps_http_request(...)\n@url = constant [9 x i8] c"https://\\00"\n'
HTTP_IR = 'declare ptr @ps_http_request(...)\n@url = constant [8 x i8] c"http://\\00"\n'
HTTP_LL = "define i32 @main() {\n  ret i32 0\n}\n"


def _link_record(tmp_path: Path, **kwargs) -> list[list[str]]:
    """Link a no-https module with the runtime object cache off, so the
    recorded cc argv is exact."""
    ll = tmp_path / "m.ll"
    ll.write_text(HTTP_LL, encoding="utf-8")
    record: list[list[str]] = []
    link_binary(ll, tmp_path / "a", record=record, **kwargs)
    return record


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


def test_no_curl_symbol_on_a_no_https_link(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A program that never reaches https:// links with no curl symbol.

    The driver only defines PSC_HAVE_CURL (and only adds libcurl's link
    flags) when the IR reaches an https:// URL, so ps_http_curl.c compiles
    to the stub: no curl_* reference reaches the object files and the link
    succeeds on any platform, including Mach-O where an undefined symbol is
    a hard error whatever the declaration says.
    """
    monkeypatch.setenv("PYSC_NO_RT_CACHE", "1")
    record = _link_record(tmp_path)
    link = record[-1]
    compiles = [cmd for cmd in record if "-c" in cmd]
    assert compiles, "no cc compile commands recorded"
    for cmd in compiles:
        assert "-DPSC_HAVE_CURL" not in cmd, cmd
    assert not any("-lcurl" in arg or arg.startswith("-l") for arg in link if arg not in ("-lm",)), link
    assert "-lm" in link


def test_https_ir_gains_the_define_and_the_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The mirror side: when the IR reaches https://, the runtime units
    compile with PSC_HAVE_CURL and the link line carries libcurl's flags."""
    monkeypatch.setattr(_driver, "curl_link_flags", lambda text, target=None: ["-lcurl"])
    monkeypatch.setenv("PYSC_NO_RT_CACHE", "1")
    ll = tmp_path / "m.ll"
    ll.write_text(HTTPS_IR + HTTP_LL, encoding="utf-8")
    record: list[list[str]] = []
    # The link itself may fail where the host lacks the libcurl dev .so; the
    # recorded argv is complete either way, and the argv is what this pins.
    try:
        link_binary(ll, tmp_path / "a", record=record)
    except IRError:
        pass
    compiles = [cmd for cmd in record if "-c" in cmd]
    assert compiles, "no cc compile commands recorded"
    runtime_compiles = [cmd for cmd in compiles if any("ps_http_curl.c" in arg for arg in cmd)]
    assert runtime_compiles, "ps_http_curl.c was not compiled"
    for cmd in runtime_compiles:
        assert "-DPSC_HAVE_CURL" in cmd, cmd
    assert "-lcurl" in record[-1]


def test_a_compiled_no_https_binary_has_no_curl_symbol(tmp_path: Path) -> None:
    """End to end: compile a urlopen http:// program and check the binary.

    `nm` on the output must find no curl symbol, the regression the macOS
    CI hit (`Undefined symbols for architecture arm64: _curl_easy_strerror`).
    Skipped where nm is unavailable (the assertion needs a symbol table)."""
    if shutil.which("nm") is None:
        pytest.skip("needs nm")
    path = write_case(
        tmp_path,
        "http_only.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    try:",
                "        r = urllib.request.urlopen(url)",
                "    except OSError:",
                "        return 7",
                "    return len(r.read())",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr(["http://127.0.0.1:1/"]))
    assert got.returncode == 0, got.stderr
    binary = tmp_path / "f"
    assert binary.exists()
    symbols = subprocess.run(["nm", str(binary)], capture_output=True, text=True)
    assert symbols.returncode == 0, symbols.stderr
    libcurl_symbols = [line for line in symbols.stdout.splitlines() if re.search(r"\bcurl_(easy|slist)_", line)]
    assert libcurl_symbols == [], symbols.stdout
