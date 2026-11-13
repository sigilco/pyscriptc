"""The bytes corpus program: the island module the compiler routes to the engine.

Bytes cross the seam as an opaque buffer in both directions: the island
copies them out of the CPython object, the native side copies them back in.
The program exercises the empty buffer, the embedded NUL and a non-ASCII
payload, which are the three shapes a length-prefixed copy can get wrong.
"""


def echo(data: bytes) -> bytes:
    return data


def length(data: bytes) -> int:
    return len(data)


def joined(a: bytes, b: bytes) -> bytes:
    return a + b


def first_byte(data: bytes) -> int:
    return data[0]
