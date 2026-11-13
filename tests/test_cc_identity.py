"""Persisted compiler identity: miss, hit, stale toolchain, opt-out, concurrency.

The sidecar in the rt cache dir answers the `cc --version` probe after the
first compile, keyed by resolved binary path + (mtime, size). A fake cc script
counts its own invocations so a hit is visible without timing.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from pyscriptc import rtcache


@pytest.fixture
def fake_cc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(tmp_path / "rt"))
    monkeypatch.delenv("PYSC_NO_RT_CACHE", raising=False)
    monkeypatch.delenv("PYSC_NO_CC_ID_CACHE", raising=False)
    script = tmp_path / "fakecc"
    _write_cc(script, tmp_path / "calls", "fake-cc version 1")
    rtcache.compiler_identity.cache_clear()
    yield script
    rtcache.compiler_identity.cache_clear()


def _write_cc(script: Path, counter: Path, version: str) -> None:
    script.write_text(f'#!/bin/sh\necho call >> "{counter}"\necho "{version}"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _calls(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


def test_miss_persists_then_hit(fake_cc: Path, tmp_path: Path) -> None:
    cc = (str(fake_cc),)
    first = rtcache.compiler_identity(cc)
    assert "fake-cc version 1" in first
    sidecars = list((tmp_path / "rt").glob("ccid-*"))
    assert len(sidecars) == 1
    assert _calls(tmp_path / "calls") == 1
    # The next process reads the sidecar: the probe does not run again.
    rtcache.compiler_identity.cache_clear()
    assert rtcache.compiler_identity(cc) == first
    assert _calls(tmp_path / "calls") == 1


def test_stale_toolchain_reprobes(fake_cc: Path, tmp_path: Path) -> None:
    cc = (str(fake_cc),)
    assert "version 1" in rtcache.compiler_identity(cc)
    # A swapped toolchain keeps the path but changes content and mtime.
    stale = fake_cc.stat().st_mtime_ns
    _write_cc(fake_cc, tmp_path / "calls", "fake-cc version 2")
    os.utime(fake_cc, ns=(stale, stale + 1_000_000_000))
    rtcache.compiler_identity.cache_clear()
    second = rtcache.compiler_identity(cc)
    assert "version 2" in second
    assert _calls(tmp_path / "calls") == 2
    # ...and the refreshed answer persists again.
    rtcache.compiler_identity.cache_clear()
    assert rtcache.compiler_identity(cc) == second
    assert _calls(tmp_path / "calls") == 2


def test_opt_out_probes_every_time(fake_cc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_NO_CC_ID_CACHE", "1")
    cc = (str(fake_cc),)
    rtcache.compiler_identity(cc)
    rtcache.compiler_identity.cache_clear()
    rtcache.compiler_identity(cc)
    assert _calls(tmp_path / "calls") == 2
    rt = tmp_path / "rt"
    assert not rt.exists() or not list(rt.glob("ccid-*"))


def test_argv_variants_get_own_sidecars(fake_cc: Path, tmp_path: Path) -> None:
    assert rtcache.compiler_identity((str(fake_cc),)) == rtcache.compiler_identity((str(fake_cc),))
    other = rtcache.compiler_identity((str(fake_cc), "-m64"))
    assert " ".join((str(fake_cc), "-m64")) in other
    assert len(list((tmp_path / "rt").glob("ccid-*"))) == 2
    assert _calls(tmp_path / "calls") == 2


def test_unresolvable_cc_still_probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(tmp_path / "rt"))
    identity = rtcache.compiler_identity(("no-such-cc-v6c",))
    assert identity.startswith("unknown:")
    assert not (tmp_path / "rt").exists()


def test_concurrent_first_compiles(fake_cc: Path, tmp_path: Path) -> None:
    cc = (str(fake_cc),)
    results: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            results.append(rtcache.compiler_identity(cc))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(set(results)) == 1
    sidecars = list((tmp_path / "rt").glob("ccid-*"))
    assert len(sidecars) == 1
    # Whatever raced, the file on disk is a complete record.
    head, marker, identity = sidecars[0].read_text(encoding="utf-8").partition("\n---\n")
    assert marker and identity == results[0]
    assert len(head.split("\n")) == 3
    assert not list((tmp_path / "rt").glob(".ccid-*"))
