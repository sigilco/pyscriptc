# The closed `selectors` subset (issue #485). The compiled surface takes a
# bare int fd where CPython takes a `fileobj`: the typed `socket` handle is
# #229, so the fd-carrying form is the int one and `SelectorKey.fileobj` and
# `.data` stay outside the subset (divergence 29). `register` and
# `unregister` return the key, `select` returns the ready keys (not the
# `(key, events)` pairs CPython returns), and the selector classes other
# than `DefaultSelector` are outside the table.
from typing import Final

EVENT_READ: Final = 1
EVENT_WRITE: Final = 2

class SelectorKey:
    fd: int
    events: int

class DefaultSelector:
    def register(self, fd: int, events: int) -> SelectorKey: ...
    def unregister(self, fd: int) -> SelectorKey: ...
    def modify(self, fd: int, events: int) -> SelectorKey: ...
    def select(self, timeout: float | None = None) -> list[SelectorKey]: ...
    def get_key(self, fd: int) -> SelectorKey | None: ...
    def close(self) -> None: ...
