# Issue #1416: a bytes slice or index counts bytes, not code points. The
# probe runs on the pinned CPython oracle and on the compiled program: the
# stdout, stderr and exit code must match byte for byte. The payloads cover
# a multi-byte sequence (the seam that split under the code-point bounds),
# an embedded NUL and a high-byte-only buffer.

data: bytes = b"caf\xc3\xa9 xyz"
nul: bytes = b"a\x00b\xff"
high: bytes = b"\x80\x81\xff\xfe"

print(len(data), len(nul), len(high))

# The issue's slice: it crosses the 0xc3 0xa9 pair and must not split it.
part = data[2:4]
print(len(part), part[0], part[1])

print(data[3], data[-1], data[7], data[-9])
print(data[2:8], data[:5], data[5:], data[:])
print(data[2:-2], data[-4:-2])

print(data[2:8:2], data[::3], data[1:6:2])
print(data[::-1], data[6:2:-1], data[::-2])

print(nul[1], nul[0:2], len(nul[1:]), nul[-1])
print(high[1], high[1:3], high[::-1], len(high[::2]))

print(data[8:2], data[100:200], data[-100:2], data[5:5])
print(data[3:1:-1], data[-1:-6:-2], data[100::2], data[::-100])

first: int = data[0]
last: int = nul[3]
print(first + last)
