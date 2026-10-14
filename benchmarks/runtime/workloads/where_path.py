"""certifi.where: one as_file lookup stored in a global, then reused."""

import atexit
from importlib.resources import as_file, files

_CTX = None
_PATH = None


def _release() -> None:
    _CTX.__exit__(None, None, None)  # type: ignore[union-attr]


def bench(n: int) -> int:
    global _CTX
    global _PATH
    total: int = 0
    i: int = 0
    while i < n:
        if _PATH is None:
            _CTX = as_file(files("pyscriptc").joinpath("runtime").joinpath("ps_rt.h"))
            _PATH = str(_CTX.__enter__())
            atexit.register(_release)
        total = total + len(_PATH)
        i = i + 1
    return total
