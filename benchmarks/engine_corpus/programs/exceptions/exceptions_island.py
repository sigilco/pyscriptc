"""The exceptions corpus program: the island module the compiler routes to the engine.

The exception path is the one the engine seam changed for PyPy (the
`args` attribute fetch instead of `PyException_GetArgs`), so this program
covers a caught exception, a chained one and an exception that crosses the
seam uncaught.
"""

from typing import NoReturn


def caught() -> str:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return "caught:" + str(exc)


def chained() -> str:
    try:
        try:
            raise KeyError("k")
        except KeyError as inner:
            raise ValueError("outer") from inner
    except ValueError as exc:
        return "chained:" + str(exc.__cause__)


def custom() -> str:
    class Marker(Exception):
        pass

    try:
        raise Marker("m")
    except Marker as exc:
        return "custom:" + str(exc)


def escapes() -> NoReturn:
    raise RuntimeError("escapes the seam")
