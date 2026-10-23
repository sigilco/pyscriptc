# Vendored stub for the lowered jwt surface (the PyJWT `jwt` package).
# The pack lowers encode and decode for HS256, HS384 and HS512 over a
# dict[str, str] payload (issue #61, the closed subset in
# runtime/ps_jwt.c); every other PyJWT name stays fenced at the call site.
from typing import Any

__all__ = ["encode", "decode"]

def encode(payload: dict[str, str], key: str, algorithm: str = ...) -> str: ...
def decode(token: str, key: str, algorithms: list[str], **kwargs: Any) -> dict[str, str]: ...
