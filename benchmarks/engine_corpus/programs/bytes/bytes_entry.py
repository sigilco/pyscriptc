"""The typed entry over the bytes island module."""

import bytes_island


def main() -> None:
    a: bytes = bytes_island.echo(b"")
    b: bytes = bytes_island.echo(b"\x00\x01\x02")
    c: bytes = bytes_island.echo("café".encode())
    d: int = bytes_island.length(b"\x00\x01\x02")
    e: bytes = bytes_island.joined(b"ab", b"cd")
    f: int = bytes_island.first_byte(b"\x07rest")
    print(len(a))
    print(len(b))
    print(len(c))
    print(d)
    print(len(e))
    print(f)


if __name__ == "__main__":
    main()
