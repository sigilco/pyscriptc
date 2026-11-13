"""The closed selectors subset over the #177 event loop (issue #485).

The compiled surface takes a bare int fd where CPython takes a fileobj
(the typed socket handle is #229), and it cannot create a FIFO or a pipe
(`os.mkfifo` and `os.pipe` are outside the lowered `os` subset), so the
watch scenario over a real pipe lives in tests/test_stdlib_selectors.py,
where the harness creates the FIFO and both sides run the same program
and compare the ready count against the pinned CPython.

This probe covers the rest of the surface, the part both sides run
byte-for-byte: the event constants and the shape of the key through
register, get_key, modify and unregister. The CPython-shaped
divergences (`select()` returns keys, not pairs; `get_key` of an unknown
fd boxes `None` instead of raising `KeyError`) are recorded in divergence
29 and are not probed here.
"""

import os
import selectors

path: str = "selectors_probe_tmp.txt"
with open(path, "w") as handle:
    handle.write("x")

fd: int = os.open(path, os.O_RDONLY)
os.unlink(path)

sel: selectors.DefaultSelector = selectors.DefaultSelector()
key: selectors.SelectorKey = sel.register(fd, selectors.EVENT_READ)
print(key.fd == fd, key.events == selectors.EVENT_READ)

found: selectors.SelectorKey | None = sel.get_key(fd)
print(found is not None, found is not None and found.fd == fd)

changed: selectors.SelectorKey = sel.modify(fd, selectors.EVENT_READ | selectors.EVENT_WRITE)
print(changed.fd == fd, changed.events == selectors.EVENT_READ | selectors.EVENT_WRITE)

dropped: selectors.SelectorKey = sel.unregister(fd)
print(dropped.fd == fd, dropped.events == selectors.EVENT_READ)

sel.close()
os.close(fd)
