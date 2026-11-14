"""Differential probe for the streaming hashlib surface (issue #1248).

The program hashes 10 MiB in 64 KiB chunks plus the boundary sizes around the
64-byte (md5/sha1/sha256) and 128-byte (sha384/sha512) block lines, and
byte-compares the hex digests against the pinned CPython 3.14. The one-shot
form hashes the same total through `hashlib.<alg>(all)` so the two paths must
agree with each other and with CPython.

Lane contract: the runner compiles this file with the current compiler, runs
it next to the pinned CPython and byte-compares stdout, stderr and exit code.
"""

import hashlib


def main() -> None:
    chunk: bytes = b"K" * 65536
    total: bytes = b"K" * 1048576

    h256 = hashlib.sha256()
    for _i in range(16):
        h256.update(chunk)
    print(h256.hexdigest())
    print(hashlib.sha256(total).hexdigest())

    h512 = hashlib.sha512()
    h512.update(chunk)
    h512.update(chunk)
    print(h512.hexdigest())
    print(hashlib.sha512(chunk + chunk).hexdigest())

    h1 = hashlib.md5()
    h1.update(b"ab")
    h1.update(b"cd")
    print(h1.hexdigest())
    print(hashlib.md5(b"abcd").hexdigest())

    h384 = hashlib.sha384()
    for _i in range(4):
        h384.update(chunk)
    print(h384.hexdigest())
    print(hashlib.sha384(b"K" * 262144).hexdigest())

    # Block-boundary chunk sizes: 55/56/57/63/64/65 bytes per update for
    # sha256, and 111/112/113/127/128/129 for sha512.
    sizes: list[int] = [55, 56, 57, 63, 64, 65]
    for s in sizes:
        piece: bytes = b"x" * s
        acc = hashlib.sha256()
        acc.update(piece)
        acc.update(piece)
        print(s, acc.hexdigest())
        print(s, hashlib.sha256(piece + piece).hexdigest())

    big_sizes: list[int] = [111, 112, 113, 127, 128, 129]
    for s in big_sizes:
        piece: bytes = b"y" * s
        acc = hashlib.sha512()
        acc.update(piece)
        acc.update(piece)
        print(s, acc.hexdigest())
        print(s, hashlib.sha512(piece + piece).hexdigest())

    # digest() rows: the raw bytes, the frozen second read and the empty hash.
    h2 = hashlib.sha256()
    h2.update(b"zz")
    first: bytes = h2.digest()
    second: bytes = h2.digest()
    print(first == second)
    print(h2.hexdigest())
    empty = hashlib.sha256()
    print(empty.hexdigest())
    print(empty.digest() == h2.digest())


main()
