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
        binary_bytes=1234,
    )
    for key in ("pyscriptc_version", "python_version", "platform", "ci"):
        assert key in props
    assert props["outcome"] == "fences"
    assert props["fence_count"] == 3
    assert props["fence_families"] == {"union-types": 2, "stdlib": 1}
    assert props["binary_bytes"] == 1234
    assert props["skip_check"] is False
    bare = telemetry.compile_properties(duration_ms=1, outcome="ok", fence_count=0, fence_families={})
    assert "checker" not in bare and "driver" not in bare and "binary_bytes" not in bare


def test_coverage_properties_aggregates_and_collapses_stdlib():
    summary = {
        "files": 2,
        "definitions": 4,
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
    assert props["byFamily"] == {"union-types": 1, "stdlib": 2}
    assert props["coverage_ratio"] == 0.25
    assert "byCode" not in props
    assert "zz_private_mod" not in json.dumps(props)
    assert telemetry.coverage_properties({})["coverage_ratio"] == 0.0


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
    assert 0.0 <= props["coverage_ratio"] <= 1.0
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
