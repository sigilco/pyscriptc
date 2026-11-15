# Issue #1245: `os.pipe` hands back the (read, write) pair in CPython's
# order. The fd numbers differ between the two runs, so the probe prints
# booleans about the pair and byte-compares the payload that travels
# through it: a write on the second fd reads back on the first.
import os

r, w = os.pipe()
print(r > 0)
print(w > 0)
print(r != w)
print(os.write(w, b"pipe bytes") == 10)
print(os.read(r, 10) == b"pipe bytes")
os.close(r)
os.close(w)

# dup: the copy writes through to the same pipe; a distinct fd number.
r, w = os.pipe()
d: int = os.dup(w)
print(d != w)
print(os.write(d, b"dup bytes") == 9)
print(os.read(r, 9) == b"dup bytes")
os.close(d)
os.close(r)
os.close(w)

# dup2: the copy lands on the given descriptor and the result is it.
r, w = os.pipe()
d: int = os.dup(w)
os.close(d)
print(os.dup2(w, d) == d)
print(os.write(d, b"dup2 bytes") == 10)
print(os.read(r, 10) == b"dup2 bytes")
os.close(d)
os.close(r)
os.close(w)
