import struct

data: bytes = b"\x01\x02"
head: list[int] = struct.unpack_from("<I", data)
print(head[0])
