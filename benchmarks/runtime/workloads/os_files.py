"""os.makedirs, open/write, stat, listdir, and getenv."""

import io
import os
import posixpath


def bench(n: int) -> int:
    root: str = "/tmp/pyscriptc-bench-os"
    if not posixpath.isdir(root):
        os.makedirs(root)
    path: str = "/tmp/pyscriptc-bench-os/note.txt"
    total: int = 0
    i: int = 0
    while i < n:
        handle: io.StringIO = open(path, "w")  # type: ignore[assignment]
        wrote: int = handle.write("hi")
        handle.close()
        info: os.stat_result = os.stat(path)
        total = total + wrote + info.st_size
        i = i + 1
    names: list[str] = os.listdir(root)
    env_path: str | None = os.getenv("PATH")
    return total + len(names) + len(env_path or "")
