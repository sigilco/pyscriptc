import struct


def main() -> None:
    data: bytes = struct.pack("<IHB", 7, 513, 9)
    print(struct.calcsize("<IHB"))
    print(struct.calcsize(">2I3s"))
    print(struct.calcsize("!qd"))
    print(struct.calcsize("<"))
    head: list[int] = struct.unpack_from("<IHB", data)
    print(head[0], head[1], head[2])
    pad: bytes = b"\x00\x00" + data + b"\xff"
    shifted: list[int] = struct.unpack_from("<IHB", pad, 2)
    print(shifted[0], shifted[1], shifted[2])
    tail: list[int] = struct.unpack_from("<H", pad, -3)
    print(tail[0])
    n: int = struct.calcsize("<IH")
    two: list[int] = struct.unpack_from("<IH", pad, n - 6 + 2)
    print(two[0], two[1])


main()
