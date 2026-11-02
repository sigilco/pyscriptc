"""Streaming canonical serializer over the dict JSON IR (TIR gate bootstrap).

Byte-identical to the canonical payload `pyscriptc.canonical.hash_module`
digests today, namely

    json.dumps({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)},
               sort_keys=True, separators=(",", ":"), ensure_ascii=False,
               allow_nan=False).encode("utf-8")

but the serialization walks the canonical structure once and feeds SHA-256
directly, so the full payload string is never materialized. This is the
serializer the TIR_HASHES gate consumes (spec: the "Proposed" section of
.agents/docs/spec/ir-canonicity.md); `canonical.py` itself is frozen and the
old IR_HASHES gate keeps reading it until the recorded retirement decision.

Byte identity is enforced per probe by `benchmarks.compat2.tirscore
--verify-bytes`, which `cmp`s the streaming bytes against the json.dumps
bytes on the whole visible corpus.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION, canonicalize

# json.dumps short escapes for the C0 controls it does not write as \uXXXX.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}

_HEX = "0123456789abcdef"


class Serializer:
    """Feeds one canonical payload into a sink (hashlib or bytearray).

    The output is exactly the json.dumps payload described in the module
    docstring, produced incrementally: object keys are emitted in Python
    string sort order, strings are UTF-8 with json's ensure_ascii=False
    escaping, floats must be finite (allow_nan=False semantics).
    """

    __slots__ = ("_sink",)

    def __init__(self, sink: Any) -> None:
        self._sink = sink

    def _write(self, chunk: bytes) -> None:
        self._sink(chunk)

    def write_any(self, value: Any) -> None:
        if value is None:
            self._write(b"null")
        elif value is True:
            self._write(b"true")
        elif value is False:
            self._write(b"false")
        elif isinstance(value, str):
            self.write_str(value)
        elif isinstance(value, int):
            self._write(str(value).encode("ascii"))
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(
                    "a non-finite float reached the canonical IR; "
                    "json.dumps(allow_nan=False) would refuse it too"
                )
            self._write(repr(value).encode("ascii"))
        elif isinstance(value, dict):
            self.write_object(value)
        elif isinstance(value, (list, tuple)):
            self.write_array(value)
        else:
            raise TypeError(f"the canonical IR carries a {type(value).__name__}; json.dumps would refuse it")

    def write_object(self, obj: dict[str, Any]) -> None:
        self._write(b"{")
        first = True
        for key in sorted(obj):
            if not first:
                self._write(b",")
            first = False
            self.write_str(key)
            self._write(b":")
            self.write_any(obj[key])
        self._write(b"}")

    def write_array(self, items: Any) -> None:
        self._write(b"[")
        first = True
        for item in items:
            if not first:
                self._write(b",")
            first = False
            self.write_any(item)
        self._write(b"]")

    def write_str(self, text: str) -> None:
        self._write(b'"')
        start = 0
        for index, char in enumerate(text):
            code = ord(char)
            if code == 0x22:
                escape = '\\"'
            elif code == 0x5C:
                escape = "\\\\"
            elif code < 0x20:
                escape = _SHORT_ESCAPES.get(code)
                if escape is None:
                    # json's \\u%04x form, lower case hex digits.
                    escape = "\\u" + _HEX[code >> 12] + _HEX[(code >> 8) & 0xF] + _HEX[(code >> 4) & 0xF] + _HEX[code & 0xF]
            else:
                continue
            if start < index:
                self._write(text[start:index].encode("utf-8"))
            self._write(escape.encode("ascii"))
            start = index + 1
        if start < len(text):
            self._write(text[start:].encode("utf-8"))
        self._write(b'"')


def payload_bytes(module: dict[str, Any]) -> bytes:
    """The canonical payload bytes, built incrementally into one buffer.

    Byte-identical to the json.dumps payload hash_module digests; used by
    the byte-identity verifier and by tooling that wants the stream itself.
    """
    chunks: list[bytes] = []
    stream = Serializer(chunks.append)
    stream.write_object({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)})
    return b"".join(chunks)


def hash_module(module: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical payload, streamed; never materializes it.

    Same digest contract as pyscriptc.canonical.hash_module at the same
    CANONICAL_SCHEMA_VERSION; `tirscore --verify-bytes` proves the bytes
    are identical on the corpus.
    """
    digest = hashlib.sha256()
    stream = Serializer(digest.update)
    stream.write_object({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)})
    return digest.hexdigest()
