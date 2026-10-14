"""Build one probe from one candidate call and check it against the CPython oracle.

Every execution happens in a forked child with a timeout, so a crash, an exit, or a stray
side effect in the stdlib cannot take the generator down or leak state into the next probe.
"""

from __future__ import annotations

import ast
import json
import os
import select
import signal
import time
from dataclasses import dataclass, field

from .fold import Desc, describe, fold_lines, shape_lines

TIMEOUT = 2.5


def run_forked(job, timeout: float = TIMEOUT) -> tuple[str, object]:  # noqa: ANN001
    """Run `job()` in a child. Returns ("ok", json value) or ("exc", type name) or ("timeout"|"crash", "")."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        status = 0
        try:
            os.close(read_fd)
            os.setpgid(0, 0)
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            signal.alarm(int(timeout) + 2)
            try:
                payload = ["ok", job()]
            except BaseException as exc:  # noqa: BLE001
                payload = ["exc", type(exc).__name__]
            os.write(write_fd, json.dumps(payload).encode())
        except BaseException:  # noqa: BLE001
            status = 1
        os._exit(status)
    os.close(write_fd)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([read_fd], [], [], left)
        if not ready:
            timed_out = True
            break
        data = os.read(read_fd, 65536)
        if not data:
            break
        chunks.append(data)
    os.close(read_fd)
    if timed_out:
        os.killpg(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    if timed_out:
        return "timeout", ""
    if not chunks:
        return "crash", ""
    try:
        status, value = json.loads(b"".join(chunks))
    except ValueError:
        return "crash", ""
    return status, value


def _exec_probe(source: str, args: list[object]) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source, "<probe>", "exec"), namespace)
    return namespace["probe"](*args)  # type: ignore[operator]


@dataclass
class Built:
    kind: str  # value | raises | fail
    source: str = ""
    args: list[object] = field(default_factory=list)
    expect_exc: str = ""
    fold: str = ""  # which fold produced the int, for the manifest
    reason: str = ""
    ann: str = ""  # annotation of the folded result, used for receivers


def _body(setup: list[str], lines: list[str]) -> str:
    return "\n".join("    " + line if line else "" for line in [*setup, *lines])


def _signature(params: list[tuple[str, str]], ret: str) -> str:
    return f"def probe({', '.join(f'{n}: {t}' for n, t in params)}) -> {ret}:"


def build(
    header: list[str],
    params: list[tuple[str, str]],
    args: list[object],
    setup: list[str],
    expr: str,
    receiver: str = "",
    receiver_ann: str = "",
) -> Built:
    """`expr` is evaluated after `setup`. With `receiver`, a None result folds the receiver instead."""
    head = "\n".join(header) + ("\n\n" if header else "")
    probe_obj = head + _signature(params, "object") + "\n"
    probe_obj += _body(setup, [f"r = {expr}", f"return (r, {receiver or 'None'})"])
    status, info = run_forked(lambda: _describe_job(probe_obj, args, bool(receiver)))
    if status == "exc":
        return Built("fail", reason=str(info), fold="raises")
    if status != "ok":
        return Built("fail", reason=status)
    desc = Desc(*info["desc"])  # type: ignore[index]
    big = bool(info["big"])  # type: ignore[index]
    lines: list[str]
    if desc.fold == "none":
        recv = info["recv"]  # type: ignore[index]
        if receiver and recv and recv[0]:
            rdesc = Desc(*recv)
            lines = [f"{expr}", *fold_lines(rdesc, receiver, 2**62 if info["recv_big"] else 0)]  # type: ignore[index]
            return _finalize(head, params, args, setup, lines, rdesc, f"recv-{rdesc.fold}", receiver)
        lines = [f"{expr}", "return 1"]
        return _finalize(head, params, args, setup, lines, desc, "none")
    if not desc.ann:
        return _finalize(head, params, args, setup, [f"{expr}", "return 1"], desc, "exec")
    lines = [f"r: {desc.ann} = {expr}", *fold_lines(desc, "r", 2**62 if big else 0)]
    return _finalize(head, params, args, setup, lines, desc, desc.fold, "r")


def _describe_job(source: str, args: list[object], has_receiver: bool) -> object:
    result = _exec_probe(source, args)
    value, receiver = result  # type: ignore[misc]
    desc = describe(value)
    recv = describe(receiver) if has_receiver else None
    return {
        "desc": [desc.ann, desc.fold],
        "big": isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 2**62,
        "recv": [recv.ann, recv.fold] if recv else None,
        "recv_big": isinstance(receiver, int) and abs(receiver) >= 2**62,
    }


def _finalize(
    head: str,
    params: list[tuple[str, str]],
    args: list[object],
    setup: list[str],
    lines: list[str],
    desc: Desc,
    fold: str,
    var: str = "r",
) -> Built:
    source = head + _signature(params, "int") + "\n" + _body(setup, lines) + "\n"
    first, second = (run_forked(lambda: _exec_probe(source, args)) for _ in range(2))
    if first[0] == "ok" and first == second and isinstance(first[1], int):
        return Built("value", source, args, fold=fold, ann=desc.ann)
    if first[0] != "ok":
        return Built("fail", reason=f"final-{first[0]}:{first[1]}")
    # nondeterministic across two forks: keep only the type check
    shaped = [lines[0], *shape_lines(desc, var)] if lines[0].startswith(f"{var}:") else lines
    source = head + _signature(params, "int") + "\n" + _body(setup, shaped) + "\n"
    first, second = (run_forked(lambda: _exec_probe(source, args)) for _ in range(2))
    if first[0] == "ok" and first == second and isinstance(first[1], int):
        return Built("value", source, args, fold="shape", ann=desc.ann)
    return Built("fail", reason="nondeterministic")


def raises_probe(
    header: list[str], params: list[tuple[str, str]], args: list[object], setup: list[str], expr: str, exc: str
) -> Built:
    head = "\n".join(header) + ("\n\n" if header else "")
    source = head + _signature(params, "int") + "\n" + _body(setup, [expr, "return 1"]) + "\n"
    status, value = run_forked(lambda: _exec_probe(source, args))
    if status == "exc" and value == exc:
        return Built("raises", source, args, expect_exc=exc, fold="raises")
    return Built("fail", reason=f"raises-recheck:{status}:{value}")


def scalar_params(positional: list[str], keywords: dict[str, str], start: int = 0):
    """Lift int/float/bool/str literals into probe parameters. Returns (params, args, call_text)."""
    params: list[tuple[str, str]] = []
    args: list[object] = []

    def lift(text: str) -> str:
        try:
            value = ast.literal_eval(text)
        except ValueError, SyntaxError:
            return text
        if type(value) in (int, float, bool, str):
            name = f"p{start + len(params)}"
            params.append((name, type(value).__name__))
            args.append(value)
            return name
        return text

    parts = [lift(t) for t in positional] + [f"{k}={lift(v)}" for k, v in keywords.items()]
    return params, args, ", ".join(parts)
