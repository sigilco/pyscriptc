"""Telemetry v2: the PSxxxx histogram, binary size and the run event.

No network: urlopen is mocked, same as tests/test_telemetry.py.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from pyscriptc import telemetry

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

_FENCED_SRC = "import zz_private_mod\n\n\ndef f(x: int) -> int:\n    return zz_private_mod.go(x)\n"
_OK_SRC = "def f(x: int) -> int:\n    return x + 1\n"


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
        sent.append(json.loads(request.data.decode()))
        return _Resp()

    for name in ("CI", "PYSC_NO_TELEMETRY", "DO_NOT_TRACK", "PYSC_POSTHOG_TOKEN", "PYSC_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(telemetry, "_notice_shown_now", True)
    monkeypatch.setattr(telemetry, "threading", SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)
    return sent


class _SyncThread:
    """Drop-in for threading.Thread that runs the target synchronously."""

    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self._target = target
        self._args = args

    def start(self):
        if self._target:
            self._target(*self._args)


def _events(captured, name):
    return [p for p in captured if p["event"] == name]


def _run_cli(argv):
    from pyscriptc.cli import main

    return main(argv)


def test_compile_properties_carry_the_code_histogram():
    props = telemetry.compile_properties(
        duration_ms=5,
        outcome="fences",
        fence_count=3,
        fence_families={"union-types": 2, "stdlib": 1},
        fence_codes={"PS1101": 2, "PS2002": 1},
    )
    assert props["fence_codes"] == {"PS1101": 2, "PS2002": 1}
    assert sum(props["fence_codes"].values()) == props["fence_count"]
    # No histogram at all is still a valid payload.
    assert (
        telemetry.compile_properties(duration_ms=1, outcome="ok", fence_count=0, fence_families={})["fence_codes"] == {}
    )


def test_compile_properties_carry_the_binary_size():
    props = telemetry.compile_properties(
        duration_ms=5,
        outcome="ok",
        fence_count=0,
        fence_families={},
        binary_bytes=4096,
    )
    assert props["binary_bytes"] == 4096
    # No binary means no field, not a zero.
    assert "binary_bytes" not in telemetry.compile_properties(
        duration_ms=1, outcome="ok", fence_count=0, fence_families={}
    )


def test_run_properties_schema():
    props = telemetry.run_properties(
        duration_ms=7,
        exit_code=0,
        cache_hit=True,
        binary_bytes=1234,
        driver="clang",
    )
    assert props["exit_code"] == 0
    assert props["cache_hit"] is True
    assert props["binary_bytes"] == 1234
    assert props["driver"] == "clang"
    assert "target" not in props


def test_compile_fences_event_carries_the_code_histogram(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text(_FENCED_SRC)
    rc = _run_cli(["compile", str(src), "--skip-check", "--emit", "ir"])
    capsys.readouterr()
    assert rc == 3
    (event,) = _events(captured, "compile.finished")
    props = event["properties"]
    assert props["fence_codes"]
    assert sum(props["fence_codes"].values()) == props["fence_count"]
    assert all(code.startswith("PS") for code in props["fence_codes"])
    blob = json.dumps(captured)
    assert "zz_private_mod" not in blob and str(tmp_path) not in blob


def test_compile_binary_event_carries_the_binary_size(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text(_OK_SRC)
    binary = tmp_path / "prog.bin"
    rc = _run_cli(["compile", str(src), "--skip-check", "--emit", "bin", "-o", str(binary)])
    capsys.readouterr()
    assert rc == 0
    (event,) = _events(captured, "compile.finished")
    assert event["properties"]["binary_bytes"] == binary.stat().st_size


@needs_clang
def test_run_finished_event(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text("def main() -> int:\n    return 0\n\nmain()\n")
    rc = _run_cli(["run", str(src), "--skip-check"])
    capsys.readouterr()
    assert rc == 0
    (event,) = _events(captured, "run.finished")
    props = event["properties"]
    assert props["exit_code"] == 0
    assert props["cache_hit"] is False
    assert props["binary_bytes"] > 0
    blob = json.dumps(captured)
    assert str(tmp_path) not in blob and "prog" not in blob.replace("pyscriptc", "")


@needs_clang
def test_run_finished_event_on_a_cache_hit(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    src = tmp_path / "prog.py"
    src.write_text("def main() -> int:\n    return 0\n\nmain()\n")
    assert _run_cli(["run", str(src), "--skip-check"]) == 0
    assert _run_cli(["run", str(src), "--skip-check"]) == 0
    capsys.readouterr()
    events = _events(captured, "run.finished")
    assert len(events) == 2
    assert events[0]["properties"]["cache_hit"] is False
    assert events[1]["properties"]["cache_hit"] is True


def test_v2_events_respect_the_opt_out(captured, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(telemetry, "_BAKED_TOKEN", "phc_test")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    src = tmp_path / "prog.py"
    src.write_text(_OK_SRC)
    _run_cli(["compile", str(src), "--skip-check", "--emit", "ir"])
    capsys.readouterr()
    assert captured == []
