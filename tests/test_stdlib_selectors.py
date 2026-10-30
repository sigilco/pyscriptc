"""selectors: the closed subset over the #177 event loop (issue #485).

The compiled surface is the int-fd form: `register(fd, events)` takes the
descriptor number where CPython takes a `fileobj` (the typed `socket` handle
is #229), and `select()` returns the ready keys where CPython returns the
`(key, events)` pairs. The differential probes therefore compare the facts
both sides agree on (the key fields, the counts, the key identity); the two
divergent shapes are asserted on the compiled side alone.

The probes use a FIFO, because the epoll backend refuses a regular file and
a pipe is the smallest fd a program can wait on.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    """The compiled output must equal the CPython oracle's output."""
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def _compiled(tmp_path: Path, name: str, source: str, func: str = "run") -> str:
    """The compiled output, for the shapes CPython does not have."""
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    return got.stdout


def _fifo(tmp_path: Path, name: str = "fifo") -> str:
    """A FIFO path: the epoll backend watches pipes, not regular files."""
    path = tmp_path / name
    os.mkfifo(path)
    return str(path)


def test_selectors_register_returns_the_key(tmp_path: Path) -> None:
    """register hands back the key, with the fd and the event bits."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select1.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    key = sel.register(r, selectors.EVENT_READ)",
                "    print(key.fd == r, key.events == selectors.EVENT_READ)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_get_key_finds_the_registration(tmp_path: Path) -> None:
    """get_key finds the registered fd and reports the same events."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select2.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    found = sel.get_key(r)",
                "    if found is not None:",
                "        print(found.fd == r, found.events == selectors.EVENT_READ)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_modify_keeps_the_key(tmp_path: Path) -> None:
    """modify changes the interest and keeps the key identity."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select3.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    key = sel.register(r, selectors.EVENT_READ)",
                "    both = selectors.EVENT_READ | selectors.EVENT_WRITE",
                "    changed = sel.modify(r, both)",
                "    print(changed.fd == r, changed.events == both)",
                "    again = sel.get_key(r)",
                "    if again is not None:",
                "        print(again.events == both)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_unregister_returns_the_key(tmp_path: Path) -> None:
    """unregister hands back the key it dropped."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select4.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    out = sel.unregister(r)",
                "    print(out.fd == r, out.events == selectors.EVENT_READ)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_empty_poll_is_empty(tmp_path: Path) -> None:
    """A poll with nothing ready is an empty list, and a timeout waits."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select5.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    print(len(sel.select(0.0)))",
                "    print(len(sel.select(0.05)))",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_ready_count_matches_cpython(tmp_path: Path) -> None:
    """The number of ready keys is the number CPython reports."""
    fifo = _fifo(tmp_path)
    _oracle(
        tmp_path,
        "select6.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    w: int = os.open(path, os.O_WRONLY | os.O_NONBLOCK)",
                "    os.write(w, b'x')",
                "    print(len(sel.select(1.0)))",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_two_fds_one_ready(tmp_path: Path) -> None:
    """Only the ready fd comes back from a select."""
    fifo = _fifo(tmp_path)
    other = tmp_path / "other"
    os.mkfifo(other)
    _oracle(
        tmp_path,
        "select7.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                f"    other: str = {str(other)!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    q: int = os.open(other, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    sel.register(q, selectors.EVENT_READ)",
                "    w: int = os.open(other, os.O_WRONLY | os.O_NONBLOCK)",
                "    os.write(w, b'x')",
                "    print(len(sel.select(1.0)))",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_selectors_select_returns_the_key_records(tmp_path: Path) -> None:
    """The compiled select returns the key records, not CPython's pairs.

    CPython returns `list[tuple[SelectorKey, int]]`; the subset returns
    `list[SelectorKey]`, the key carrying the fd and the events. The
    divergence is recorded in docs/reference/divergences.md (29).
    """
    fifo = _fifo(tmp_path)
    out = _compiled(
        tmp_path,
        "select8.py",
        "\n".join(
            [
                "import os",
                "import selectors",
                "",
                "def run() -> int:",
                f"    path: str = {fifo!r}",
                "    r: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK)",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    sel.register(r, selectors.EVENT_READ)",
                "    w: int = os.open(path, os.O_WRONLY | os.O_NONBLOCK)",
                "    os.write(w, b'x')",
                "    ready = sel.select(1.0)",
                "    print(len(ready), ready[0].fd == r, ready[0].events == selectors.EVENT_READ)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )
    assert out == "1 True True\n0\n"


def test_selectors_get_key_of_an_unregistered_fd_is_none(tmp_path: Path) -> None:
    """The compiled get_key boxes None; CPython raises KeyError."""
    out = _compiled(
        tmp_path,
        "select9.py",
        "\n".join(
            [
                "import selectors",
                "",
                "def run() -> int:",
                "    sel: selectors.DefaultSelector = selectors.DefaultSelector()",
                "    print(sel.get_key(4242) is None)",
                "    sel.close()",
                "    return 0",
                "",
            ]
        ),
    )
    assert out == "True\n0\n"


def test_selectors_fences_stay_closed(tmp_path: Path) -> None:
    """The names outside the subset fence."""
    path = write_case(
        tmp_path,
        "fence.py",
        "\n".join(
            [
                "import selectors",
                "",
                "def run() -> int:",
                "    sel = selectors.DefaultSelector()",
                "    sel.register(3, selectors.EVENT_READ, None)",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "bad-argument-count" in got.stderr, got.stderr


def test_selectors_fileobj_form_fences_with_issue_229(tmp_path: Path) -> None:
    """CPython's register takes a fileobj; the int form is what lands until #229."""
    path = write_case(
        tmp_path,
        "fileobj.py",
        "\n".join(
            [
                "import selectors",
                "",
                "class Sock:",
                "    def fileno(self) -> int:",
                "        return 3",
                "",
                "def run() -> int:",
                "    sel = selectors.DefaultSelector()",
                "    sel.register(Sock(), selectors.EVENT_READ)  # type: ignore[arg-type]",
                "    return 1",
                "",
                "def run() -> int:",
                "    sel = selectors.DefaultSelector()",
                "    sel.register(Sock(), selectors.EVENT_READ)  # type: ignore[arg-type]",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS1003" in got.stderr or "bad-argument-type" in got.stderr, got.stderr
    assert "fileobj" in got.stderr, got.stderr
    assert "#229" in got.stderr, got.stderr
