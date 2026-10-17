"""Structured audit logging: a tiny structlog-shaped JSON-lines layer.

Zero-dependency by design (self-host + PyPI slimness). `get_logger()` returns
a logger with `.debug/.info/.warning/.error(event, **kv)` writing one JSON
record per line to `~/.cache/pyscriptc/log/YYYYMMDD.jsonl` (or
`$PYSC_LOG_DIR/YYYYMMDD.jsonl`). Records carry `ts`, `level`, `event`,
`pysc`, `py`, `pid` plus the caller's key-values.

Level is INFO by default; DEBUG when `PYSC_DEBUG=1` or the CLI `--debug`
flag. Files rotate by day and are capped near 10 MiB: on overflow the
oldest lines are dropped. Paths are fine to log; never log source
contents. Writes never raise — logging must not break a compile.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import sys
from pathlib import Path

from pyscriptc import __version__

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_MAX_BYTES = 10 * 1024 * 1024
_debug = os.environ.get("PYSC_DEBUG") == "1"


def enable_debug() -> None:
    """Turn on DEBUG records for this process (the CLI `--debug` flag)."""
    global _debug
    _debug = True


def log_dir() -> Path:
    override = os.environ.get("PYSC_LOG_DIR")
    return Path(override) if override else Path.home() / ".cache" / "pyscriptc" / "log"


def log_path(now: datetime.date | None = None) -> Path:
    day = (now or datetime.date.today()).strftime("%Y%m%d")
    return log_dir() / f"{day}.jsonl"


def _min_level() -> int:
    if _debug or os.environ.get("PYSC_DEBUG") == "1":
        return _LEVELS["debug"]
    return _LEVELS["info"]


def _cap(path: Path) -> None:
    try:
        if path.stat().st_size <= _MAX_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        keep: list[str] = []
        size = 0
        for line in reversed(lines):
            size += len(line.encode("utf-8", errors="replace"))
            if size > _MAX_BYTES:
                break
            keep.append(line)
        path.write_text("".join(reversed(keep)), encoding="utf-8")
    except OSError:
        pass


def _emit(level: str, event: str, kv: dict[str, object]) -> None:
    if _LEVELS[level] < _min_level():
        return
    record = {
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
        "pysc": __version__,
        "py": platform.python_version(),
        "pid": os.getpid(),
        **kv,
    }
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        _cap(path)
    except OSError:
        pass


class Logger:
    """Structlog-shaped API: `log.info("compile.start", source=..., fences=...)`."""

    def debug(self, event: str, **kv: object) -> None:
        _emit("debug", event, kv)

    def info(self, event: str, **kv: object) -> None:
        _emit("info", event, kv)

    def warning(self, event: str, **kv: object) -> None:
        _emit("warning", event, kv)

    def error(self, event: str, **kv: object) -> None:
        _emit("error", event, kv)


def get_logger() -> Logger:
    return Logger()


def tail(lines: int = 50, out: object = None) -> Path:
    """Print the audit-file path and its last `lines` lines. Returns the path."""
    stream = out if out is not None else sys.stdout
    path = log_path()
    print(path, file=stream)
    if not path.exists():
        print("(no records yet)", file=stream)
        return path
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"(unreadable: {exc})", file=stream)
        return path
    for line in content[-lines:]:
        print(line, file=stream)
    return path
