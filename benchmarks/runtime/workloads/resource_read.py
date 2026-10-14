"""importlib.resources.files read of one header. The path is resolved without libpython."""

import importlib.resources


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        text: str = (
            importlib.resources.files("pyscriptc").joinpath("runtime").joinpath("ps_rt.h").read_text(encoding="ascii")
        )
        total = total + len(text)
        i = i + 1
    return total
