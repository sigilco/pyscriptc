"""Fingerprint a two-integer record after a base64 round trip.

`fingerprint` packs the integers little-endian, checks that base64
encode-then-decode reproduces those bytes, and returns the SHA-256 hex
digest. A mismatch returns an empty string. `--args` cannot pass `bytes`,
so the bytes are built inside the function.
"""

import base64
import hashlib
import struct


def fingerprint(left: int, right: int) -> str:
    packed: bytes = struct.pack("<ii", left, right)
    wrapped: bytes = base64.b64encode(packed)
    back: bytes = base64.b64decode(wrapped)
    if back != packed:
        return ""
    return hashlib.sha256(packed).hexdigest()
