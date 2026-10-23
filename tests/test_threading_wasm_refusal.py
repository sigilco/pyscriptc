"""The WASM/WASI thread refusal: thread programs do not cross-compile to wasm32-wasi.

The runtime's thread primitives are pthreads (`ps_thread.c`). A wasm target
has no working `pthread_create`: wasi-libc stubs it to return `EAGAIN` and
its `pthread_cond_wait` traps. The link would succeed and the program would
fail at run time with a libc error that names neither the target nor the
reason. The refusal fires at link time, before the sysroot is touched, and
names both (issue #172).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import run_cli

from pyscriptc.emit.llvm._driver import link_binary, refuse_unsupported_target
from pyscriptc.errors import IRError


def test_refusal_names_target_and_symbols():
    text = "declare void @ps_thread_start2() #0\n"
    with pytest.raises(IRError) as err:
        refuse_unsupported_target(text, "wasm32-wasi")
    message = str(err.value)
    assert "wasm32-wasi" in message
    assert "ps_thread_start2" in message
    assert "issue #172" in message


def test_refusal_lists_only_used_symbols():
    text = "declare void @ps_queue_new(i64, i64, i64) #0\n"
    with pytest.raises(IRError) as err:
        refuse_unsupported_target(text, "wasm32-wasi")
    assert "ps_queue_new" in str(err.value)
    assert "ps_thread_start2" not in str(err.value)


def test_native_target_passes_thread_text():
    text = "declare void @ps_thread_start2() #0\n"
    refuse_unsupported_target(text, None)
    refuse_unsupported_target(text, "x86_64-linux-gnu")
    refuse_unsupported_target(text, "aarch64-apple-darwin")


def test_wasm_without_thread_symbols_passes():
    text = "define void @main() {\n  ret void\n}\n"
    refuse_unsupported_target(text, "wasm32-wasi")


def test_wasi_snapshot_symbols_do_not_trigger():
    # A wasi libc symbol can substring-collide: the check matches the runtime
    # symbol names as IR globals (`@name(`), not bare substrings.
    text = "declare void @not_ps_thread_start2_fake() #0\n"
    refuse_unsupported_target(text, "wasm32-wasi")


def _thread_ir() -> str:
    return "declare ptr @ps_thread_new(ptr, i64, ptr)\ndeclare void @ps_thread_start2(ptr)\n"


def test_link_binary_refuses_threads_on_wasm_target(tmp_path: Path) -> None:
    ll = tmp_path / "thr.ll"
    ll.write_text(_thread_ir() + "define void @main() {\n  ret void\n}\n", encoding="utf-8")
    with pytest.raises(IRError, match="wasm32-wasi.*no working pthreads"):
        link_binary(ll, tmp_path / "thr", driver="zig", target="wasm32-wasi")


def test_link_binary_native_target_still_links_threads(tmp_path: Path) -> None:
    # The gate reads the IR before any cc call: a native target with the same
    # IR reaches the link, where the runtime supplies the thread symbols.
    ll = tmp_path / "thr.ll"
    ll.write_text(_thread_ir() + "define void @main() {\n  ret void\n}\n", encoding="utf-8")
    out = tmp_path / "thr"
    link_binary(ll, out, driver="clang")
    assert out.is_file()


_THREAD_SOURCE = """
import threading

def work(n: int) -> None:
    print(n)

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=work, args=(41,))
    t.start()
    t.join()
    return 0
"""

_LOCK_SOURCE = """
import threading

def checklist() -> int:
    lock: threading.Lock = threading.Lock()
    ok: bool = lock.acquire()
    lock.release()
    return 0 if ok else 1
"""


def test_cli_refuses_a_thread_program_for_wasm(tmp_path: Path) -> None:
    # The refusal fires right after the emit, so `--emit llvm` reaches it
    # without a C driver on PATH. The diagnostic names the target and the
    # runtime symbols.
    source = tmp_path / "thr.py"
    source.write_text(_THREAD_SOURCE, encoding="utf-8")
    proc = run_cli(
        "compile",
        str(source),
        "--target",
        "wasm32-wasi",
        "--driver",
        "zig",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "thr.ll"),
    )
    assert proc.returncode != 0
    assert "wasm32-wasi" in proc.stderr
    assert "no working pthreads" in proc.stderr
    assert "ps_thread_new" in proc.stderr


def test_cli_allows_a_lock_only_program_for_wasm(tmp_path: Path) -> None:
    # A lock needs no second thread, so the refusal does not fire for it. The
    # link itself is the wasm branch's business, so this only checks that the
    # gate let the program through.
    source = tmp_path / "lock.py"
    source.write_text(_LOCK_SOURCE, encoding="utf-8")
    proc = run_cli(
        "compile",
        str(source),
        "--target",
        "wasm32-wasi",
        "--driver",
        "zig",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "lock.ll"),
    )
    assert "no working pthreads" not in proc.stderr
