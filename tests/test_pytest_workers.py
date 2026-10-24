from scripts import pytest_workers as pw


def _set(monkeypatch, cpus, load, mem):
    monkeypatch.delenv("PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(pw, "cpus", lambda: cpus)
    monkeypatch.setattr(pw, "load", lambda: load)
    monkeypatch.setattr(pw, "mem_gb", lambda: mem)


def test_idle_machine_uses_the_cap(monkeypatch):
    _set(monkeypatch, 8, 0.5, 8.0)
    assert pw.workers() == pw.CAP


def test_busy_machine_shrinks_to_the_floor(monkeypatch):
    _set(monkeypatch, 8, 13.0, 8.0)
    assert pw.workers() == pw.MIN_WORKERS


def test_partial_load_gives_the_remaining_cpus(monkeypatch):
    _set(monkeypatch, 8, 4.2, 8.0)
    assert pw.workers() == 3


def test_memory_pressure_overrides_the_floor(monkeypatch):
    _set(monkeypatch, 8, 13.0, 0.3)
    assert pw.workers() == 1


def test_override_wins(monkeypatch):
    _set(monkeypatch, 8, 13.0, 0.3)
    monkeypatch.setenv("PYTEST_WORKERS", "5")
    assert pw.workers() == 5
