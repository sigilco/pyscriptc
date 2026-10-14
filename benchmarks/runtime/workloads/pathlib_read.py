"""pathlib.Path write_text, read_text, name, suffix, and parent."""

import pathlib


def bench(n: int) -> int:
    path: pathlib.Path = pathlib.Path("/tmp/pyscriptc-bench-path.txt")
    total: int = 0
    i: int = 0
    while i < n:
        wrote: int = path.write_text("hi")
        body: str = path.read_text()
        parent: pathlib.Path = path.parent
        total = total + wrote + len(body) + len(path.name) + len(path.suffix) + len(parent.as_posix())
        i = i + 1
    return total
