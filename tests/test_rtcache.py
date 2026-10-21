"""Runtime object cache: hit, invalidation, concurrency, bypass."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from pyscriptc import rtcache
from pyscriptc.llvm import link_binary

HELLO_LL = Path(__file__).parent / "fixtures" / "hello.ll"

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "rt"
    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(root))
    monkeypatch.delenv("PYSC_NO_RT_CACHE", raising=False)
    return root


def _ll(tmp_path: Path) -> Path:
    ll = tmp_path / "m.ll"
    ll.write_text(
        "define i32 @main() {\n  ret i32 0\n}\n",
        encoding="utf-8",
    )
    return ll


def _link(tmp_path: Path, name: str = "a") -> list[list[str]]:
    record: list[list[str]] = []
    link_binary(_ll(tmp_path), tmp_path / name, record=record)
    return record


def test_cold_then_warm_hit(tmp_path, cache):
    cold = _link(tmp_path)
    assert "miss=0" not in cold[0][1]
    objs = sorted(cache.glob("*.o"))
    assert objs and not list(cache.glob("*.tmp"))
    stamps = {o: o.stat().st_mtime_ns for o in objs}
    warm = _link(tmp_path, "b")
    assert "miss=0" in warm[0][1]
    assert {o: o.stat().st_mtime_ns for o in cache.glob("*.o")} == stamps
    assert len(warm) < len(cold)


def test_invalidation_on_source_change(tmp_path, cache):
    runtime = Path(rtcache.__file__).parent / "runtime"
    src = runtime / "ps_argv.c"
    first = rtcache.object_path(src, runtime, ["clang"], ["-O2"])
    assert first == rtcache.object_path(src, runtime, ["clang"], ["-O2"])
    assert first != rtcache.object_path(src, runtime, ["clang"], ["-O0"])
    assert first != rtcache.object_path(src, runtime, ["clang"], ["-O2", "-target", "x"])
    copy = tmp_path / "rt2"
    shutil.copytree(runtime, copy)
    same = rtcache.object_path(copy / "ps_argv.c", copy, ["clang"], ["-O2"])
    assert same == first
    (copy / "ps_argv.c").write_text((copy / "ps_argv.c").read_text() + "\n/* x */\n")
    assert rtcache.object_path(copy / "ps_argv.c", copy, ["clang"], ["-O2"]) != first
    other = tmp_path / "rt3"
    shutil.copytree(runtime, other)
    (other / "ps_rt.h").write_text((other / "ps_rt.h").read_text() + "\n/* x */\n")
    assert rtcache.object_path(other / "ps_argv.c", other, ["clang"], ["-O2"]) != first


def test_bypass(tmp_path, cache, monkeypatch):
    monkeypatch.setenv("PYSC_NO_RT_CACHE", "1")
    record = _link(tmp_path)
    assert record[0][1].startswith("rtcache off")
    assert not cache.exists() or not list(cache.glob("*.o"))
    assert (tmp_path / "a").exists()


def test_concurrent_links(tmp_path, cache):
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            d = tmp_path / f"w{i}"
            d.mkdir()
            _link(d)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert not list(cache.glob("*.tmp"))
    assert all(o.stat().st_size > 0 for o in cache.glob("*.o"))


def test_stats(cache):
    assert rtcache.stats()["enabled"] is True
    assert rtcache.stats()["dir"] == str(cache)
