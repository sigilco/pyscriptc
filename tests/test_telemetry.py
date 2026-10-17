"""Tests for src/pyscriptc/telemetry.py. No network: urlopen is mocked."""

from __future__ import annotations

import json

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

    for name in ("CI", "PYSC_NO_TELEMETRY", "DO_NOT_TRACK", "PYSC_POSTHOG_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
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
    assert len(first["distinct_id"]) == 64  # sha256 hex
    assert first["distinct_id"] == second["distinct_id"]
    # No PII-ish fields anywhere in the payload.
    blob = json.dumps(first)
    for banned in ("hostname", "machine-id", "path", "file", "source", "user"):
        assert banned not in blob


def test_distinct_id_stable_across_calls(captured):
    assert telemetry._machine_id() == telemetry._machine_id()


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


def test_notice_prints_exactly_once(captured, monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    telemetry.maybe_print_notice()
    err = capsys.readouterr().err
    assert "anonymous usage metrics" in err
    assert err.count("\n") == 1
    telemetry.maybe_print_notice()
    assert capsys.readouterr().err == ""


def test_notice_survives_config_roundtrip(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
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
