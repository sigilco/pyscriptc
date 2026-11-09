"""Tests for src/pyscriptc/telemetry.py. No network: urlopen is mocked."""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import uuid
from pathlib import Path

import pytest

from pyscriptc import telemetry


class _SyncThread:
    """Drop-in for threading.Thread that runs the target synchronously."""

    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self._target = target
        self._args = args

    def start(self):
        if self._target:
            self._target(*self._args)

    def join(self, timeout=None):
        pass


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Isolate env/config, mock urllib, return the list of payloads PostHog got."""
    sent = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        assert timeout == telemetry._TIMEOUT_S
        sent.append(json.loads(request.data.decode()))
        return _Resp()

    for name in ("CI", "PYSC_NO_TELEMETRY", "DO_NOT_TRACK", "PYSC_POSTHOG_TOKEN", "PYSC_INTERNAL"):
        monkeypatch.delenv(name, raising=False)
    # A dev checkout looks internal (repo src/ or an editable finder on sys.path);
    # scrub those entries so the sending tests exercise the normal user path.
    scrubbed = [p for p in telemetry.sys.path if "__editable__" not in p and not p.rstrip("/").endswith("/src")]
    monkeypatch.setattr(telemetry.sys, "path", scrubbed, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(telemetry, "_notice_shown_now", True)
    monkeypatch.setattr(telemetry.threading, "Thread", _SyncThread)
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_empty_token_is_noop(captured):
    assert not telemetry.enabled()
    telemetry.track("cli.invoked", {})
    assert captured == []


def test_payload_shape_and_stable_distinct_id(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    assert telemetry.enabled()
    telemetry.track("cli.invoked", {"subcommand": "compile"})
    telemetry.track("compile.finished", {"duration_ms": 12, "ok": True})
    assert len(captured) == 2
    first, second = captured
    assert first["api_key"] == "phc_test"
    assert first["event"] == "cli.invoked"
    assert second["event"] == "compile.finished"
    assert uuid.UUID(first["distinct_id"]).version == 4
    assert first["distinct_id"] == second["distinct_id"]
    # No PII-ish fields anywhere in the payload.
    blob = json.dumps(first).replace("profile", "")
    for banned in ("hostname", "machine-id", "path", "file", "source", "user"):
        assert banned not in blob


def _no_hardware(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("hardware identifier read")

    monkeypatch.setattr(uuid, "getnode", boom)
    monkeypatch.setattr(socket, "gethostname", boom)
    real_read_bytes = Path.read_bytes

    def guarded(self):
        if "machine-id" in str(self):
            boom()
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)


def test_distinct_id_stable_across_calls_and_no_hardware_reads(captured, monkeypatch, tmp_path):
    _no_hardware(monkeypatch)
    first = telemetry._install_id()
    assert first == telemetry._install_id()
    id_file = tmp_path / "pyscriptc" / "telemetry-id"
    assert id_file.read_text().strip() == first
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(id_file).st_mode) == 0o600


def test_reset_id_creates_new_id(captured, monkeypatch, tmp_path, capsys):
    first = telemetry._install_id()
    assert telemetry.telemetry_cmd("reset-id") == 0
    assert not (tmp_path / "pyscriptc" / "telemetry-id").exists()
    assert telemetry._install_id() != first


def test_payload_has_no_ip_and_privacy_flags(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    telemetry.track("cli.invoked", {"subcommand": "compile"})
    props = captured[0]["properties"]
    assert "$ip" in props and props["$ip"] is None
    assert props["$geoip_disable"] is True
    assert props["$process_person_profile"] is False
    assert telemetry.POSTHOG_HOST == "https://eu.i.posthog.com"


def test_base_properties_minimal():
    assert set(telemetry.base_properties()) == {"pyscriptc_version", "python_version", "platform", "internal"}


def test_opt_in_mode(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setattr(telemetry, "OPT_IN", True)
    monkeypatch.setattr(telemetry, "_notice_shown_now", False)
    _tty(monkeypatch)
    assert not telemetry.enabled()
    telemetry.maybe_print_notice()
    assert "pyscriptc telemetry enable" in capsys.readouterr().err
    telemetry.track("cli.invoked", {})
    assert captured == []
    telemetry.telemetry_cmd("enable")
    telemetry.track("cli.invoked", {})
    assert len(captured) == 1


@pytest.mark.parametrize("envar", ["PYSC_NO_TELEMETRY", "DO_NOT_TRACK"])
def test_opt_out_env_vars(captured, monkeypatch, envar):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setenv(envar, "1")
    assert not telemetry.enabled()
    telemetry.track("cli.invoked", {})
    assert captured == []


def test_config_disable(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    config = tmp_path / "pyscriptc" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("telemetry = false\n", encoding="utf-8")
    assert not telemetry.enabled()


def test_telemetry_cmd_disable_enable(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    assert telemetry.telemetry_cmd("disable") == 0
    config = tmp_path / "pyscriptc" / "config.toml"
    assert "telemetry = false" in config.read_text()
    assert not telemetry.enabled()
    assert telemetry.telemetry_cmd("enable") == 0
    assert "telemetry = true" in config.read_text()
    assert telemetry.enabled()
    assert telemetry.telemetry_cmd("status") == 0
    assert telemetry.telemetry_cmd("bogus") == 2


def test_ci_silences_everything(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setenv("CI", "true")
    assert not telemetry.enabled()
    telemetry.maybe_print_notice()
    telemetry.track("cli.invoked", {})
    assert captured == []
    assert capsys.readouterr().err == ""


def _tty(monkeypatch):
    monkeypatch.setattr(telemetry.sys.stderr, "isatty", lambda: True, raising=False)


def test_notice_prints_exactly_once(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    _tty(monkeypatch)
    telemetry.maybe_print_notice()
    err = capsys.readouterr().err
    for needle in ("PYSC_NO_TELEMETRY=1", "pyscriptc telemetry disable", telemetry.NOTICE_URL, "Never source code"):
        assert needle in err
    telemetry.maybe_print_notice()
    assert capsys.readouterr().err == ""


def test_notice_survives_config_roundtrip(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    _tty(monkeypatch)
    telemetry.maybe_print_notice()
    # A second process would re-read the file: the flag must persist.
    config = (tmp_path / "pyscriptc" / "config.toml").read_text()
    assert "notice_seen = true" in config


def test_send_failure_is_swallowed(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")

    def boom(request, timeout=None):
        raise OSError("no network")

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", boom)
    telemetry.track("cli.invoked", {})  # must not raise


def test_cli_invoked_event_from_main(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    from pyscriptc.cli import main

    rc = main(["explain", "PS0001"])
    assert rc == 0
    assert any(p["event"] == "cli.invoked" and p["properties"]["subcommand"] == "explain" for p in captured)


# --- v1 schema: compile.finished / coverage.reported ---------------------------------

_SECRET = "zz_secret_name"
_OK_SRC = f"def {_SECRET}(x: int) -> int:\n    return x + 1\n"
_FENCED_SRC = "import zz_private_mod\n\n\ndef f(x: int) -> int:\n    return zz_private_mod.go(x)\n"


def _events(captured, name):
    return [p for p in captured if p["event"] == name]


def _run_cli(argv):
    from pyscriptc.cli import main

    return main(argv)


def test_compile_properties_schema():
    props = telemetry.compile_properties(
        duration_ms=5,
        outcome="fences",
        fence_count=3,
        fence_families={"union-types": 2, "stdlib": 1},
        checker="pyrefly",
        emit="bin",
        driver="cc",
    )
    for key in ("pyscriptc_version", "python_version", "platform"):
        assert key in props
    assert props["outcome"] == "fences"
    assert props["fence_count"] == 3
    assert props["fence_families"] == {"union-types": 2, "stdlib": 1}
    assert props["skip_check"] is False
    bare = telemetry.compile_properties(duration_ms=1, outcome="ok", fence_count=0, fence_families={})
    assert "checker" not in bare and "driver" not in bare


def test_coverage_properties_aggregates_and_collapses_stdlib():
    summary = {
        "files": 2,
        "definitions": 4,
        "nativeDefinitions": 3,
        "fencedDefinitions": 1,
        "fences": 3,
        "rootFences": 2,
        "byCode": {"PS1101": 3},
        "byFamily": {
            "union-types": {"fences": 1, "derived": False},
            "stdlib:zz_private_mod": {"fences": 1, "derived": False},
            "stdlib:other": {"fences": 1, "derived": False},
        },
    }
    props = telemetry.coverage_properties(summary)
    assert props["definitions"] == 4
    assert props["nativeDefinitions"] == 3
    assert props["fencedDefinitions"] == 1
    assert props["byFamily"] == {"union-types": 1, "stdlib": 2}
    assert "coverage_ratio" not in props and "files" not in props
    assert "byCode" not in props
    assert "zz_private_mod" not in json.dumps(props)

    props_fallback = telemetry.coverage_properties({"definitions": 5, "fencedDefinitions": 2})
    assert props_fallback["nativeDefinitions"] == 3


def test_compile_ok_event(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text(_OK_SRC)
    assert _run_cli(["compile", str(src), "--skip-check", "--emit", "ir"]) == 0
    (event,) = _events(captured, "compile.finished")
    props = event["properties"]
    assert props["outcome"] == "ok"
    assert props["fence_count"] == 0
    assert props["emit"] == "ir"
    assert props["skip_check"] is True
    assert "checker" not in props
    blob = json.dumps(captured)
    assert _SECRET not in blob and str(tmp_path) not in blob and "prog" not in blob.replace("pyscriptc", "")


def test_compile_fences_event_has_no_identifiers(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text(_FENCED_SRC)
    rc = _run_cli(["compile", str(src), "--skip-check", "--emit", "ir"])
    capsys.readouterr()
    assert rc == 3
    (event,) = _events(captured, "compile.finished")
    props = event["properties"]
    assert props["outcome"] == "fences"
    assert props["fence_count"] >= 1
    assert sum(props["fence_families"].values()) == props["fence_count"]
    assert not any(k.startswith("stdlib:") for k in props["fence_families"])
    blob = json.dumps(captured)
    assert "zz_private_mod" not in blob and str(tmp_path) not in blob


def test_compile_error_outcome(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "bad.py"
    src.write_text("def (:\n")
    with pytest.raises(SyntaxError):
        _run_cli(["compile", str(src), "--skip-check", "--emit", "ir"])
    (event,) = _events(captured, "compile.finished")
    assert event["properties"]["outcome"] == "lower-error"


def test_coverage_event(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text(_FENCED_SRC)
    assert _run_cli(["coverage", str(src)]) == 0
    capsys.readouterr()
    (event,) = _events(captured, "coverage.reported")
    props = event["properties"]
    assert props["definitions"] >= 1
    blob = json.dumps(captured)
    assert "zz_private_mod" not in blob and str(tmp_path) not in blob


@pytest.mark.parametrize("argv_kind", ["compile", "coverage"])
def test_new_events_respect_opt_out(captured, monkeypatch, tmp_path, capsys, argv_kind):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    src = tmp_path / "prog.py"
    src.write_text(_OK_SRC)
    argv = ["compile", str(src), "--skip-check", "--emit", "ir"] if argv_kind == "compile" else ["coverage", str(src)]
    _run_cli(argv)
    capsys.readouterr()
    assert captured == []


def test_notice_never_when_not_a_tty(captured, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setattr(telemetry.sys.stderr, "isatty", lambda: False, raising=False)
    telemetry.maybe_print_notice()
    assert capsys.readouterr().err == ""
    assert not (tmp_path / "pyscriptc" / "config.toml").exists()


@pytest.mark.parametrize("envar", ["PYSC_NO_TELEMETRY", "DO_NOT_TRACK", "CI"])
def test_notice_never_when_disabled(captured, monkeypatch, capsys, envar):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    _tty(monkeypatch)
    monkeypatch.setenv(envar, "1")
    telemetry.maybe_print_notice()
    assert capsys.readouterr().err == ""


def test_notice_never_when_config_disabled(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    _tty(monkeypatch)
    telemetry.telemetry_cmd("disable")
    capsys.readouterr()
    telemetry.maybe_print_notice()
    assert capsys.readouterr().err == ""


def test_no_event_before_notice(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setattr(telemetry, "_notice_shown_now", False)
    # Non-interactive first run: no notice, so no event.
    monkeypatch.setattr(telemetry.sys.stderr, "isatty", lambda: False, raising=False)
    telemetry.maybe_print_notice()
    telemetry.track("cli.invoked", {})
    assert captured == []
    # Interactive run: notice first, then events flow.
    _tty(monkeypatch)
    telemetry.maybe_print_notice()
    assert "PYSC_NO_TELEMETRY=1" in capsys.readouterr().err
    telemetry.track("cli.invoked", {})
    assert len(captured) == 1
    # A later non-interactive process sees notice_seen in the config.
    monkeypatch.setattr(telemetry, "_notice_shown_now", False)
    telemetry.track("cli.invoked", {})
    assert len(captured) == 2


def test_telemetry_subcommand_sends_nothing(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    assert _run_cli(["telemetry", "status"]) == 0
    assert captured == []


# --- release version, internal flag and delivery ---------------------------------------


def test_version_matches_installed_metadata(monkeypatch, tmp_path):
    """The event version is pinned to the installed package metadata, never a hardcoded literal."""
    from importlib.metadata import version as metadata_version

    import pyscriptc

    assert pyscriptc.__version__ == metadata_version("pyscriptc")
    assert pyscriptc.__version__ != "0.1.0"
    # the workflow stamp wins when present (the release binary has no metadata)
    stub = type(sys)("pyscriptc._release")
    stub.VERSION = "9.9.9"
    monkeypatch.setitem(sys.modules, "pyscriptc._release", stub)
    import importlib

    importlib.reload(pyscriptc)
    assert pyscriptc.__version__ == "9.9.9"
    del sys.modules["pyscriptc._release"]
    importlib.reload(pyscriptc)
    assert pyscriptc.__version__ == metadata_version("pyscriptc")
    props = telemetry.base_properties()
    assert props["pyscriptc_version"] == pyscriptc.__version__


def test_internal_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("CI", "PYSC_INTERNAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(telemetry.sys, "path", ["/usr/lib/python3.14", "/some/venv/lib"], raising=False)
    assert telemetry.internal() is False
    monkeypatch.setenv("PYSC_INTERNAL", "1")
    assert telemetry.internal() is True
    monkeypatch.delenv("PYSC_INTERNAL")
    monkeypatch.setenv("CI", "true")
    assert telemetry.internal() is True
    monkeypatch.delenv("CI")
    monkeypatch.setattr(
        telemetry.sys, "path", ["/repo/src/pyscriptc", "/x/__editable__.pyscriptc.finder.py"], raising=False
    )
    assert telemetry.internal() is True


def test_internal_blocks_sending_even_with_a_token(captured, monkeypatch):
    """Our own smoke and dev traffic must never send, token or not."""
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setenv("PYSC_INTERNAL", "1")
    telemetry.track("cli.invoked", {})
    assert captured == []
    assert telemetry.base_properties()["internal"] is True


def test_base_properties_carry_internal_false(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    assert telemetry.base_properties()["internal"] is False


def test_events_flush_before_exit(captured, monkeypatch, tmp_path):
    """A command that queues events and returns must deliver them (flush in cli.main)."""
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setattr(telemetry.sys, "path", ["/usr/lib/python3.14"], raising=False)
    from pyscriptc.cli import main

    rc = main(["explain", "PS2002"])
    assert rc == 0
    telemetry.flush()
    events = [p["event"] for p in captured]
    assert events == ["cli.invoked"]

    # flush is bounded: a hung post must not block longer than the budget.
    def slow(request, timeout=None):
        import time

        time.sleep(5)

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", slow)
    telemetry._pending.clear()
    telemetry.track("cli.invoked", {})
    import time

    started = time.monotonic()
    telemetry.flush()
    assert time.monotonic() - started < telemetry._FLUSH_BUDGET_S + 0.5


def test_coverage_command_sends_exactly_two_events(captured, monkeypatch, tmp_path):
    """coverage: cli.invoked plus coverage.reported, delivered before exit."""
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text("def f(x: int) -> int:\n    return x + 1\n")
    from pyscriptc.cli import main

    rc = main(["coverage", str(src)])
    assert rc == 0
    names = [p["event"] for p in captured]
    assert names.count("cli.invoked") == 1
    assert names.count("coverage.reported") == 1
    reported = [p for p in captured if p["event"] == "coverage.reported"][0]
    assert isinstance(reported["properties"]["fences"], int)
